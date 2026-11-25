> **Blacksite Polish Update:** Replace all Python source files from this release, including the **new `blacksite_ids.py` module**. The Dockerfile explicitly copies that module. Keep `/data/ledger.sqlite3` and Railway variables; do not recreate the volume. After deployment run `/ledger refresh_top_earners`, inspect a contract card and mentor case, and submit a low-value test screenshot. No new Discord commands or database migrations are necessary.

## Important — Card Position Stats Fix

This build changes how **new** screenshot submissions link player statistics to Discord accounts. The physical card index (left-to-right) is saved in a new optional SQLite column. Existing credits, contracts, units, shops and older links remain intact. Do not delete `/data/ledger.sqlite3`.

Upgrade: Back up the volume database; upload the full ZIP contents to your private GitHub repository, including root-level `squad_ocr.py`, `storage.py`, `bot.py`, `ocr_verifier.py`, and all other root modules and `assets/`; deploy with the existing Railway volume mounted at `/data`. Run `/stats diagnose` on a **new** screenshot submission and confirm it displays `card N` for the selected Discord member. Check `/stats me` for the resulting kill and stim totals. Existing screenshots are not retroactively assigned a new column position.

---

# Latest Blacksite overhaul notes

For the concise deployment checklist, use **[docs/START_HERE.md](docs/START_HERE.md)**.

New mandatory root Python modules for this build: `operation_ocr.py` and `blacksite_lore.py`, in addition to `squad_ocr.py`, `ocr_verifier.py`, `blacksite_ui.py`, `mentorship.py`, `player_links.py`, `contract_objectives.py`, `bot.py`, `storage.py`, and `start_railway.py`. The provided Dockerfile copies all of them. The `assets/` folder must also be uploaded intact.

Keep your existing mounted `/data` volume and back up `/data/ledger.sqlite3`. Additive migrations retain older wallets, roles, contracts, orders and history. Never manually create `RAILWAY_VOLUME_MOUNT_PATH` to bypass the real Railway volume check.

After deployment run `/ledger ocr_status`, `/ledger publish_dispatch`, `/profile`, and `/stats diagnose`. For live screenshot OCR tuning, submit a full-size three-player stats image, then a real operation-results image. The operation recognizer is conservative and sends uncertain screenshots to manual review.

---

# Deploy LEDGER to Railway (Windows / GitHub instructions)

This is the complete **LEDGER Local OCR Edition** with Railway packaging. It includes the Blacksite Credits economy, contracts, unit treasuries, role-linked units, dual shops, screenshot OCR, duplicate detection, teammate payouts, stats, and the refreshing Top Earners board.

Railway runs a **persistent Discord worker**, not a website: **no public domain, PORT variable, or HTTP health check is needed.** The `Dockerfile` installs Python 3.12, the Tesseract binary, and English OCR data. No OpenAI API key is used.

## Essential configuration

- Railway project: one service named **LEDGER**; **one replica** only (SQLite must not be used with several replicas of the same bot).
- **Attach a Railway Volume to LEDGER with mount path `/data`** (do this before providing a valid bot token).
- LEDGER service -> **Variables**:

  ```env
  DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
  TEST_GUILD_ID=YOUR_DISCORD_SERVER_ID
  DATABASE_PATH=/data/ledger.sqlite3
  ```

  `TEST_GUILD_ID` is optional. `DATABASE_PATH` points into the volume. **Do not** commit your real token, `.env`, or local `.sqlite3` to GitHub. Add secrets to Railway's Variables tab, not the repo. Railway automatically supplies `RAILWAY_VOLUME_MOUNT_PATH` when a volume is attached. No `TESSERACT_CMD`, `OPENAI_API_KEY`, or `LEDGER_VISION_MODEL` is necessary.

**Safety guard:** `start_railway.py` will refuse to launch in Railway if the persistent volume is missing or the database path points outside it. This prevents an accidental fresh database with apparently vanished credits on a new deployment.

## Option A — deploy using GitHub (automatic updates)

