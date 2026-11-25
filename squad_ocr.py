"""Conservative per-player OCR for 2–4 card Helldivers 2 results pages.

Recognizes the *layout* in the user's reference screenshot; it does not prove
that a mission happened or that a linked Discord account owns a game name.
Unclear fields are omitted. OCR is run on-device via Tesseract, not an API.
"""
from __future__ import annotations

from PIL import Image, ImageOps, ImageEnhance
import re

# Ordered stat rows in the screenshot. Positions are relative to the screenshot
# height and are only used after identifying the specific three-column screen.
STATS = (
    'kills', 'accuracy_pct', 'shots_fired', 'shots_hit', 'deaths',
    'stims_used', 'accidentals', 'samples_collected', 'stratagems_used',
    'melee_kills', 'times_reinforcing', 'friendly_fire_damage',
)
_ROW_TOP_REFERENCE = 294
_ROW_STEP_REFERENCE = 48
_REFW, _REFH = 1396, 935


def normalize_handle(name: str) -> str:
    """Compare names conservatively; don't fuzzy-match unrelated players."""
    return re.sub(r'[^a-z0-9]', '', str(name).casefold())


def _name_from_header(image: Image.Image) -> str | None:
    import pytesseract
    # Name sits above the 'Level' line. Crop out rank icon + right-edge art.
    upper = image.crop((int(image.width * .18), 0,
                        int(image.width * .91), int(image.height * .062)))
    upper = upper.resize((upper.width*3, upper.height*3))
    # OCR nameplates independently from numerical stats. A poor name reading
    # must never block a perfectly readable statistics card. Tesseract word
    # confidences are only an OCR quality signal, not player authentication.
    data = pytesseract.image_to_data(upper, lang='eng', config='--psm 7',
                                    output_type=pytesseract.Output.DICT,
                                    timeout=12)
    words = []
    for raw,confidence in zip(data['text'],data['conf']):
        word = str(raw).strip()
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            continue
        # Drop HUD icons and low-confidence text rather than displaying
        # nonsense such as 'NOCANCHD Ainv &'. A legitimate name may still be
        # misread, so names are displayed as unverified suggestions.
        if confidence < 70 or not re.fullmatch(r'[\w.\-]+',word,flags=re.UNICODE):
            continue
        if not any(ch.isalnum() for ch in word) and word != '-':
            continue
        words.append(word)
    name = ' '.join(words).strip(' -|.:!')
    return name[:64] if len(name) >= 2 and any(ch.isalnum() for ch in name) else None


def _numeric_ink(image: Image.Image) -> Image.Image:
    """Keep bright/white/yellow HUD numbers, suppress busy armor background."""
    rgb = image.convert('RGB')
    out = Image.new('L', rgb.size, 0)
    inp, dst = rgb.load(), out.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = inp[x, y]
            dst[x, y] = 255 if r >= 145 and g >= 145 and max(r, g, b) >= 180 else 0
    return out.resize((out.width * 3, out.height * 3), Image.Resampling.BICUBIC)


def _number(raw: str, key: str) -> int | float | None:
    value = str(raw).strip().strip('()[]|:; .')
    if key == 'accuracy_pct':
        match = re.fullmatch(r'[+]?([0-9]{1,3}(?:\.[0-9])?)\s*[%°o]?', value)
        if match:
            number = float(match.group(1))
            return number if 0 <= number <= 100 else None
        return None
    # Do not silently change letters to digits; for example 'iso' is ambiguous.
    if not re.fullmatch(r'\d{1,3}(?:,\d{3})*|\d{1,6}', value):
        return None
    n = int(value.replace(',', ''))
    return n if 0 <= n <= (1_000_000 if key in ('shots_fired','shots_hit','friendly_fire_damage') else 100_000) else None


# Conservative upper bounds protect career statistics from dramatic OCR errors.
# Values above these bounds are treated as unreadable, not silently clamped.
_PLAUSIBLE_MAX = {
    'kills': 10000, 'deaths': 200, 'stims_used': 1000,
    'shots_fired': 100000, 'shots_hit': 100000,
    'accidentals': 200, 'samples_collected': 1000,
    'stratagems_used': 1000, 'melee_kills': 10000,
    'times_reinforcing': 200, 'friendly_fire_damage': 1000000,
}


def sanitize_player_stats(data: dict) -> dict:
    """Reject impossible OCR outliers; never award guessed statistics."""
    result = {}
    for key, value in data.items():
        if key == 'accuracy_pct':
            if type(value) in (int,float) and 0 <= value <= 100:
                result[key] = round(float(value),1)
        elif key in _PLAUSIBLE_MAX and type(value) is int and 0 <= value <= _PLAUSIBLE_MAX[key]:
            result[key] = value
    fired = result.get('shots_fired')
    hit = result.get('shots_hit')
    if fired is not None and hit is not None:
        if hit > fired:
            # One or both were misread; do not claim exact accuracy.
            result.pop('shots_hit',None)
            result.pop('accuracy_pct',None)
        elif fired > 0:
            result['accuracy_pct'] = round(hit / fired * 100,1)
    if 'kills' in result and 'melee_kills' in result and result['melee_kills'] > result['kills']:
        result.pop('melee_kills',None)
    return result


