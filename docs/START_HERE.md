## New: Reliable Discord stat linking when player names are garbled

On the **Assign Player Statistics** screen, select Discord members by **left-to-right player-card position**, not the guessed OCR name. The upload screen shows each card's kills/stims first. Leave outside/non-faction player cards blank. This release stores `player_column` in SQLite and credits the selected card's stats directly, even when its in-game name is misread. A card whose numbers are unreadable still needs a clearer screenshot. Run `/stats diagnose` after a new proof to confirm `card N` links appear.

---

# START HERE — LEDGER BLACKSITE NETWORK

**The Broker has opened the books.** LEDGER is a fictional Blacksite Credits Discord economy for the R.E.A.P.E.R. Divers, styled as an underground PMC arms dealer's secured terminal. It is a Discord bot, not a website. Discord controls the app's dark/light background and component appearance; LEDGER controls its embeds, labels, interactions, and Blacksite Credits artwork.

## Deploy safely on Railway

1. **Stop the old LEDGER process** (including the copy on your PC). Run only one instance of the same Discord bot.
2. **Back up the SQLite database** currently in your mounted Railway volume: `/data/ledger.sqlite3`. Don't delete the existing volume or overwrite this file with an empty database.
3. Upload the **contents of the `LEDGER/` folder** to the *root* of your **private GitHub repository**, keeping directories intact. The root contains `Dockerfile`, `bot.py`, `storage.py`, `squad_ocr.py`, `operation_ocr.py`, and `blacksite_lore.py`. The `assets/` directory contains Blacksite Credits art.
4. In Railway, keep your service's persistent volume attached at **`/data`**. In the **same service → Variables**, configure:

   ```env
   DISCORD_TOKEN=your_private_discord_bot_token
   TEST_GUILD_ID=your_discord_server_id
   DATABASE_PATH=/data/ledger.sqlite3
   LEDGER_MEMBER_SYNC=1
   ```

   `LEDGER_MEMBER_SYNC=1` additionally requires **Server Members Intent** enabled under your application's **Bot → Privileged Gateway Intents** in the Discord Developer Portal.
5. Commit, deploy, inspect Railway logs, and confirm LEDGER comes online. This project uses a Docker worker; **no public HTTP domain is needed**. Docker installs `tesseract-ocr` and English recognition automatically.
6. Run `/ledger ocr_status` in Discord, then start the in-server setup below.

**Never upload `.env` to GitHub, share your token, or manually fake Railway's `RAILWAY_VOLUME_MOUNT_PATH`.** If Railway reports a missing volume, attach the volume to the same running service and mount it at `/data`.

## Discord setup in the right order

1. `/ledger create_mod_role` — assign this role to your authorized staff.
2. Create channels: `#blacksite-terminal`, `#black-market-contracts`, `#contractor-market`, `#unit-supply-exchange`, `#top-earners`, and a **private** `#contract-reviews` visible only to staff and LEDGER.
3. `/ledger channels` — choose the last five channels in the interactive selectors.
4. `/ledger install_credit_icon` — install the supplied Blacksite Credits emoji.
5. `/ledger publish_hub` — choose `#blacksite-terminal` for the public terminal.
6. `/ledger publish_dispatch` — choose `#black-market-contracts` or a separate `#blacksite-dispatch` for the **live contract progress board**. One persistent message is edited after payouts and every 2 minutes.
7. `/unit setup` — select existing Discord unit roles. LEDGER recognizes their members without assigning extra roles. `/unit set_buyer_role` for unit-market buyers.
8. `/shop add`, `/shop publish` and `/unitshop add`, `/unitshop publish` — establish the two markets.
9. `/mentor setup`, `/mentor set_lead`, `/mentor publish` — configure the private mentor dispatch desk and public **Find a Mentor** button.
10. `/contract post` — enter contract terms. In its private draft set **evidence type**, **required mission count**, **repeatability**, **public report goal**, and optional stat bonuses; then publish.
11. Test a **low-value** stats contract. Upload a full-size end-of-mission statistics screenshot; assign each relevant physical player card to its Discord member (do not rely on the guessed OCR name); leave all other dropdowns blank, then confirm. Use `/stats diagnose` and `/profile` to check actual recorded values. Test operation-only and both-evidence modes with staff review before enabling automatic payment broadly.

## What a contractor sees

- `/hub`: Blacksite terminal, contract board, private dossier, markets, units, and **Find a Mentor**.
- `/contract submit`: select a contract → attach stats and/or operation evidence → preview player columns → optionally assign each detected player card to a server member (blank = outside server/faction) → **Confirm & Submit**. Also available via `/submit-mission-stats` and `/submit-operation`.
- `/dossier edit`: customize callsign, specialty, bio, and motto. `/profile` shows this card with personal Blacksite Credits and actually attributed stats.
- `/contract progress`: personal accepted-mission progress and paid cycle, including repeatable contracts.
- `/intel`: occasional original, lore-only black-market transmissions. It doesn't change accounts.

## Reading screenshots accurately

The **stats OCR** is trained by layout and rules to separate visible players in Helldivers 2 result cards. The user supplied a full-resolution **three-player** screenshot for regression testing. The parser also attempts two- and four-player layouts; these require more real-world testing. Only readable, correctly linked player-column numbers are saved as individual stats. A mission screenshot may earn credits without yielding a valid stat line if a moderator manually approves it; use `/stats diagnose`.

The **operation OCR** is separate and looks for operation-level context (planet, difficulty, completion evidence). In `BOTH` mode, an operation screenshot *and* stats screenshot constitute **one** contract report. The operation parser was only tested on generated text-layout evidence; a genuine Helldivers operation-completion image is still needed to tune recognition, so ambiguous images deliberately go to staff. This is not proof of genuine gameplay or tamper-proof verification.

## Safeguards

- Duplicate SHA-256/visual fingerprints flag suspicious repeats for staff rather than automatically awarding.
- One accepted two-image proof counts as **one** mission, regardless of how many teammates are linked.
- Partial or unreadable numerical results are omitted, never guessed.
- When multi-mission contracts reach their accepted screenshot target, eligible recipients receive base and qualifying bonus credits in one audited database transaction.
- An *officer other than the mentor* must approve a completed **Find a Mentor** mentoring payout.

See [COMMANDS.md](COMMANDS.md), [OCR_AND_STAT_TRACKING.md](OCR_AND_STAT_TRACKING.md), [BLACKSITE_LORE.md](BLACKSITE_LORE.md), and [DISCORD_SETUP.md](DISCORD_SETUP.md) for detailed references.
