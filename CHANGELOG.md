# BLACKSITE OCR / ID / LEADERBOARD POLISH — 2026-11-14

- Added consistent Black Market contract reference (`BMC-000123`) and mentorship case reference (`MTR-000123`) alongside the **numeric IDs** used by existing slash commands. Contract cards, dispatch board, private reviews, mentor case embeds, officer queues, and status notifications surface the IDs prominently.
- Redesigned the recurring Top Earners Discord embed as a sleek green-on-charcoal **Blacksite Rankings** feed. Lifetime contractor and unit earnings, OCR-linked kills, stims, and a compact network settlements block remain separate. Existing wallet/stats/dossier buttons keep their custom IDs; no economy definitions changed.
- Improved player-card OCR with targeted re-reads of **missing** stims/deaths/shots values; kept the proven highlighted-kills crop that correctly reads `210 / 223 / 711` on the user-supplied three-player screenshot. Added cross-field sanity checks for shots/accuracy and impossible numeric outliers. Fields that remain unreadable are omitted instead of guessed.
- Added `scripts/ocr_calibration.py` for testing screenshots locally without touching Discord balances or submitting evidence.
- No new environment variables, slash commands, database migrations or changed payout rules. Same Railway volume and `DATABASE_PATH`.
- All automated tests and Python compile checks pass locally. Live Discord and Railway deployment remain untested; always trial on low-value contracts first.

---

# LEDGER — Card-Position Stats Attribution Fix

- **Fixed false/unreadable player names affecting stats.** OCR guesses for the nameplates no longer control which Discord member gets the numbers. The uploader assigns each results card **by physical left-to-right position**.
- Four-player, three-player, and two-player screenshots are supported when the corresponding numeric cards are readable. Name-unreadable cards can still be linked if the statistics were extracted.
- `/stats diagnose` reports card numbers and marks guessed OCR names as unverified. A manual wrong OCR name no longer prevents a correctly linked contractor from appearing in `/stats me`, `/profile`, and kills/stims rankings.
- Blank selections remain outsiders. Repeat/bonus payouts continue to require linked readable player stats; duplicates are still screened.
- New optional `proof_player_links.player_column` column is created automatically when LEDGER starts, preserving prior database records, payouts, and inventories. Older links continue to use exact OCR-name matching; they are not retroactively rewritten.
- OCR name reading now filters low-confidence words and HUD symbols. Nameplates can still be misread, which is why labels are suggestions only.
- 118 tests passed locally, including new card-position attribution and duplicate-OCR-name tests; the existing real three-player reference image still yields 210 / 223 / 711 kills and 3 / 2 / 11 stims.
- Discord and Railway remain to be live-tested after deployment.

---

## Update — Optional per-card Discord assignments (2026-11-11)

- All detected results-screen player slots now appear in **Assign Player Statistics** (up to four), including unreadable panels.
- Dropdowns start empty and are optional: blank means outside Discord / not in the R.E.A.P.E.R. faction. Unreadable names are labeled and cannot be assigned automatically.
- Only linked players count toward faction-level statistics and personal dossiers; outside-squad data remain visible in stored evidence, not on faction rankings.
- Prevented automatic **and moderator-approved** personal payouts to members with no matching, readable card on screenshot-based stats contracts. Unit treasury contracts remain independent.
- Added regression tests for incomplete four-player screenshots, outside squads, and payment safeguards.
- No database migration required; `player_card_count` is an optional saved OCR field.

# BLACKSITE OVERHAUL — Latest

