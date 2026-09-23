"""Accounts, opaque sessions, per-user workspaces. No plaintext passwords in SQLite."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from fastapi import HTTPException
from . import db

COOKIE = 'akim_session'

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()
    return salt + ':' + digest

def verify(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)

def init_accounts():
    with db.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces(user_id INTEGER PRIMARY KEY REFERENCES users(id), payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS user_results(id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, summary_json TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS analysis_cache(cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY, failures INTEGER NOT NULL, started REAL NOT NULL);
        """)
        c.execute('INSERT OR IGNORE INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)', ('admin','Администратор',password_hash(os.getenv('INITIAL_ADMIN_PASSWORD','admin')),'admin',db.now()))
        columns = {r['name'] for r in c.execute('PRAGMA table_info(scenarios)')}
        if 'owner' not in columns:
            c.execute("ALTER TABLE scenarios ADD COLUMN owner TEXT NOT NULL DEFAULT 'admin'")

def public_user(row):
    return {k:row[k] for k in ['id','username','display_name','role','active']}

def session_user(raw):
    if not raw:return None
    with db.connect() as c:
        row=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1',(hashlib.sha256(raw.encode()).hexdigest(),time.time())).fetchone()
    return public_user(row) if row else None

def login(username,password,source):
    username=username.strip().lower()
    key=hashlib.sha256((source+':'+username).encode()).hexdigest()
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        attempt=c.execute('SELECT * FROM login_attempts WHERE key=?',(key,)).fetchone()
        if attempt and attempt['started']>time.time()-900 and attempt['failures']>=10:
            raise HTTPException(429,'Слишком много попыток. Повторите через 15 минут.')
        row=c.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
        valid=verify(password,row['password_hash']) if row else verify(password,password_hash('unavailable'))
        if not row or not valid:
            if not attempt or attempt['started']<=time.time()-900:
                c.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,1,?)',(key,time.time()))
            else:c.execute('UPDATE login_attempts SET failures=failures+1 WHERE key=?',(key,))
        else:
            c.execute('DELETE FROM login_attempts WHERE key=?',(key,))
            c.execute('DELETE FROM sessions WHERE expires_at<?',(time.time(),))
            raw=secrets.token_urlsafe(32)
            c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(raw.encode()).hexdigest(),row['id'],time.time()+604800))
            db.audit(c,'user:'+str(row['id']),'login','account')
            return raw, public_user(row)
    raise HTTPException(401,'Неверный логин или пароль.')

def logout(raw):
    with db.connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256((raw or '').encode()).hexdigest(),))

def create_user(username,password,display_name,role,actor):
    username=username.strip().lower()
    if not re.fullmatch(r'[a-z0-9_.-]{3,40}',username):raise ValueError('Логин: 3–40 латинских букв, цифр, точек, дефисов.')
    if not 8<=len(password)<=200:raise ValueError('Пароль сотрудника: 8–200 символов.')
    if role not in ['employee','admin']:raise ValueError('Неизвестная роль.')
    with db.connect() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():raise db.ConflictError('Логин уже занят.')
        cur=c.execute('INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)',(username,display_name,password_hash(password),role,db.now()))
        db.audit(c,actor,'create_user',str(cur.lastrowid),after={'username':username,'role':role})
    return {'id':cur.lastrowid,'username':username,'role':role}

def workspace(user_id):
    with db.connect() as c:row=c.execute('SELECT * FROM workspaces WHERE user_id=?',(user_id,)).fetchone()
    return {'state':json.loads(row['payload']) if row else {},'revision':row['revision'] if row else 0}

def save_workspace(user_id,state,revision):
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT revision FROM workspaces WHERE user_id=?',(user_id,)).fetchone()
        current=row['revision'] if row else 0
        if current!=revision:raise db.ConflictError('Черновик изменён в другой вкладке. Перезагрузите страницу перед продолжением.')
        c.execute('INSERT INTO workspaces VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at',(user_id,db.dump(state),current+1,db.now()))
    return {'revision':current+1}
