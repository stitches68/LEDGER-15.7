# First-time Discord setup — Blacksite Network

**Before:** LEDGER shows online and `/ledger ocr_status` reports Tesseract available. The bot needs `applications.commands`, View Channels, Send Messages, Embed Links, Read Message History, Attach Files, Manage Roles (for role creation), and Create Expressions (to install the emoji). The staff review channel must be hidden from `@everyone`.

1. **Authorize staff:** `/ledger create_mod_role`; assign `LEDGER | Market Moderation` to trusted moderators. Or `/ledger set_mod_role` for an existing role.
2. **Create five channels:** `#black-market-contracts`, `#contract-reviews` (staff only), `#contractor-market`, `#unit-supply-exchange`, `#top-earners`. Run `/ledger channels` and set each from the live dropdowns. Selections save immediately.
3. **Install the currency icon:** `/ledger install_credit_icon`. Check `/ledger settings`.
4. **Create the public terminal:** create a `#blacksite-terminal` channel if desired; `/ledger publish_hub channel:#blacksite-terminal`. Members can also run `/hub` to open the same menu privately.
5. **Register units:** `/unit setup` → select existing Discord unit roles. Each selected role creates its own unit treasury. Changes to Discord roles automatically affect unit membership; no roster retyping. If a unit has auxiliary roles, use `/unit link_menu` to link them to its main unit role. Set purchasing roles with `/unit set_buyer_role`.
6. **Configure the shops:** `/shop add` → add personal products → `/shop publish`. Repeat with `/unitshop add` and `/unitshop publish`. Members click through a product menu; staff fulfill completed orders via `/shop orders`/`/shop fulfill` or `/unitshop orders`/`/unitshop fulfill`.
7. **Publish the live rankings:** `/ledger refresh_top_earners`. The bot updates this message every 30 minutes and after contract payouts.
8. **Post a test contract:** `/contract post` → complete the pop-up → **add zero to four bonus objectives** via the draft menu → Publish. Verify it appears in `#black-market-contracts`.
9. **Proof test:** Click Claim, then Upload Proof. Supply a readable end-of-mission statistics screenshot, planet, and difficulty. **After upload**, use the private squad-linking panel to tag up to three server members, then type the in-game name shown for each. Optionally enter your own game name. Choose **Link Names & Verify**, or **Verify Solo**. The matching screen has a 10-minute timeout; if it expires, resubmit the screenshot. The image is not processed for payout until this step is finished. Clear OCR PASS -> base reward; bonuses require readable OCR values. Duplicate or unclear screenshots -> staff review. Check `/profile` and `/stats me`.
10. **Audit:** `/contract pending` to see queued reviews; moderators may approve their own submissions as requested, but approvals are recorded in transaction history. Start with modest rewards until OCR accuracy is known.

**If you moved from Windows to Railway:** stop Windows LEDGER before running Railway, and transfer the original `ledger.sqlite3` onto `/data/ledger.sqlite3`. Never run both copies with one token.

11. **Contractor name links:** Once paid, the uploader-reported in-game handles appear in `/profile`. Recipients are mentioned in `#black-market-contracts` only after the payment clears. If someone disputes a link, a moderator can run `/ledger unlink_player attempt_id:<number> member:@person`. Removing a link does not undo credits already issued.


## New: Adopt a Contractor mentorship setup

1. Register your unit roles using `/unit setup` and any extra membership roles using `/unit link_menu`. LEDGER recognizes current role holders immediately.
2. Create a private `#mentor-dispatch` channel. Give the LEDGER bot and your leadership roles permission to View Channel, Send Messages, Read Message History (LEDGER), and optionally Embed Links. Hide it from `@everyone`.
3. `/mentor setup` opens a two-dropdown panel. Select the dispatch channel and all higher officer roles. `/mentor set_lead` connects a unit's existing leadership role for pings (repeat for each unit and role).
4. `/mentor terms reward:750 required_sessions:2` sets the reward for NEW training requests. Change the value to suit your economy.
5. `/mentor publish channel:#blacksite-terminal` posts an optional public request button. The permanent hub already has an **Adopt a Contractor** button.
6. A trainee uses `/mentor request` and supplies their experience, goals and availability. The request card appears privately; the selected unit lead and senior officer roles are notified.
7. An officer selects a volunteer using the case card's **Assign Mentor** button (or `/mentor assign`). The selected mentor runs `/mentor accept request_id:<case>`.
8. After each training session, the mentor runs `/mentor session request_id:<case> summary:<what was taught>`. The trainee receives a DM button to confirm attendance, or uses `/mentor confirm session_id:<id>`.
9. After the required confirmed sessions, the trainee uses `/mentor finish request_id:<case>`. An officer other than mentor or trainee presses **Clear Contract** or runs `/mentor approve request_id:<case>`. The mentor receives one Blacksite Credits payout and the case is archived as PAID.
10. Optional **full automatic roster sync**: enable *Server Members Intent* for LEDGER in the Discord Developer Portal and set `LEDGER_MEMBER_SYNC=1` in Railway Variables. Otherwise role-based eligibility still works automatically whenever a member interacts with LEDGER, but the full roster index won't refresh continuously.
