# OCR, player cards, and contract reporting

LEDGER uses **local Tesseract OCR** installed in its Docker image. No OpenAI API key is used. It does **not** inspect actual Helldivers game telemetry or prove screenshots authentic.

## The two evidence formats

- **STATS:** Full, preferably uncropped **1080p+** end-of-mission stats cards showing player names and columns for kills, accuracy, shots, deaths, stims, samples, and other visible numbers. The user-provided reference image with three player cards was verified locally with extracted kills 210 / 223 / 711 and stims 3 / 2 / 11. A 2–4-player parser is implemented, but only the three-player reference has been checked against a real full-size game screenshot. The OCR cannot guarantee all fields will be read, especially yellow highlighted digits and low-resolution crops.
- **OPERATION:** Operation completion/results screenshot with operation status; may contain planet, difficulty, and completed mission indicators, but no per-player stats. Automated recognition requires clear operation-specific completion context. Actual in-game completion images still need calibration; inconclusive screens go to review.
- **BOTH:** Attach a **STATS** screenshot plus an **OPERATION** screenshot in the same upload. They represent **one reported mission**, not two. Both must be readable for a fully automatic pass; otherwise the case goes to moderators.

## What happens after upload

1. LEDGER decodes and checks file type/size and creates a visual fingerprint.
2. Tesseract reads the relevant image(s) and extracts only explicit fields it can recognize.
3. The submitter sees an ephemeral **Assign Player Statistics** panel with the real screenshot preview and one optional dropdown for **each detected player card (up to four)**. All cards are shown even if some belong to nonmembers. If a name cannot be read but the numbers are clear, the card is still assignable by its physical position. Only cards with unreadable numeric statistics have disabled selectors.
4. The submitter assigns **only faction/server players** to Discord accounts. **Leaving a dropdown blank explicitly means outside the Discord server or faction**; there is no need to fill all four. External players remain on the preview/evidence record but get no member payouts, personal stats, or faction kill/stim totals. Names are OCR suggestions only; a card can be linked using its left-to-right position when its numbers are readable. These mappings are declared by the uploader, not identity-verified.
5. The bot records the evidence and screens it for duplicates. A clear qualifying result can auto-pay; duplicates/uncertain results go to the private moderator review queue. Rejects do not pay.
6. A successful accepted proof contributes to contract progress. Personal stats are stored against **the physical player-card position selected by the uploader**, not arbitrarily split among teammates. Group payouts and individual stat attribution are intentionally separate.

**If stats are missing:** Run `/stats diagnose` and look for unreadable card statistics or missing card-position assignments. Then check `/stats me` and `/profile`. A manual approval can pay a contract even when no stats could be confidently extracted; do not assume missing stats are zero.

## Contract editor options

- **Required missions:** 1–25 separate accepted screenshot submissions per completion.
- **Repeatable:** If enabled, a new completion cycle opens after payout. The same proof cannot be reused to advance it.
- **Optional bonus objectives:** Up to four OCR-based bonus thresholds for supported fields. Missing/unreadable values cannot satisfy a bonus.
- **Public goal:** A non-financial display target for the live Blacksite Dispatch channel; counts accepted distinct screenshots from the contract, not the number of tagged members.

## OCR testing and honest limitations

You can check the installed engine with `/ledger ocr_status`. To validate numerical extraction end-to-end, perform a known screenshot submission and check the values shown **before** pressing Confirm & Submit, then use `/stats diagnose`. More full-size screenshots from the actual game, including operation results and 2/4-player screens, are necessary for reliability improvements. **No image-only technique proves gameplay genuinely occurred**, so consider moderator reviews for high-value awards.

## Blank player slots, payouts, and review

- **Individual contract:** The submitting contractor must be linked to a readable player card before LEDGER will release a personal screenshot-based payout. A blank uploader does **not** receive an automatic payment; the case goes to staff review. Staff approval also refuses to pay a member whose readable player card was left blank.
- **Other linked Discord members:** Only explicitly assigned teammates are included as potential personal recipients. One member cannot be linked to two player slots on the same submission.
- **Unit contract:** Can count a valid squad screenshot for a unit treasury even when none of the pictured players has a Discord association. No individual career stats are fabricated for external players.
- **Incomplete OCR:** If OCR can infer the number of player panels but not read every player, LEDGER lists the unreadable slots and routes uncertain verification to staff. The full, uncropped high-resolution game screenshot gives the best results.
