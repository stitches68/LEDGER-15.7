# Mission stat tracking: individual Helldivers results screens

This version recognizes the **three side-by-side player columns** in the Helldivers 2 end-of-mission stats view. It does not read game data from the game itself; it only examines submitted screenshots using Tesseract OCR on Railway. It cannot verify that an image is authentic or that someone played the pictured mission.

## Captured data

Each visible, legible player column can supply:

| Statistic | How it is tracked |
| --- | --- |
| Player name | Unverified OCR suggestion, informational only; Discord assignment uses card position |
| Kills | Numeric OCR reading in that player's column |
| Accuracy | Recomputed from OCR-read shots hit / shots fired when available |
| Shots fired / shots hit | Separately read numeric fields |
| Deaths / stims used | Separately read numeric fields |
| Accidentals | Numeric OCR field if readable |
| Samples extracted | Numeric OCR field if readable |
| Stratagems used | Numeric OCR field if readable |
| Melee kills | Numeric OCR field if readable |
| Times reinforcing | Numeric OCR field if readable |
| Friendly fire damage | Numeric OCR field if readable |

Poorly recognized fields are **omitted**. Never treat an unreadable value as zero or estimate it from another teammate. The screenshot uploader enters planet and difficulty separately; these are reported information, not verified by OCR. Some UI changes, display scaling and effects may prevent reliable recognition. Moderators should review suspicious results.

## Linking each player to Discord

After screenshot upload, choose up to three teammate Discord accounts and enter each person's in-game name. Enter **your own in-game name** even on a solo submission. Stats are assigned only when the entered name uniquely matches a player column on that screenshot after ignoring case, spaces and punctuation. A wrong or ambiguous name gets no automatic personal stat attribution; it does not magically become your teammates' statistics. The uploader-reported link is not independent identity verification.

## Multi-mission contracts

Moderators set **Mission Count / Repeat** in the private contract draft. A mission-count contract may require between 1 and 25 accepted screenshots for a single payout. A contractor can view current progress with `/contract progress`. Every screenshot must be different; duplicate-image detection is global. One accepted image contributes **one mission**, even if it contains three players.

Optional sub-objectives are checked on the **cumulative recognized numbers for each paid contractor** once that contractor reaches the required mission count. For example, a contract requiring three screenshots and 600 kills pays its 600-kill bonus only if *that contractor's linked OCR player stats* total at least 600 across the accepted images. Maximum-style objectives such as 0 deaths only qualify if deaths are readable on each required screenshot.

If the contract is marked **repeatable**, payment completes cycle 1 and the contractor can claim it again to begin cycle 2. Prior screenshots cannot be reused to skip progress. For one-time contracts, the member is done after the first completed cycle. Progress audit rows, transactions and image history survive Railway restarts in the persistent `/data` volume.

## Notes

- Automatic base payouts still require a recognizable results screen and a screenshot that is not flagged as a duplicate. Staff can manually inspect flagged images. OCR and file hashes cannot prove authentic gameplay.
- Other teammates may receive credits as tagged recipients, but their **personal stats and optional bonuses** rely on their individually mapped player columns.
- `profile`, `/stats member`, the kills and stims leaderboards draw only from correctly attributed OCR facts. No per-squad totals are copied across all teammate profiles.
- Back up the SQLite database before upgrading the Railway deployment.
