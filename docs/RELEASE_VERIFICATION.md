# Release verification — Mission Progress / Player Statistics

**Source:** LEDGER Adopt a Contractor Railway Edition, updated November 11, 2026.

## Automated checks

Run:

```bash
python -m unittest discover -s tests -q
python -m compileall -q bot.py storage.py ocr_verifier.py squad_ocr.py blacksite_ui.py contract_objectives.py mentorship.py player_links.py start_railway.py
```

Verified in the development container: **96 unit tests passing** and Python syntax checks successful.

The user-supplied three-column Helldivers 2 results screenshot was inspected once with the new local Tesseract parser. The player columns were separated, with **210 / 223 / 711 kills** and **3 / 2 / 11 stims used** correctly recognized. Several less prominent fields remained unreadable and were intentionally omitted.

## Known limitations

- OCR cannot verify screenshot authenticity, mission completion on game servers, or whether a Discord account belongs to the depicted player. Name associations are supplied by the uploader.
- UI scale and future Helldivers 2 updates may change screenshot geometry. Results that do not match the recognized layout need manual review or a new parser calibration.
- Actual discord.py interactions, Railway Docker build, and end-to-end bot deployment have **not** been run in this sandbox. Test in a private Discord channel with low-value contracts before regular use.
- No private Discord token, API key or live SQLite database is distributed in this project ZIP.
