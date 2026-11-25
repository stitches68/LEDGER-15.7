"""Validate Railway persistent storage before starting LEDGER.

A missing volume would silently reset SQLite balances on every redeploy;
fail safely instead. On a local computer, this launcher is optional.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


def validate_configuration(env: dict[str, str] | None = None) -> str:
    e = env if env is not None else os.environ
    database = e.get('DATABASE_PATH', '/data/ledger.sqlite3')
    if not e.get('RAILWAY_PROJECT_ID'):
        return database

    mount = e.get('RAILWAY_VOLUME_MOUNT_PATH', '').strip()
    if not mount:
        raise RuntimeError(
            'Railway volume missing. Attach a persistent volume at /data '
            'BEFORE enabling LEDGER, and set DATABASE_PATH=/data/ledger.sqlite3.'
        )
    mount_path = Path(mount).resolve()
    db_path = Path(database)
    if not db_path.is_absolute():
        raise RuntimeError(
            'DATABASE_PATH must be an absolute path on Railway, '
            'such as /data/ledger.sqlite3.'
        )
    resolved_db = db_path.resolve()
    if resolved_db == mount_path or not resolved_db.is_relative_to(mount_path):
        raise RuntimeError(
            'DATABASE_PATH must point INSIDE the persistent Railway volume. '
            f'Current mount: {mount}; database: {database}.'
        )
    if not mount_path.is_dir():
        raise RuntimeError(f'Railway volume path is missing: {mount}.')
    return database


if __name__ == '__main__':
    try:
        database = validate_configuration()
    except RuntimeError as exc:
        print(f'LEDGER CONFIGURATION ERROR: {exc}', file=sys.stderr, flush=True)
        raise SystemExit(1) from None
    print(f'LEDGER database path: {database}', flush=True)
    print('Starting LEDGER with offline Tesseract OCR...', flush=True)
    os.execv(sys.executable,
             [sys.executable, '-u', str(Path(__file__).parent / 'bot.py')])
