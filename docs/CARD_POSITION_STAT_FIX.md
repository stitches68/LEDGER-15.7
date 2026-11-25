# LEDGER — OCR Name Error / Card-Position Fix

**Problem:** An accepted screenshot showed nonsense OCR player names such as `Taam` and `NOCANCHD Ainv &`. The previous bot compared those guesses with declared player names before it credited personal statistics.

**Change:** The uploader now selects the Discord account for the physical player card (left-to-right). The selected card number is stored as `proof_player_links.player_column`. Payout validation, bonuses, cumulative stats, leaderboard scores and `/stats diagnose` use this number instead of the guessed name. Suspected duplicates still require review; blank cards never receive credit.

## Deploying on Railway

1. Back up the existing volume SQLite database at `/data/ledger.sqlite3`.
2. Upload the **contents** of the release's `LEDGER/` folder to the root of your **private** GitHub repository. Keep `assets/` and root Python modules intact. Do not upload secrets.
3. Deploy to the **existing** Railway service with the same volume mounted at `/data` and `DATABASE_PATH=/data/ledger.sqlite3`. Do not initialize a second empty database.
4. Check `/ledger ocr_status` in Discord.
5. Submit a *new* full-resolution mission stats image. Under **Assign Player Statistics**, use the physical card position, numeric stats and the original image to select the right server members. Leave outsiders blank.
6. After PASS, run `/stats diagnose` for the submission and verify **card 1**, **card 2**, etc. are shown as linked; then check `/stats me` and `/profile`.

Old proof data is preserved, but old records **without a saved player-column assignment are not guessed or rewritten**. Those historical associations retain legacy exact-name behavior. The corrected flow applies to new submissions.

## Limitations

- OCR names are unverified suggestions; poor backgrounds can still produce bad guesses. Position, number extraction and a member's explicit selection control attribution.
- If Tesseract cannot read the numbers, LEDGER cannot reliably record that player's kills or stims, even with a selected Discord user.
- A screenshot cannot authenticate a player's identity or prove unmodified gameplay. Uploader-selected links are subject to normal faction trust and moderator oversight.
- The code passed local regression tests and a real three-player OCR sample; Railway and live Discord interaction need on-server verification.
