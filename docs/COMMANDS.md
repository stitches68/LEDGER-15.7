# LEDGER — complete slash command reference

Commands documented from the actual `bot.py` decorators: **100**.

Permissions are checked by Discord and LEDGER; shop/contract management generally requires the configured LEDGER Market Moderation role or Manage Server. Mentor commands have their own officer/participant checks.

## First things to know

- `/hub` — interactive contact terminal for members.
- `/ledger channels` — assign the five standard channels.
- `/ledger publish_dispatch` — publish the continuously edited, dark-green Blacksite contract progress board.
- `/contract post` — staged private popup to create contracts, set evidence mode and public progress goal, and add bonuses.
- `/contract submit` — native screenshot upload followed by player→Discord dropdown assignment.
- `/dossier edit` — customize callsign, specialty, biography, and motto.
- `/mentor request` — request an officer-assigned mentor; public terminal calls this **Find a Mentor**.
- `/intel` — original lore-only unlisted Blacksite transmissions.

---

## General & contractor

| Command | Description |
|---|---|
| `/balance` | Check a member’s Blacksite Credits |
| `/payments` | Show recent account activity including earnings and purchases |
| `/leaderboard` | Show the ten richest contractors |
| `/hub` | Open the Blacksite contract terminal (private dashboard) |
| `/profile` | Open a contractor dossier with Discord profile, credits and stats |
| `/submit-mission-stats` | Upload post-mission stats and link visible player cards to Discord members |
| `/submit-operation` | Upload a completed operation scorecard for a Blacksite contract |
| `/intel` | Intercept an unlisted Blacksite transmission |

## Blacksite administration

| Command | Description |
|---|---|
| `/ledger create_mod_role` | Create the LEDGER Market Moderation role (server manager only) |
| `/ledger set_mod_role` | Authorize an existing role to moderate LEDGER |
| `/ledger install_credit_icon` | Upload the supplied Blacksite Credits artwork as a server emoji |
| `/ledger set_credit_icon` | Use an existing server emoji as the Blacksite Credits icon |
| `/ledger channels` | Configure all five LEDGER channels using dropdown menus |
| `/ledger refresh_top_earners` | Refresh the public Top Earners board immediately |
| `/ledger ocr_status` | Check whether local screenshot OCR is installed and ready |
| `/ledger publish_dispatch` | Create or refresh the live Blacksite contract progress board |
| `/ledger refresh_dispatch` | Force-refresh the live contract dispatch board |
| `/ledger settings` | Show LEDGER channels and moderator role |
| `/ledger unlink_player` | Remove a disputed uploader-reported game-name association |
| `/ledger publish_hub` | Publish or relocate the public Blacksite terminal |
| `/ledger guide` | Quick guide to the Blacksite network |

## Channel setup

| Command | Description |
|---|---|
| `/board set_channel` | Open LEDGER channel configuration dropdowns |
| `/board set_review_channel` | Open LEDGER channel configuration dropdowns |

## Contracts & progression

| Command | Description |
|---|---|
| `/contract post` | Open a popup to create a Black Market contract |
| `/contract status` | Show an open contract and the number of paid recipients |
| `/contract claim` | Claim an open contract independently of other contractors |
| `/contract submit` | Upload proof, then link in-game players to Discord teammates |
| `/contract pending` | List pending contract completions (moderators) |
| `/contract public_goal` | Moderator: set public community target for live dispatch (0 to disable) |
| `/contract evidence_type` | Moderator: choose STATS, OPERATION, or BOTH proof types |
| `/contract approve` | Approve and pay screenshot submitter plus requested teammates |
| `/contract reject` | Reject a completion so the recipient can resubmit |
| `/contract progress` | View your verified missions for a contract |
| `/contract configure_progress` | Staff: set required screenshot count and repeatable terms |
| `/contract close` | Stop new submissions for a contract (unpaid awards remain paid) |
| `/contract reopen` | Reopen a previously closed repeatable contract |
| `/contract objectives` | Inspect a contract and its optional paid bonuses |
| `/contract bonus_add` | Add a screenshot-tracked bonus before any payments occur |

## Units & rosters

| Command | Description |
|---|---|
| `/unit setup` | Select multiple existing roles to create separate units automatically |
| `/unit link_menu` | Link additional existing membership roles using dropdowns |
| `/unit register` | Register a Discord role as a unit with its own treasury |
| `/unit link_role` | Pair an existing Discord member role with a registered unit |
| `/unit unlink_role` | Remove a linked membership role from a unit |
| `/unit roles` | View the Discord roles currently identifying unit members |
| `/unit check_member` | Show which registered units a contractor belongs to via Discord roles |
| `/unit set_buyer_role` | Authorize unit leadership to spend its treasury in the unit shop |
| `/unit list` | List registered units and treasuries |
| `/unit balance` | Check the shared balance for a registered unit |
| `/unit roster` | View automatically matched members of a unit |
| `/unit sync_roster` | Rebuild automatic unit rosters from Discord server roles |