- Charcoal/green contractor terminal, both market embeds, contract cards and edited personal dossiers.
- Native post-upload per-player Discord member selectors (max four OCR cards); player kills/stims/deaths preview before confirmation.
- Offline Tesseract **individual stats** OCR plus conservative, separate **operation status** OCR. Contracts choose STATS / OPERATION / BOTH. Both images count as one verified mission.
- Live `publish_dispatch` progress board; moderator-selected channel, persistent message, refresh on accepted payouts plus background two-minute updates; per-contract public targets.
- Custom `/dossier edit` and `/dossier view`, with profile action buttons and original `/intel` lore transmissions.
- Public mentorship button renamed to **Find a Mentor**. All previous contract, unit, bank, shops, and mentorship mechanics retained.
- Additive database migration for dossier customization, evidence rules, progress targets and operation companion duplicate checks.
- Verified 106 local automated tests and Python syntax checks. Tested stats OCR against the supplied real three-player screenshot, and operation OCR against a synthetic completion screen. **Neither Railway deployment nor Discord integration nor a real operation screenshot was tested here.**

---

## Mission Progress and Individual Player OCR — November 11, 2026

- Three-column Helldivers results OCR reads stats **per visible player**, rather than merging squad columns.
- Includes kills, stims, shots fired/hit, derived accuracy, deaths, stratagems, samples and more when legible.
- Contractor stats and kills/stims leaderboards use explicit uploader-provided in-game name / Discord member pairings.
- Contracts may require **1–25 accepted screenshots per completion**, with an optional repeatable mode and audited payout cycles.
- Optional bonus objectives accumulate *recipient-specific* stats across a cycle, paying only when complete.
- New `/contract progress` and `/contract configure_progress` commands; broker contract draft has a Mission Count / Repeat editor.
- Duplicate fingerprints remain active across cycles; rejected or uncertain OCR results go to moderation.
- Additive SQLite upgrade; old contracts remain single-submission, one-time.
- Railway Dockerfile now includes `squad_ocr.py`.

## Squad identity-linking and payout mentions

- Added a private follow-up screen **after image upload** to select up to three Discord teammates.
- Added a dynamic popup linking each teammate to their in-game player name (and optional self-name).
- Added durable, guild-scoped per-attempt player links and reported handles in contractor dossiers.
- Ping actual credit recipients in the contract channel only once a payment is confirmed, including staff approvals.
- Added moderator command `/ledger unlink_player` for disputed player-name annotations.
- Kept OCR verification, anti-duplicate checking, bonuses, both shops, credits, stats, and unit treasuries unchanged.
- Reported names are **not** authenticated; no per-teammate kills/stims attribution is inferred.
- Included the new `player_links.py` module in Railway Docker COPY.

# LEDGER — Local OCR Verification (Current Release)

- Replaced remote OpenAI image verification with **local Tesseract OCR** and the lightweight `pytesseract` Python wrapper; no paid AI API or API key is required.
- New OCR-only `ocr_verifier.py` module: results text classification, conservative readable stats extraction, and install checks.
- New `/ledger ocr_status` moderator command for Windows installation troubleshooting.
- Preserved duplicate checks, group auto-payout safeguards, manual moderator review, moderator self-approval, shops, units, financial history, statistics and the 30-minute Top Earners board.
- Only visible, unambiguous statistic values are saved. Low-confidence photos route to private moderation; failed/unrelated images do not pay.
- Preserved existing SQLite field names (`ai_verdict`, `ai_summary`, `vision_stats`) for non-destructive compatibility with older versions.
- Additive install requirement: **Tesseract application on the machine** plus `pytesseract` installed by pip; no remote OCR service used.
- Local automated tests and one synthetic image OCR smoke test passed. Authentic in-game screenshots and Discord interactions still require live testing.

---

# LEDGER — Screenshot-Only Payouts & Moderator Self-Approval

- Moderators and server managers can approve **their own** proof submissions using `/contract approve` or the private review buttons. Approval and credits remain logged; non-moderators cannot approve.
- AI PASS now means a clear **Helldivers 2 mission-results screenshot** with at least one readable results element. No contract description, kill count, planet, difficulty or successful-extraction threshold applies.
- Contract-create popup no longer shows optional custom screenshot criteria; `/contract set_criteria` removed. Previous saved criteria stay in SQLite but are ignored.
- Clear PASS screenshots auto-pay; duplicates, uncertain images, AI outages and payout conflicts still go to review. Photos clearly not showing results fail.
- Existing credits, unit treasuries, two shops, stats, role assignments, and 30-minute leaderboard are preserved.
- SQLite/database regressions and Python compilation validated; live Discord and image API still require verification.

