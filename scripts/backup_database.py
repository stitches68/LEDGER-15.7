"""Transaction-safe SQLite backup. Run: python scripts/backup_database.py"""
from datetime import datetime,timezone
from pathlib import Path
import os,sqlite3,sys
ROOT=Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
except ImportError:
    pass

path=Path(os.getenv('DATABASE_PATH','ledger.sqlite3'))
if not path.is_absolute():
    path=ROOT/path
if not path.is_file():
    sys.exit(f'Database not found at {path}; no backup created.')
folder=ROOT/'backups'
folder.mkdir(exist_ok=True)
out=folder/f'ledger-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-UTC.sqlite3'
with sqlite3.connect(str(path)) as source,sqlite3.connect(str(out)) as dest:
    source.backup(dest)
    result=dest.execute('PRAGMA integrity_check').fetchone()[0]
if result!='ok':
    out.unlink(missing_ok=True)
    sys.exit(f'Backup failed SQLite integrity check: {result}')
print(f'Ledger database backed up to {out}')
print('Keep backups private; they contain server financial and member records.')
