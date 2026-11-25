# ADOPT A CONTRACTOR — Program playbook

## What trainees experience

The Blacksite terminal has **Adopt a Contractor**, or members run `/mentor request`.
A private intake form asks for experience, what they want to learn, and availability.
If they belong to one registered unit, that unit is selected automatically. If
several, they can select one or General. LEDGER does not need to modify their roles.

Requests appear in your private mentor-dispatch channel. Unit leads and senior
officers are mentioned (provided they can view the channel).

## Officer setup

- `/mentor setup`: private dispatch channel and senior officer role multi-select.
- `/mentor set_lead unit:@ANGEL lead_role:@ANGEL-Leadership`: register unit-lead roles, repeat per unit.
- `/mentor terms reward:750 required_sessions:2`: set reward (1–10,000,000 BC) and session count (1–10), snapshotted per new request.
- `/mentor publish channel:#mentor-requests`: optional public button. The main `/hub` already links here.
- `/mentor queue`: queue of relevant active cases.

Ensure the channel hides `@everyone` and grants your configured leadership roles
View Channel, plus bot permissions to write messages and embeds. If a role is
not mentionable and LEDGER cannot mention roles, adjust role/channel settings.

## Paid training contract criteria

1. An officer/lead assigns a willing mentor with the case card's button or `/mentor assign request_id:<id> member:@mentor`.
2. Mentor accepts: `/mentor accept request_id:<id>`.
3. Mentor logs a real session: `/mentor session request_id:<id> summary:<notes>`.
4. Trainee clicks **Confirm I attended** in a DM or uses `/mentor confirm session_id:<id>`. Unconfirmed sessions do not count.
5. Repeat until the configured minimum (default 2) is confirmed.
6. Trainee finishes: `/mentor finish request_id:<id>`.
7. A separate officer checks the case and clicks **Clear Contract** or `/mentor approve request_id:<id>`. Full configured reward is transferred to the mentor's personal wallet once.

A moderator may approve their own ordinary screenshot contract under existing
LEDGER rules, but **cannot approve their own mentor-training payout**. This
protects training credits from single-person fabrication.

## Useful commands

`/mentor request`, `/mentor status`, `/mentor accept`, `/mentor session`,
`/mentor confirm`, `/mentor finish`, `/mentor cancel`, `/mentor assign`,
`/mentor approve`, `/mentor queue`, `/mentor setup`, `/mentor terms`,
`/mentor set_lead`, `/mentor publish`.

`/unit roster`, `/unit sync_roster` inspect the optional background roster.
Current unit eligibility is always computed from actual Discord roles, even
without the Members Intent.

## Deploy safely

Upload `mentorship.py`, updated `bot.py`, updated `storage.py`, and Dockerfile
along with the rest of the project. Keep the previous Railway `/data` volume
and `DATABASE_PATH=/data/ledger.sqlite3`. Back up the live database first.

If enabling real-time roster events, toggle the **Server Members Intent** in
Discord Developer Portal > Bot and then add `LEDGER_MEMBER_SYNC=1` in Railway
service Variables. Redeploy.

## If someone cannot mentor

The assigned mentor can use `/mentor decline request_id:<id>` before any confirmed sessions.
An officer can use `/mentor unassign request_id:<id>` to return the case to the open
queue. This deletes only unconfirmed training notes; once a trainee has confirmed
training, the case must be completed with the same mentor or cancelled and restarted.
