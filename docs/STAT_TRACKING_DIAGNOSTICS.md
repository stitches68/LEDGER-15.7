# LEDGER — Screenshot statistics diagnostics (Card Position Edition)

Use `/stats diagnose` after a submission to see the accepted proof, OCR-extracted numbers, the card positions (left-to-right) and the Discord members assigned to them.

- **New submissions:** Player-card position is the authoritative assignment. OCR nameplate text is an **unverified guess**, NOT a match requirement. The uploader selects the Discord member who belongs to each results card. Unassigned cards are external or non-faction and contribute no member statistics or payout.
- **Name unclear, numbers readable:** The card can still be assigned. Link by position and visible stats, not guessed text.
- **Numbers unreadable:** The card remains visible, but automatic stats credit requires a clearer results screenshot. Uncertain proof goes to review.
- **PASS / PAID:** The selected card's readable stats count toward the member dossier and kills/stims leaderboard, provided the screenshot was accepted. A screenshot with several linked Discord members counts as a single mission in community progress.
- **UNCERTAIN / staff-approved:** Credits may pay, but unclear numbers do not automatically become verified career statistics.
- **Legacy screenshots:** Older submissions without saved card positions still use the previous exact OCR-name matching rules. Existing credits and stats are preserved; incorrect legacy mappings are not blindly reassigned.

**Why this matters:** Tesseract frequently misreads small HD2 in-game usernames on translucent animated backgrounds (e.g. `Taam` instead of `Tama`). These OCR guesses no longer control who receives the statistics.

This system relies on uploader-declared Discord assignments and cannot authenticate player identity or screenshot authenticity. Moderators should review suspected abuse or anomalous high-value submissions.
