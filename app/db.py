import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from . import config
from .model import validate_dataset, forecast
from .events import scenario_forecast

def now():
    return datetime.now(timezone.utc).isoformat()

def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)

class ConflictError(ValueError):
    pass

@contextmanager
def connect():
    path = Path(config.DATABASE_PATH); path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        with connection:
            yield connection
    finally:
        connection.close()

def audit(connection, actor, action, entity, before=None, after=None):
    connection.execute('INSERT INTO audit_log(created_at,actor,action,entity,before_json,after_json) VALUES(?,?,?,?,?,?)',
                       (now(), actor, action, entity, dump(before), dump(after)))

def init_db():
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS datasets (
          id INTEGER PRIMARY KEY AUTOINCREMENT, data_json TEXT NOT NULL,
          created_at TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scenarios (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, dataset_version INTEGER NOT NULL REFERENCES datasets(id),
          plan_json TEXT NOT NULL, forecast_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT NOT NULL,
          action TEXT NOT NULL, entity TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_runs (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, actor TEXT NOT NULL,
          prompt TEXT NOT NULL, answer TEXT NOT NULL, actions_json TEXT NOT NULL, status TEXT NOT NULL);
        ''')
        if not c.execute("SELECT value FROM state WHERE key='active_dataset'").fetchone():
            data = json.loads((config.ROOT / 'seed/dataset.json').read_text(encoding='utf-8'))
            scale = config.env_int('TENGE_PER_UNIT', 100000000)
            old = data['tenge_per_unit']
            data['budget_tenge'] = data['budget_tenge'] // old * scale
            for m in data['measures']:
                m['cost_tenge'] = m['cost_tenge'] // old * scale
            data['tenge_per_unit'] = scale
            validate_dataset(data)
            cursor = c.execute('INSERT INTO datasets(data_json,created_at,actor,note) VALUES(?,?,?,?)', (dump(data), now(), 'system', 'Исходный датасет хакатона'))
            c.execute("INSERT INTO state(key,value) VALUES('active_dataset',?)", (str(cursor.lastrowid),))
        c.execute("INSERT OR IGNORE INTO state(key,value) VALUES('site_settings',?)", (dump({'revision': 1, 'title': 'Аким на 5 часов', 'subtitle': 'Пять решений для будущего города', 'map_note': 'Пять районов учебного кейса. Метки условные, не административные границы.', 'forecast_note': 'Факт — исходные синтетические данные; прогноз — расчёт по эффектам модели.'}),))

def get_dataset(version=None):
    with connect() as c:
        if version is None:
            version = int(c.execute("SELECT value FROM state WHERE key='active_dataset'").fetchone()[0])
        row = c.execute('SELECT * FROM datasets WHERE id=?', (version,)).fetchone()
    if not row:
        raise KeyError('Версия датасета не найдена.')
    return {'version': row['id'], 'data': json.loads(row['data_json']), 'created_at': row['created_at'], 'note': row['note']}

def dataset_versions():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT id AS version,created_at,actor,note FROM datasets ORDER BY id DESC LIMIT 100')]

def replace_dataset(data, expected_version, actor, note='Редактирование данных'):
    validate_dataset(data)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        current = int(c.execute("SELECT value FROM state WHERE key='active_dataset'").fetchone()[0])
        if current != expected_version:
            raise ConflictError('Датасет уже изменён. Обновите данные и повторите правку.')
        cur = c.execute('INSERT INTO datasets(data_json,created_at,actor,note) VALUES(?,?,?,?)', (dump(data), now(), actor, note[:300]))
        c.execute("UPDATE state SET value=? WHERE key='active_dataset'", (str(cur.lastrowid),))
        audit(c, actor, 'replace_dataset', 'dataset', {'version': current}, {'version': cur.lastrowid, 'note': note})
        version = cur.lastrowid
    return get_dataset(version)

def edit_record(collection, record_id, patch, expected_version, actor, operation='update'):
    if not isinstance(patch, dict):
        raise ValueError('Изменения должны быть объектом.')
    if collection not in ['districts', 'measures', 'metrics', 'categories']:
        raise ValueError('Неизвестная таблица данных.')
    current = get_dataset()
    if current['version'] != expected_version:
        raise ConflictError('Версия устарела.')
    data = current['data']; items = data[collection]
    index = next((i for i, item in enumerate(items) if item['id'] == record_id), None)
    if operation == 'create':
        if index is not None:
            raise ValueError('Запись уже существует.')
        items.append({**patch, 'id': record_id})
    elif operation == 'delete':
        if index is None:
            raise KeyError('Запись не найдена.')
        items.pop(index)
    elif operation == 'update':
        if index is None:
            raise KeyError('Запись не найдена.')
        for key, value in patch.items():
            if key in ['effects', 'indicators'] and isinstance(value, dict):
                items[index][key] = {**items[index].get(key, {}), **value}
            else:
                items[index][key] = value
        if items[index]['id'] != record_id:
            raise ValueError('Идентификатор записи менять нельзя.')
    else:
        raise ValueError('Неизвестная операция.')
    return replace_dataset(data, expected_version, actor, f'{operation}: {collection}/{record_id}')

def get_settings():
    with connect() as c:
        return json.loads(c.execute("SELECT value FROM state WHERE key='site_settings'").fetchone()[0])

def update_settings(patch, expected_revision, actor):
    if not isinstance(patch, dict) or not set(patch) <= {'title', 'subtitle', 'map_note', 'forecast_note'} or not all(isinstance(v, str) and 1 <= len(v) <= 500 for v in patch.values()):
        raise ValueError('Неверные настройки сайта.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        before = json.loads(c.execute("SELECT value FROM state WHERE key='site_settings'").fetchone()[0])
        if before['revision'] != expected_revision:
            raise ConflictError('Настройки изменились. Обновите страницу.')
        after = {**before, **patch, 'revision': before['revision'] + 1}
        c.execute("UPDATE state SET value=? WHERE key='site_settings'", (dump(after),))
        audit(c, actor, 'update_settings', 'site', before, after)
    return after

def scenario_record(row):
    if not row:
        raise KeyError('Сценарий не найден.')
    d = dict(row); d['plan'] = json.loads(d.pop('plan_json')); d['forecast'] = json.loads(d.pop('forecast_json'))
    d['event_id'] = d['forecast'].get('event', {}).get('id', 'none')
    return d

def list_scenarios():
    with connect() as c:
        return [scenario_record(row) for row in c.execute('SELECT * FROM scenarios ORDER BY updated_at DESC LIMIT 100')]

def get_scenario(scenario_id):
    with connect() as c:
        return scenario_record(c.execute('SELECT * FROM scenarios WHERE id=?', (scenario_id,)).fetchone())

def save_scenario(name, plan, dataset_version, actor, scenario_id=None, expected_revision=None, event_id='none'):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise ValueError('Название сценария: 1–120 символов.')
    dataset = get_dataset(dataset_version)
    result = scenario_forecast(dataset['data'], plan, event_id=event_id)
    if not result['valid']:
        raise ValueError(' '.join(result['errors']))
    timestamp = now()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if scenario_id:
            before = scenario_record(c.execute('SELECT * FROM scenarios WHERE id=?', (scenario_id,)).fetchone())
            if before['revision'] != expected_revision:
                raise ConflictError('Сценарий уже изменён.')
            c.execute('UPDATE scenarios SET name=?,dataset_version=?,plan_json=?,forecast_json=?,revision=revision+1,updated_at=? WHERE id=?', (name.strip(), dataset_version, dump(plan), dump(result), timestamp, scenario_id))
        else:
            before = None; scenario_id = uuid.uuid4().hex
            c.execute('INSERT INTO scenarios(id,name,dataset_version,plan_json,forecast_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)', (scenario_id, name.strip(), dataset_version, dump(plan), dump(result), timestamp, timestamp))
        if before is None:
            c.execute('UPDATE scenarios SET owner=? WHERE id=?',(actor,scenario_id))
        after = scenario_record(c.execute('SELECT * FROM scenarios WHERE id=?', (scenario_id,)).fetchone())
        audit(c, actor, 'save_scenario', scenario_id, before, after)
    return after

def delete_scenario(scenario_id, expected_revision, actor):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        before = scenario_record(c.execute('SELECT * FROM scenarios WHERE id=?', (scenario_id,)).fetchone())
        if before['revision'] != expected_revision:
            raise ConflictError('Сценарий уже изменён.')
        c.execute('DELETE FROM scenarios WHERE id=?', (scenario_id,))
        audit(c, actor, 'delete_scenario', scenario_id, before, None)
    return {'deleted': scenario_id}

def list_audit():
    with connect() as c:
        return [dict(r) for r in c.execute('SELECT id,created_at,actor,action,entity FROM audit_log ORDER BY id DESC LIMIT 100')]

def save_agent_run(actor, prompt, answer, actions, status):
    run_id = uuid.uuid4().hex
    with connect() as c:
        c.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?,?)', (run_id, now(), actor, prompt, answer, dump(actions), status))
    return run_id