def _read_single_stat(src: Image.Image, left: int, right: int, key: str) -> int | float | None:
    """Targeted retry for missing numbers on highlighted HUD text rows.

    OCR stays within a bounded row inside this player's panel. Only run
    for fields missing from the initial numerical-column pass.
    """
    import pytesseract
    if key not in STATS:
        return None
    w,h=src.size
    row=STATS.index(key)
    yc=(_ROW_TOP_REFERENCE + row*_ROW_STEP_REFERENCE + 9)*h/_REFH
    y0=max(0,round(yc - 22*h/_REFH))
    y1=min(h,round(yc + 20*h/_REFH))
    x0=max(left,right - round(w*129/_REFW))
    x1=min(right,right - round(w*5/_REFW))
    if x1-x0 < 24 or y1-y0 < 10:
        return None
    region=_numeric_ink(src.crop((x0,y0,x1,y1)))
    txt=pytesseract.image_to_string(region,lang='eng',
        config='--psm 7 -c tessedit_char_whitelist=0123456789,.%',timeout=12)
    return _number(txt,key)


def _extract_layout_stats(image: Image.Image, count:int) -> list[dict]:
    """Extract named player rows against one candidate layout.

    Returns [] for unsupported images. The caller can use generic OCR/manual
    review instead. Each player gets only their OWN numeric column values.
    """
    import pytesseract
    src = ImageOps.exif_transpose(image).convert('RGB')
    w, h = src.size
    ratio = w / h
    if w < 960 or h < 580 or not (1.25 <= ratio <= 2.05):
        return []
    found = []
    for idx in range(count):
        left = int(w * (idx / count)) + int(w * 8 / _REFW)
        right = int(w * ((idx+1) / count)) - int(w * 37 / _REFW)
        if right <= left + 100:
            return []
        panel = src.crop((left, int(h * 130/_REFH), right, int(h * 858/_REFH)))
        name = _name_from_header(panel)
        # Numeric text is placed on the right of each stats card.
        nx = max(left, right - int(w*120/_REFW))
        ny = int(h * 275/_REFH)
        nr = max(nx+30, right - int(w*10/_REFW))
        num = _numeric_ink(src.crop((nx, ny, nr, int(h*857/_REFH))))
        tokens = pytesseract.image_to_data(num, lang='eng', config='--psm 6',
                                          output_type=pytesseract.Output.DICT, timeout=12)
        data = {}
        # Anchor rows to the in-game stats layout, never sum columns.
        row_centers = [( _ROW_TOP_REFERENCE + i*_ROW_STEP_REFERENCE + 9)*h/_REFH for i in range(len(STATS))]
        best = {}
        for j, raw in enumerate(tokens['text']):
            if not str(raw).strip():
                continue
            global_y = ny + ((tokens['top'][j] + tokens['height'][j]*.5) / 3)
            index = min(range(len(STATS)), key=lambda k: abs(global_y-row_centers[k]))
            if abs(global_y-row_centers[index]) > 20*h/_REFH:
                continue
            key = STATS[index]
            number = _number(raw, key)
            try:
                conf = float(tokens['conf'][j])
            except (TypeError, ValueError):
                conf = -1
            if number is None or conf < 25:
                continue
            # Damage numbers are often confused with busy armor textures;
            # require substantially higher OCR confidence.
            if key == 'friendly_fire_damage' and conf < 75:
                continue
            if index not in best or conf > best[index][0]:
                best[index] = (conf, number)
        for i, (_, number) in best.items():
            data[STATS[i]] = number
        # Bright highlighted kills sometimes lose the last digit in a full
        # numeric-column OCR pass. Re-read only this one clearly bounded row.
        kill_roi = src.crop((max(left, right-int(w*125/_REFW)),
                             int(h*286/_REFH), right-int(w*7/_REFW),
                             int(h*326/_REFH)))
        clean_kills = Image.new('L', kill_roi.size, 0)
        original, pixels = kill_roi.load(), clean_kills.load()
        for ky in range(kill_roi.height):
            for kx in range(kill_roi.width):
                rr, gg, bb = original[kx, ky]
                pixels[kx, ky] = 255 if rr >= 130 and gg >= 130 and max(rr,gg,bb) >= 170 else 0
        clean_kills = clean_kills.resize((kill_roi.width*4, kill_roi.height*4), Image.Resampling.BICUBIC)
        kill_text = pytesseract.image_to_string(clean_kills, lang='eng',
                        config='--psm 7 -c tessedit_char_whitelist=0123456789,', timeout=12)
        kill_number = _number(kill_text.strip(), 'kills')
        if kill_number is not None:
            data['kills'] = kill_number
        # HUD accuracy is often misrecognized (e.g. 41.1 becomes 11.1), but
        # shots hit / shots fired yield the corresponding exact game percentage.
        if data.get('shots_fired', 0) > 0 and 'shots_hit' in data:
            if 0 <= data['shots_hit'] <= data['shots_fired']:
                data['accuracy_pct'] = round(data['shots_hit'] / data['shots_fired'] * 100, 1)
        for important in ('stims_used','deaths','shots_fired','shots_hit'):
            if important in data:
                continue
            candidate=_read_single_stat(src,left,right,important)
            if candidate is not None:
                data[important]=candidate
        data=sanitize_player_stats(data)
        if len(data) >= 4 and any(k in data for k in ('kills','stims_used','shots_fired')):
            found.append({'game_name':name or '', 'column':idx+1, 'stats':data})
    # Verify the actual HD2 row labels as well as the numeric layout.
    # A random three-panel image containing numbers must not auto-pay.
    if len(found) < 2:
        return []
    left_card=src.crop((int(w*.016),int(h*.285),int(w*.225),int(h*.885)))
    gray=ImageEnhance.Contrast(ImageOps.grayscale(left_card)).enhance(2.0)
    gray=gray.resize((gray.width*2,gray.height*2),Image.Resampling.BICUBIC)
    label_text=pytesseract.image_to_string(gray,lang='eng',config='--psm 6',timeout=12).upper()
    required_patterns=(r'SHOTS?\s*FIRED',r'SHOTS?\s*HIT',r'STIM[S5]\s*USED',
                       r'DEATHS?',r'SAMPLES?',r'STRAT[AE]GEMS?\s*USED',
                       r'ACCURACY',r'FRIENDLY\s*FIRE')
    hits=sum(bool(re.search(p,label_text)) for p in required_patterns)
    return found if hits>=4 and ('SHOTS' in label_text or 'STIMS' in label_text) else []


