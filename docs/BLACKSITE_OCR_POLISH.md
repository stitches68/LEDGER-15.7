# LEDGER — Blacksite OCR, Reference IDs & Rankings

## What changed

**Card-by-card OCR:** Reads each of up to four visible HD2 statistics cards in its own image region. The first pass reads numbers along the right edge of each card. If key fields are missing (stims used, deaths, shots fired/hit), a narrowly cropped second pass attempts recovery; highlighted **kills** keep the proven high-resolution crop. Results are checked for obvious impossibilities (e.g., shots hit > shots fired) rather than fabricating numbers.

**This is Tesseract tuning, not machine-learning training.** No external API, account, model download, or image upload is involved. New screenshots that differ significantly in HUD layout may still fail. OCR does not authenticate gameplay or identities. Confidence and correctness are not guaranteed.

**Contract IDs:** A card now shows **CONTRACT ID `123`** and branded reference `BMC-000123`. Use **123** as `contract_id` in `/contract status`, `/contract submit`, etc.

**Mentor IDs:** Mentor case cards show **MENTOR REQUEST ID `8`** and `MTR-000008`. Use **8** as `request_id` in `/mentor status`, `/mentor assign`, etc. Training session IDs and proof review IDs are separate identifiers.

**Leaderboard:** A dark-neon Green Discord embed provides four rankings: contractor earnings, unit earnings, OCR-read kills, and OCR-read stims. It still refreshes **every 30 minutes**, edits the same message, and uses the existing wallet/telemetry/dossier buttons.

## Check a screenshot on your own machine

Tesseract and Python requirements must be installed. From the LEDGER project folder:

```bash
python scripts/ocr_calibration.py "path/to/your-HD2-stats.png"
```

This outputs card numbers and any readable values without submitting proof, paying credits, writing to the database, or uploading the image. Player names are not required to attribute stats; the uploader selects the Discord member matching each **card position**.

For a safe live test after deploying: run `/ledger ocr_status`, submit one new low-value proof screenshot, link your card in the member selector, then inspect `/stats diagnose`, `/stats me`, and `/profile`.

## Deployment

Upgrade your existing Railway service and GitHub repository. **Back up** the persistent SQLite database first; keep its `/data` volume and secrets. Replace source files, including `blacksite_ids.py`, and deploy the included Dockerfile. Use `/ledger refresh_top_earners` to republish the redesigned live board right away.

## Known limitations

- Results can differ by in-game HUD resolution, scaling, screenshots from other versions, or compression.
- Missing/unreadable statistics are not counted toward personal dossiers or bonus-objective thresholds.
- Discord renders embeds according to its own theme. The bot controls green embed accents and typography, not Discord's background or font family.
- No new OCR model weights are trained by this update. Future accuracy improvements require anonymized, representative screenshots with known expected values.
