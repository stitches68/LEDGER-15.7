"""Local diagnostics without exposing tokens. Run: python scripts/doctor.py"""
from pathlib import Path
import os
import sys
import shutil
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
except ImportError:
    pass

print('LEDGER / LOCAL DOCTOR')
print('Bot token present:', bool(os.getenv('DISCORD_TOKEN')))
print('Discord server ID present:', bool(os.getenv('TEST_GUILD_ID')))
print('Tesseract executable:', os.getenv('TESSERACT_CMD') or shutil.which('tesseract') or 'not found in PATH')
print('Database target:', os.getenv('DATABASE_PATH','ledger.sqlite3'))
print('Assets folder present:', (ROOT/'assets').is_dir())
print('Blacksite emoji present:', (ROOT/'assets'/'blacksite_credits_emoji.png').is_file())
try:
    from ocr_verifier import ocr_engine_status
    ready,message=ocr_engine_status()
    print('OCR check:', message)
except Exception as exc:
    print('OCR check could not run:',type(exc).__name__)
print('A running Discord bot still needs separately verified permissions and a real test screenshot.')