def extract_squad_layout(image: Image.Image) -> tuple[list[dict], int]:
    """Return readable players and the best-supported 2–4 card layout.

    A partly unreadable card is still a *visible player slot* for the Discord
    assignment screen. Cards with readable numeric stats count even when
    OCR cannot confidently read their nameplate.
    """
    possible = []
    for columns in (4, 3, 2):
        values = _extract_layout_stats(image, columns)
        # Repeated OCR NAME guesses are common over noisy player headers.
        # Numeric columns and HUD label checks determine the layout; never
        # discard a readable stats card because two guesses look alike.
        if len(values) == columns:
            return values, columns
        if values:
            possible.append((values, columns))
    if not possible:
        return [], 0
    # When two layouts recognize the same number of players, prefer the
    # layout requiring fewer speculative missing cards.
    return max(possible, key=lambda choice: (len(choice[0]),
                                             len(choice[0]) / choice[1]))


def player_assignment_slots(players: list[dict], card_count: int = 0) -> list[dict]:
    """Show EVERY OCR-detected card, including unreadable/unlinked players.

    Slots lacking a readable player name are still assignable if their numeric
    stats were read. Blank selections represent external/non-faction players;
    we never invent a Discord identity for them.
    """
    known = {}
    for player in players:
        if not isinstance(player, dict):
            continue
        column = player.get('column')
        if type(column) is int and 1 <= column <= 4:
            known[column] = player
    total = max(min(card_count, 4), max(known, default=0))
    if not total and players:
        total = min(len(players), 4)
        known = {i: p for i, p in enumerate(players[:total], 1)}
    slots = []
    for column in range(1, total + 1):
        record = known.get(column)
        name = (str(record.get('game_name', '')).strip() if record else '')
        readable = bool(record and isinstance(record.get('stats'), dict)
                        and record['stats'])
        slots.append({'column': column, 'game_name': name if name else None,
                      'stats': dict(record['stats']) if readable else {},
                      'readable': readable, 'name_readable': bool(name)})
    return slots


def extract_squad_stats(image: Image.Image) -> list[dict]:
    """Backward-compatible per-player OCR; unreadable columns are not guessed."""
    return extract_squad_layout(image)[0]


def extract_three_column_stats(image: Image.Image) -> list[dict]:
    """Backward-compatible API; now handles 2, 3, or 4 player cards."""
    return extract_squad_stats(image)


def player_values(players: list[dict], game_name: str) -> dict:
    """Only attribute stats when one distinctly matching OCR player exists."""
    target = normalize_handle(game_name)
    if len(target) < 2:
        return {}
    matches = [p['stats'] for p in players
               if normalize_handle(p.get('game_name','')) == target]
    return dict(matches[0]) if len(matches) == 1 else {}