## Blacksite Credits

| Command | Description |
|---|---|
| `/credits add` | Moderation team: credit a member or unit treasury |
| `/credits subtract` | Moderation team: deduct credits from a member or unit |

## Contractor Market

| Command | Description |
|---|---|
| `/shop set_channel` | Open LEDGER channel configuration dropdowns |
| `/shop publish` | Publish the current shop catalog in its configured channel |
| `/shop add` | Add a purchasable listing to the shop |
| `/shop edit` | Update an existing shop listing |
| `/shop remove` | Remove a listing from sale without deleting purchase receipts |
| `/shop stock_add` | Increase an item’s inventory |
| `/shop stock_subtract` | Decrease an item’s inventory |
| `/shop stock_set` | Set stock to a finite quantity, or -1 for unlimited |
| `/shop browse` | Browse the Black Market Exchange |
| `/shop buy` | Purchase a listing using Blacksite Credits |
| `/shop orders` | List outstanding shop purchases for manual fulfillment |
| `/shop fulfill` | Mark a shop purchase as manually delivered |

## Unit Supply Exchange

| Command | Description |
|---|---|
| `/unitshop set_channel` | Open LEDGER channel configuration dropdowns |
| `/unitshop publish` | Publish the Unit Supply Exchange catalog |
| `/unitshop add` | List an item available only to unit treasuries |
| `/unitshop edit` | Change a Unit Supply Exchange listing |
| `/unitshop remove` | Remove a listing from the Unit Supply Exchange |
| `/unitshop stock_add` | Increase unit-shop inventory |
| `/unitshop stock_subtract` | Decrease unit-shop inventory |
| `/unitshop stock_set` | Set unit item stock; -1 means unlimited |
| `/unitshop browse` | View items available for unit treasury purchase |
| `/unitshop buy` | Use a unit treasury to purchase supplies (authorized buyers only) |
| `/unitshop orders` | Show pending Unit Supply Exchange orders |
| `/unitshop fulfill` | Mark a unit order as delivered |

## Career statistics

| Command | Description |
|---|---|
| `/stats me` | Show your tracked mission screenshot statistics |
| `/stats member` | Show screenshot statistics submitted by a member |
| `/stats unit` | Show mission stats for paid unit contracts |
| `/stats server` | Show aggregate server contract verification statistics |
| `/stats diagnose` | Explain whether your screenshot OCR stats were recorded and linked |
| `/stats recent` | View your five most recent screenshot checks |

## Find a Mentor

| Command | Description |
|---|---|
| `/mentor setup` | Configure private mentorship dispatch and higher-officer roles |
| `/mentor terms` | Set credit payout and number of trainee-confirmed sessions |
| `/mentor set_lead` | Link a unit leadership role for mentor request notifications |
| `/mentor request` | Newer contractors: ask to be assigned a mentor |
| `/mentor publish` | Publish an Adopt a Contractor request button in a channel |
| `/mentor assign` | Unit lead or officer: appoint a mentor to a trainee |
| `/mentor accept` | Assigned mentor: accept the training contract |
| `/mentor decline` | Assigned mentor: decline if you cannot train this contractor |
| `/mentor unassign` | Officer: release an untrained mentor assignment for reassignment |
| `/mentor session` | Mentor: log one training session for trainee confirmation |
| `/mentor confirm` | Trainee: confirm an actual training session |
| `/mentor finish` | Trainee: confirm required training and request officer sign-off |
| `/mentor approve` | Officer: approve completed training and pay the mentor |
| `/mentor queue` | Unit lead or staff: view outstanding mentor cases |
| `/mentor status` | See your ongoing mentorship and training session IDs |
| `/mentor cancel` | Trainee or officer: close a mentorship without payout |

## Contractor dossiers

| Command | Description |
|---|---|
| `/dossier edit` | Customize your callsign, specialty, bio, and motto |
| `/dossier view` | View a contractor’s Blacksite service record |

## Evidence rules

Contracts can require `STATS`, `OPERATION` or `BOTH` screenshots. The per-player and operation OCR recognizers are separate. In BOTH mode two images count as one report. Some screenshots may need moderator review; unreadable stats are not invented.

For deployment and the first 15-minute setup, read [START_HERE.md](START_HERE.md).
