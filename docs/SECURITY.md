# Security, privacy, and safe operations

- **Currency:** Blacksite Credits are fictional in-server tokens, not real currency, crypto, or a payment processor.
- **Secrets:** Do not commit `.env`, Discord tokens, or private SQLite databases. Use Railway Variables for the deployed token.
- **SQLite:** Only run one LEDGER instance/replica. Mount the Railway volume at `/data`, set `DATABASE_PATH=/data/ledger.sqlite3`, enable Railway volume backups, and periodically make independent SQLite-consistent backups.
- **Migration:** Stop the old bot before moving its database. Never overwrite a live database that has new transactions without first backing it up and reconciling the histories.
- **Moderation:** Market moderators can make manual adjustments, edit shops, approve/reject submissions, and approve their own submissions. All payouts have ledger transactions and reviewer IDs; use trusted staff and audit unusual activity.
- **Evidence:** OCR locally processes uploaded image bytes. For uncertain/duplicate images, LEDGER posts the screenshot in the staff-only review channel. The screenshot may contain in-game names; keep that channel private.
- **Limitations:** Tesseract only recognizes text. It does not connect to Helldivers servers and cannot authenticate a mission. Planet/difficulty in the form are declarations, not verified screenshot stats. Only unambiguous screenshot stats are used for bonus thresholds and performance rankings.
- **Attribution:** Kills and stims rankings use OCR numeric player cards, attributed ONLY through the uploader-selected left-to-right card position. OCR name guesses are not identity verification. Legacy links fall back to exact names. Unlinked or ambiguous player totals never count toward a member.
- **Abuse controls:** One-time contracts pay once per recipient. Repeatable contracts pay once per completed cycle; every cycle requires new proof. Exact and near-duplicate screenshot detection flags repeat submissions for staff; it is not foolproof against edits.
- **Safety:** Restrict `#contract-reviews` from `@everyone`, and ensure LEDGER's bot account can still View, Send, Attach, and Embed there.

- **Uploader-declared player links:** In-game names paired with Discord users come from the uploader, NOT an authenticated game account. These links are per-screenshot annotations and are displayed in profiles only when the screenshot is PAID. Mods can remove a disputed link via `/ledger unlink_player`; this does not reverse transactions. Automatic team payouts still rely on uploader honesty.
- **Payout mentions:** The bot only uses targeted user mentions for recipients after the database confirms a successful payout; it never pings `@everyone` or roles for this event.
- **Upload linking timeout:** Post-upload evidence is held in memory for up to 10 minutes while the uploader selects teammates. Restarting LEDGER during this step requires a new upload, without debit or claim being recorded.


## Mentorship permissions and anti-abuse controls

Training goals and availability are posted only to a private dispatch channel; trainees can check their own case with `/mentor status`. Leads may manage requests for their linked unit; senior officer roles and LEDGER moderators can manage all cases. Program configuration is limited to market moderators or server managers.

A mentor must opt in, may not mentor themselves, and cannot log another training session until the trainee confirms the prior one. Only the trainee can confirm or finish sessions. A separate authorized officer must approve any payout (no self-approval by mentor or trainee), which is recorded exactly once via a unique ledger source/reference. Cancelling does not issue credits. No real-world money is involved.

Full unit roster synchronization requires the privileged Discord Members Intent and optional `LEDGER_MEMBER_SYNC=1`. This feature stores guild ID, unit role ID and user ID in `unit_roster`; the user's actual server roles remain authoritative.
