"""Consistent SQLite backup, including committed WAL transactions."""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from app.config import DATABASE_PATH

if __name__ == '__main__':
    folder=Path('backups'); folder.mkdir(exist_ok=True)
    target=folder/('akim-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.sqlite3')
    if not Path(DATABASE_PATH).exists():
        raise SystemExit('Database does not exist yet.')
    with sqlite3.connect(DATABASE_PATH) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    print(target)