1. Extract the ZIP, then open the inner `LEDGER` folder. That folder contains `Dockerfile`, `bot.py`, `storage.py`, `ocr_verifier.py`, and `railway.json`.
2. Create a **private** GitHub repository and upload the **contents of the inner `LEDGER` folder into the repository root**. GitHub must see `Dockerfile` at the top level. Never upload `.env`, local database files, or tokens.
3. At [Railway](https://railway.com), create a project, connect the repository as its service source, and name the service `LEDGER`. It might fail the first deployment because variables/volume have not been set; that's expected.
4. Open the service's canvas menu and attach a **Volume** mounted at **`/data`**.
5. In the service's **Variables** tab, set `DISCORD_TOKEN`, `DATABASE_PATH=/data/ledger.sqlite3` and optional `TEST_GUILD_ID`. Deploy the staged changes. Make sure the service is configured as a continuously running service, not a cron job or scale-to-zero web process.
6. Check service **Deploy Logs**. Look for `LEDGER database path: /data/ledger.sqlite3`, `Starting LEDGER with offline Tesseract OCR`, and the normal Discord login messages.
7. In Discord, run `/ledger ocr_status`. It should report that Tesseract is available.

Railway automatically discovers `Dockerfile` at the repository root. `railway.json` selects Dockerfile builds and an on-failure restart policy.

## Option B — deploy without GitHub (Railway CLI)

1. Install [Node.js](https://nodejs.org/) and then, in Command Prompt, run `npm install -g @railway/cli`.
2. Log in: `railway login`.
3. Create a Railway project with **one Empty Service**, choose it as `LEDGER`, and add a persistent volume mounted at `/data` from the Railway dashboard. Add the variables above.
4. In **File Explorer**, open the inner `LEDGER` folder, click the address bar, enter `cmd`, and press Enter.
5. Run `railway link` and select the project, environment, and service, then run `railway up` to upload and deploy this folder.
6. Open the service's deployment logs and check `/ledger ocr_status` in Discord.

`railway up` is a one-time or manual upload. GitHub-connected deploys can deploy automatically on code changes.

## Bring your EXISTING credits and contracts to Railway

If you already used LEDGER on your Windows PC, **do not skip this** or you'll start with an empty database.

1. Stop the PC copy of LEDGER with Ctrl+C so the SQLite file is consistent; keep it stopped during migration.
2. Back up your existing `ledger.sqlite3` file and any nonstandard database configured by `DATABASE_PATH`. Never place the real database inside your GitHub repo or ZIP.
3. Create the Railway service and `/data` volume. **Do not yet set a valid `DISCORD_TOKEN`**; the new server must not start and initialize a new database during transfer.
4. Install and use Railway CLI (`npm install -g @railway/cli`, `railway login`, `railway link`), then from a folder containing your backed-up database use:

   ```powershell
   railway volume files upload ./ledger.sqlite3 /ledger.sqlite3
   railway volume files list /
   ```

   If the CLI asks you to choose a volume, pick the one attached to LEDGER. The upload destination `/ledger.sqlite3` means the file will appear inside the Railway container at `/data/ledger.sqlite3` (the volume's mount path). For older CLI versions, use `railway volume browse /` and upload interactively.
5. Set `DISCORD_TOKEN` in Railway Variables and deploy/redeploy. Run `/balance`, `/unit list`, and `/ledger settings` to verify data came across.
6. **Only run the Railway bot**, not the Windows bot, after the transfer; two processes using the same Discord bot token can conflict.

Important: Railway volumes are persistent storage, **not a substitute for backups**. Enable volume backups in Railway, and periodically download a separate copy of `ledger.sqlite3`. Stop the bot or make a SQLite-consistent backup before exporting the live database. Keep only one Railway replica when using SQLite.

## Troubleshooting

| Symptom | Likely fix |
| --- | --- |
| `LEDGER CONFIGURATION ERROR: Railway volume missing` | Add a volume to LEDGER, mounted at `/data`. |
| `DATABASE_PATH must point INSIDE...` | Set `DATABASE_PATH=/data/ledger.sqlite3`; check the mount path matches. |
| `Missing DISCORD_TOKEN` | Add token to Railway **Variables** and deploy. |
| Bot starts but your old balances are missing | An empty SQLite database was created; stop bot, check the correct migrated database is on the mounted volume. **Don't overwrite until backed up.** |
| OCR says unavailable | Confirm deployment was built with the included Dockerfile; its `tesseract-ocr` + `tesseract-ocr-eng` packages should be installed. |
| `no data found` for stats | OCR only records text visible in the screenshot. Ask for a clear full stats screen. |
| Discord commands unavailable | Confirm token, correct guild, slash-command invite scopes, and service logs. |
| No HTTP URL shows up in Railway | Normal. This is a Discord background worker, not a web app. |

**Note:** This build has passed code and local unit tests, but the final Railway deployment must be performed in your own account. Cloud deployment, Docker image build, and live Discord/OCR integration cannot be validated without those services.

## Source and official Railway docs

- [Railway Dockerfile builds](https://docs.railway.com/builds/dockerfiles)
- [Railway volumes](https://docs.railway.com/volumes)
- [Railway service variables](https://docs.railway.com/variables)
- [Railway CLI upload](https://docs.railway.com/cli/up)
- [Railway volume CLI](https://docs.railway.com/cli/volume)

### Squad name-linking update

Upload the new `player_links.py` **at your repository root** alongside `bot.py`; it is required by `storage.py`. The included Dockerfile copies it into Railway. This release only adds the `proof_player_links` table and keeps balances, contracts, inventories and saved unit links. Make a volume-backed SQLite backup before upgrading.


## Adopt a Contractor and automatic unit rosters (new)

The latest ZIP contains **`mentorship.py`** at the repository root; the Dockerfile must copy it alongside `bot.py` and `storage.py`. Existing `/data/ledger.sqlite3` files are upgraded **additively**, retaining balances, shops, units, contracts and player links. Back up the database before deploying.

For continuous unit-role roster updates (join, leave and role changes):

1. In the [Discord Developer Portal](https://discord.com/developers/applications) choose LEDGER → **Bot** → **Privileged Gateway Intents** → enable **Server Members Intent**.
2. In Railway → LEDGER service → **Variables**, add `LEDGER_MEMBER_SYNC=1` and deploy. This enables full member-list synchronization. Without it, LEDGER still checks the roles of users interacting with it; the saved rosters are not kept live.
3. In Discord, run `/unit sync_roster` once if needed and `/unit roster` to inspect a registered unit.
4. Create a private `#mentor-dispatch` channel with **View Channel** access for your unit leads, higher officer roles and LEDGER. Run `/mentor setup` to choose it and the officer roles, then `/mentor set_lead` for each unit. `/mentor terms` sets the per-mentor Blacksite Credits payout and required trainee-confirmed training sessions. `/mentor publish` creates the public request board.

**Security:** The bot's member intent requires approval if the app is subject to Discord's privileged-intent review threshold. Do not manually set a fake `RAILWAY_VOLUME_MOUNT_PATH`; attach the real `/data` volume. Mentorship data includes trainee-provided learning goals and availability, so keep the dispatch channel private.


## Multi-mission + player-column OCR update

This release includes one additional root-level Python module, **`squad_ocr.py`**. Upload it alongside `bot.py`, `storage.py`, `ocr_verifier.py` and other modules. The included Dockerfile already copies it to the container. Leaving it out will cause `ModuleNotFoundError: No module named 'squad_ocr'`.

The SQLite migration adds `contracts.required_submissions`, `contracts.repeatable`, and two progress audit tables. These are **additive changes**; balances and existing one-mission contracts are preserved. Back up the live `/data/ledger.sqlite3` volume before deploying, retain `DATABASE_PATH=/data/ledger.sqlite3`, and keep only one running LEDGER bot. No extra secrets, OCR API, or Discord role permission is needed.

After redeploying: `/ledger ocr_status` → `/contract post` → set **Mission Count / Repeat** in draft → add bonuses → publish. Contractors use `/contract progress`. For stats, upload the full **three-column post-mission player results page** and link each player's in-game name to the corresponding Discord member.


## Troubleshooting missing mission stats

Once LEDGER is running, submit a fresh full-width Helldivers results screenshot and enter the exact OCR-recognized player names shown in the post-upload linking screen. Run `/stats diagnose` to see whether the image was accepted, whether numbers were extracted, and whether names were matched. Unit contract submissions now offer the same stat-linking screen. Read `docs/STAT_TRACKING_DIAGNOSTICS.md` for interpretation. Back up the Railway `/data/ledger.sqlite3` database before replacing the source files.
