# Optional contract objectives

## Workflow

Run `/contract post`. The first popup sets title, description, base Blacksite Credits reward, and whether the contract pays individuals or a unit. LEDGER then shows a private **draft** (not yet posted to a channel).

Use **Add Bonus** to enter a label, a screenshot metric, a numeric target, and the bonus Blacksite Credits. Repeat up to four times, use **Undo Bonus** if needed, optionally select a target unit, then **Publish Contract**. The contract card shows the base reward, each optional objective, and maximum potential payout.

## Supported metrics

| Metric | Requirement | Example |
|---|---|---|
| Enemy kills | At least | 200 kills pays +125 BC |
| Stims used | At least | 12 stims pays +75 BC |
| Samples collected | At least | 10 samples pays +100 BC |
| Shots fired | At least | 1,000 shots pays +40 BC |
| Shots hit | At least | 500 hits pays +40 BC |
| Stratagems used | At least | 8 stratagems pays +90 BC |
| Deaths | At most | 0 deaths pays +150 BC |

**Each bonus must be greater than zero and smaller than the base reward.** Bonuses are optional, additive, and independent; multiple bonuses can pay together.

## How credits are issued

- OCR must recognize a post-mission results screen to auto-pay the base reward.
- For each objective, OCR must extract the required **recipient-specific** numeric statistic from the linked player column. Missing or ambiguous values do **not** count toward that objective.
- At payout, bonuses are evaluated separately for each recipient using the figures attributed to that recipient. Missing player name mappings cannot unlock personal stat bonuses. A unit treasury can use the aggregate of recognized player columns on that submission.
- An OCR-UNCERTAIN screenshot goes to staff review; manual approval pays the base, but bonuses are awarded only from a PASS screenshot with readable matching values.
- One-time contracts cannot pay twice. Repeatable contracts can pay again only after a *new completed cycle* with distinct screenshots.
- Staff may run `/contract bonus_add contract_id:...` to add a bonus to an existing contract **only before the first proof submission**. `/contract objectives` shows the current list.

## Risk and fairness

OCR does not prove game authenticity. Screenshots can be altered or reused, and the game UI may change. Strongly similar images are sent to review instead of automatically paid. For high-value contracts, consider manual scrutiny even after OCR success. Maintain moderator discretion and log all payouts.


## Multi-mission and repeatable contract rules

When an officer uses **Mission Count / Repeat** in the contract draft (or `/contract configure_progress` before the first upload), LEDGER stores a requirement of **1–25 distinct accepted screenshots** per completion. For these contracts, base pay is withheld until all accepted submissions are counted, and qualifying bonuses use the **accumulated stat values attributed to each recipient's linked game name**. Repeatable contracts open a new completion cycle after the previous one pays. Attempts and transactions remain auditable across cycles.

Bonuses are per recipient, not a blanket squad total. For example, a contractor whose matching in-game column reports 350 kills over three missions may earn a 300-kill bonus; teammates only earn the same bonus if their own recognized columns meet that target. For at-most constraints such as zero deaths, the applicable field must be readable on **every** screenshot in the cycle. Unreadable fields never count as zero or unlock unsupported bonuses.

A suspected duplicate, unreadable screenshot or unclear name stays subject to moderator review. OCR does **not** prove mission authenticity or account identity.
