"""Offline Helldivers 2 post-mission screenshot checks using Tesseract OCR.

No screenshots are sent to an API. Keep decisions conservative: OCR text is
never proof that the picture is genuine or a player participated in a mission.
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import re
import shutil

from PIL import Image, ImageOps, ImageEnhance, UnidentifiedImageError
from squad_ocr import extract_squad_layout


# Only a single OCR run is performed per uploaded image.
STAT_PATTERNS: dict[str, str] = {
    'kills': r'(?:enemy\s+)?kills?',
    'deaths': r'deaths?',
    'stims_used': r'stims?\s+(?:used|administered)',
    'shots_fired': r'shots?\s+fired',
    'shots_hit': r'shots?\s+(?:hit|landed)',
    'samples_collected': r'samples?\s+(?:extracted|collected)',
    'stratagems_used': r'strat[ae]?gems?\s+used',
}
OTHER_STAT_PATTERNS = (
    r'accuracy', r'stims?\s+used', r'accidentals?', r'melee\s+kills?',
    r'friendly\s+fire', r'distance\s+travell?ed', r'times?\s+reinforc(?:ing|ed)',
    r'samples?\s+extracted', r'strat[ae]?gems?',
)
RESULT_MARKERS = (
    r'\bmission\s+(?:completed|complete|failed|results?|summary)\b',
    r'\bsquad\s+impact\b',
    r'\bmission\s+stats?\b',
    r'\bpost\s+mission\b',
    r'\bstatistics\b',
    r'\bstats\b',
)
GAME_MARKERS = (
    r'\bstrat[ae]?gems?\b',
    r'\bhelldivers?\b',
    r'\bsuper\s+earth\b',
    r'\bsamples?\s+extracted\b',
    r'\bsquad\s+impact\b',
)


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _extract_int(lines: list[str], pattern: str) -> int | None:
    """Only read a single adjacent numeric value; never guess team/player totals.

    Screens with several player columns are intentionally left without numeric
    totals. They may still pass the *screen recognition* test.
    """
    matches = []
    for line in lines:
        line = re.sub(r'\s+', ' ', line).strip()
        # Fully labelled value e.g. "DEATHS: 5" or "5 DEATHS".
        after = re.fullmatch(rf'(?:{pattern})\s*[:\-]?\s*(\d{{1,6}}(?:,\d{{3}})*)', line, re.I)
        before = re.fullmatch(rf'(\d{{1,6}}(?:,\d{{3}})*)\s*[:\-]?\s*(?:{pattern})', line, re.I)
        m = after or before
        if m:
            matches.append(int(m.group(1).replace(',', '')))
    # Multiple player rows or competing values: don't attribute any figure.
    return matches[0] if len(matches) == 1 else None


def assess_ocr_text(raw: str) -> tuple[str, str, dict]:
    """Interpret OCR text without making any OpenAI calls.

    STRICT PASS: distinctive HD2 vocabulary + three result fields + a results
    context marker. Borderline/ambiguous screens are always sent for review.
    """
    if not isinstance(raw, str):
        return 'UNCERTAIN', 'OCR did not return readable text. Staff review required.', {}
    # Normalize spaces but keep lines for cautious field/value extraction.
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    text = re.sub(r'\s+', ' ', raw.lower())
    recognized = [key for key, pattern in STAT_PATTERNS.items() if _has(rf'\b(?:{pattern})\b', text)]
    auxiliary = [p for p in OTHER_STAT_PATTERNS if _has(rf'\b(?:{p})\b', text)]
    context = any(_has(p, text) for p in RESULT_MARKERS)
    distinctive = any(_has(p, text) for p in GAME_MARKERS)
    stats = {}
    for key in recognized:
        value = _extract_int(lines, STAT_PATTERNS[key])
        if value is not None:
            stats[key] = value
    if _has(r'\bmission\s+(?:completed|complete|successful)\b', text):
        stats['mission_success'] = True
    elif _has(r'\bmission\s+failed\b', text):
        stats['mission_success'] = False
    if _has(r'\b(?:successfully\s+extracted|extraction\s+successful)\b', text):
        stats['extracted'] = True
    elif _has(r'\b(?:failed\s+to\s+extract|extraction\s+failed)\b', text):
        stats['extracted'] = False
    difficulty = re.search(r'\bdifficulty\s*[:\-]?\s*(10|[1-9])\b', text)
    if difficulty:
        stats['difficulty_visible'] = int(difficulty.group(1))
    # No planet visible extraction unless explicitly labelled in the OCR text.
    planet = re.search(r'(?im)^\s*planet\s*[:\-]\s*([a-z][a-z\-\x27 ]{2,50})\s*$',raw)
    if planet:
        stats['planet_visible'] = planet.group(1).strip()

    if context and distinctive and len(recognized) >= 3 and len(auxiliary) >= 2:
        return ('PASS',
                f'Local OCR recognized a Helldivers-style mission results screen '
                f'with {len(recognized)} legible statistics labels. Screenshot authenticity is not verified.', stats)
    if not lines or len(re.sub(r'[^a-z0-9]','',text)) < 16:
        return 'UNCERTAIN', 'Could not reliably read the screenshot; sent for review.', stats
    if len(recognized) >= 2 or context or distinctive:
        return ('UNCERTAIN',
                'Some mission-result text was detected, but not enough to verify the screen automatically.', stats)
    # Text-rich unrelated screenshots are clearly not the requested results UI.
    return 'FAIL', 'OCR found no recognizable Helldivers 2 mission results interface.', {}


def _configure_tesseract() -> bool:
    """Support Windows installations and a custom TESSERACT_CMD path."""
    try:
        import pytesseract
    except ImportError:
        return False
    custom = os.getenv('TESSERACT_CMD', '').strip().strip('"')
    if custom:
        path = Path(custom)
        if not path.is_file():
            return False
        pytesseract.pytesseract.tesseract_cmd = str(path)
        return True
    cmd = shutil.which('tesseract')
    if not cmd and os.name == 'nt':
        for candidate in (r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                          r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'):
            if Path(candidate).is_file():
                cmd = candidate
                break
    if not cmd:
        return False
    pytesseract.pytesseract.tesseract_cmd = cmd
    return True


def ocr_engine_status() -> tuple[bool, str]:
    """Return a staff-safe status line without exposing environment secrets."""
    if not _configure_tesseract():
        return False, ('Tesseract not found. Install the Windows Tesseract OCR engine, '
                       'then set TESSERACT_CMD in .env if auto-detection fails.')
    try:
        import pytesseract
        version = str(pytesseract.get_tesseract_version()).splitlines()[0][:80]
        return True, f'Tesseract OCR available ({version}). OpenAI API is not used.'
    except Exception as exc:
        return False, f'Tesseract could not start ({type(exc).__name__}); check its installation.'


def analyze_photo_sync(data: bytes, mime: str) -> tuple[str, str, dict]:
    """Run local OCR to assess only the presence of post-mission results.

    A screenshot may show readable *post-mission results* yet not qualify for
    payout if layout evidence is inconclusive. No mission difficulty, extraction,
    or objective threshold is checked. The form's typed metadata is never
    treated as verified OCR evidence.
    """
    if not _configure_tesseract():
        return ('UNAVAILABLE',
                'Local OCR unavailable: install Tesseract OCR and check TESSERACT_CMD.', {})
    try:
        import pytesseract
        with Image.open(BytesIO(data)) as src:
            if src.width * src.height > 28_000_000:
                return 'UNCERTAIN', 'Image exceeds OCR processing limits; staff review required.', {}
            img = ImageOps.exif_transpose(src).convert('RGB')
            # Recognize player-specific columns before generic OCR. Never
            # interpret three people's stats as one person's totals.
            players, card_count = extract_squad_layout(img)
            if players:
                complete = len(players) == card_count
                return ('PASS' if complete else 'UNCERTAIN',
                        f'Local OCR read {len(players)} of {card_count} player cards on a '
                        'Helldivers results screen. Unreadable cards remain visible but '
                        'cannot receive automatic stat credit. Screenshot authenticity '
                        'is not verified.',
                        {'players': players, 'player_card_count': card_count})
            # Contrast assist; retain normal pixel positions and avoid invented pixels.
            gray = ImageOps.grayscale(img)
            if gray.width < 1200:
                ratio = min(2.0, 1200.0 / max(1, gray.width))
                gray = gray.resize((int(gray.width * ratio), int(gray.height * ratio)),
                                   Image.Resampling.LANCZOS)
            gray = ImageEnhance.Contrast(gray).enhance(1.35)
        extracted = pytesseract.image_to_string(gray, lang='eng',
                                                 config='--psm 11', timeout=25)
        return assess_ocr_text(extracted)
    except (UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
        return 'UNCERTAIN', f'Image could not be read reliably ({type(exc).__name__}).', {}
    except Exception as exc:
        # Missing Tesseract binary, OCR crash, wrong language pack, timeout, etc.
        return 'UNAVAILABLE', f'Local OCR error ({type(exc).__name__}); staff review required.', {}