---

# LEDGER — Top Earners & Screenshot Statistics

## New in this release

- A **Top Earners channel** added as the fifth dropdown in `/ledger channels`, with one persistent public embed refreshed every 30 minutes while LEDGER is online, on startup, and after contract payments. Deleted board posts are recreated.
- Displays **lifetime contract credits earned** for the top 10 individual contractors and top 5 units; separate from current wallet balances, shop spending and manual staff adjustments. Imported legacy payouts count.
- Buttons for **My Earnings** and **My Verified Stats** on the public leaderboard (private replies).
- `/ledger refresh_top_earners` allows staff to manually refresh the published board.
- Contract AI image checks now preserve legible screenshot mission stats in SQLite, without inventing missing values. Existing user-entered planet and difficulty are kept as **reported** metadata.
- `/stats me`, `/stats member`, `/stats unit`, `/stats server`, `/stats recent` show proof history, accepted submissions, mission details and image-derived aggregates. A single screenshot is counted once even when multiple tagged teammates are paid.
- Additive SQLite migration preserves balances, shops, units, review history and credits. **Historical screenshots are not re-analyzed.**

## Testing

Existing SQLite/contract regressions and new leaderboard/stats tests pass, plus Python compilation. Live Discord/AI behavior still needs testing in a development server.

---

# LEDGER — Interactive Setup & Contract Forms

## New in this release

- `/ledger channels`: one private Discord message with FOUR native channel dropdowns (contract board, private proof review, personal shop, unit shop). Selecting an option saves it immediately and refreshes the panel. The older `/board set_channel`, `/board set_review_channel`, `/shop set_channel`, and `/unitshop set_channel` shortcuts now open the same panel without a `channel` argument.
- `/unit setup`: multi-select up to 25 existing Discord roles; each selected role becomes an independent registered unit and treasury. Existing wallets and officer roles remain unchanged. Membership is derived from current Discord roles, so it updates as members gain or lose those roles.
- `/unit link_menu`: two dropdowns to select an existing registered unit and link multiple additional member roles. The existing `/unit link_role` command remains available.
- `/contract post`: runs an interactive popup with title, objective, payout, type (individual or unit), and optional screenshot criteria. For unit contracts, a follow-up selector targets a particular unit or any registered unit. No slash-command arguments are required.
- Previous automatic screenshot verification, duplicate checks, tagged teammate payments, shops, icon, and all credits remain supported.

## Upgrade

Back up `.env` and `ledger.sqlite3`, copy in the new bot files, install requirements, and restart. **There is no destructive database migration.** Slash commands are re-synced on boot; use `/ledger channels` instead of typing channel options into the former channel commands.

## Validation

SQLite regressions and Python syntax tests passed. Discord client UI and API checks require live-server validation.

---

# LEDGER — Tagged Teammate Proof & Squad Payouts

## New in this release

- **Automatic payout:** screenshot AI PASS + no duplicate + no pending/paid recipient conflicts = immediate full Blacksite Credit payout to submitter and tagged teammates; no screenshot-to-Discord-name matching.
- **Manual review only when flagged:** UNCERTAIN, API unavailable, suspected duplicate, or conflicting/previous payment. These are the *only contract-proof submissions* sent to the staff review channel.
- **Automatic failed-proof rejection:** clear FAIL without a duplicate warning means no payment; the member can submit new proof.
- **Exact/near duplicate detection:** image SHA-256 and a conservative image fingerprint; duplicates across all contracts in the same Discord guild go to staff review, not auto-pay.
- **Screenshot modal:** image file, planet, difficulty 1–10, a box for tagging up to 3 other Discord server members pictured, optional notes. Unit payouts omit teammate selection and credit only the unit treasury.
- **Group approvals:** review buttons **Approve All & Pay**, **Pay Submitter Only**, or **Reject**. `/contract approve` includes an `include_teammates` boolean.
- **Crash recovery:** abandoned, unposted image checks are released so the member can resubmit.
- **Legacy preserved:** contracts, credits, individual/unit shops, unit roles, all old claims, and original user-provided Blacksite Credits icon.

