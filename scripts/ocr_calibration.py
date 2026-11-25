"""Inspect what offline Tesseract reads from a local HD2 results screenshot.

Usage: python scripts/ocr_calibration.py path/to/screenshot.png
This tool does not upload anything and does not write to the bot database.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PIL import Image
from squad_ocr import extract_squad_layout, player_assignment_slots


def main() -> int:
    if len(sys.argv)!=2:
        print('Usage: python scripts/ocr_calibration.py path/to/HD2-statistics.png')
        return 2
    path=Path(sys.argv[1])
    if not path.is_file():
        print('Screenshot not found:',path)
        return 2
    try:
        with Image.open(path) as img:
            players, count=extract_squad_layout(img)
    except Exception as exc:
        print(f'Unable to read screenshot: {type(exc).__name__}: {exc}')
        return 1
    print(f'LEDGER Blacksite OCR • {count} detected player cards • {len(players)} readable')
    if not count:
        print('No supported 2-, 3-, or 4-player results layout found. Try full-resolution, uncropped screenshot.')
        return 1
    for entry in player_assignment_slots(players,count):
        info=entry['stats']
        fields=', '.join(f'{name}={value}' for name,value in sorted(info.items())) or 'unreadable'
        print(f'Card {entry["column"]}: {fields}')
    print('OCR values are estimates from screenshot pixels, not verified gameplay data.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