## Requirements

- Python 3.11+
- `discord.py>=2.7.1,<3`, `python-dotenv>=1.0,<2`, `Pillow>=10,<13`
- `OPENAI_API_KEY` in `.env` **required for automatic payout**. No key or API outage means all screenshots go to moderator review.
- Secure private staff review channel configured with `/board set_review_channel`.

## Upgrade from earlier LEDGER

1. Stop the current LEDGER process (Ctrl+C).
2. Make a backup copy of `ledger.sqlite3` and `.env`.
3. Extract the new ZIP; copy updated `bot.py`, `storage.py`, `requirements.txt`, `README.md`, and `assets/` over the old files. Keep your existing `.env` and SQLite database.
4. From LEDGER folder, run `python -m pip install -r requirements.txt` then `python bot.py`.
5. If automatic verification is desired, set `OPENAI_API_KEY=...` in `.env`. Never send this key to staff or post it in Discord.
6. Test with a low-value sample contract before enabling real in-server payouts.

## Important limitations

- Screenshots cannot prove they are unaltered or that a Discord account owns the in-game player identity. AI recognition can make mistakes. Consider staff review for unusually large rewards.
- For auto-paid **teammates**, the uploader tags other server members in the submission popup. LEDGER does not verify tagged identities against screenshot names. This relies on the uploader's declaration and may be abused; moderators should audit suspicious payouts.
- Visually altered/cropped screenshots may evade duplicate detection, and benign similar screenshots may be flagged.
- The review channel only receives flagged **contract proof images**; existing shop purchase receipts may still be posted there by LEDGER.
- OpenAI API image processing costs money and sends members' submitted screenshots to the configured API; tell members first.
- Not yet tested against a live Discord server in this build environment; database tests and Python syntax checks passed.

## Changes: tag teammates instead of matching names

- The **Tag teammates pictured** dropdown in the upload popup lets submitters tag up to three other members directly.
- On a unique AI PASS, the submitter and all tagged members earn the full individual contract reward without comparing Discord names to names visible in-game.
- Suspected duplicates, uncertain screenshots, and existing payment conflicts still go to the review channel.
- The five-field Discord upload modal keeps screenshot, planet, difficulty, teammate tags, and notes.


## Adopt a Contractor — Nov 11, 2026

- Automatic role-to-unit roster index, optional full Guild Members Intent synchronizer. Current Discord unit roles remain authoritative.
- New mentorship intake, private dispatch, lead/senior-officer pings, assignment and opt-in mentor confirmation.
- Trainee-confirmed training sessions (including DM confirmation button), trainee sign-off and independent officer clearance.
- One-time mentorship Blacksite Credits payout integrated into lifetime earnings and Top Earners.
- Safe additive tables: mentor settings/leads/requests/sessions and unit rosters. Previous credits, shops, contracts and screenshots untouched.
- New `/mentor` commands and `/unit roster`, `/unit sync_roster`.
- Tests added for enrollment/reconciliation, role-based office permissions, lifecycle, payout idempotency and data migration.

## Stat attribution diagnostic patch
- Added `/stats diagnose` with OCR verdict, recognized player names, uploader-reported Discord links, and reasons stats were not credited.
- Screenshot linking now previews OCR-recognized player names before the name-entry modal and reuses the same OCR result at final submission.
- Unit contract uploads can link participating Discord members for individual career statistics; unit payouts remain treasury-only.
- Successful submissions now explicitly say whether stats were extracted and linked.
- Existing SQLite data and Blacksite Credits remain untouched by the code update.
