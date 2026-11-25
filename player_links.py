"""Input validation for uploader-reported HD2 player-name ↔ Discord account links.

These links are an accounting annotation, NOT proof of participant identity.
"""
from __future__ import annotations

from typing import Mapping


class PlayerLinkError(ValueError):
    pass


def clean_player_name(value: str) -> str:
    if not isinstance(value, str):
        raise PlayerLinkError('Player name must be text.')
    name = ' '.join(value.strip().split())
    if not 2 <= len(name) <= 64:
        raise PlayerLinkError('In-game names must be between 2 and 64 characters.')
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise PlayerLinkError('In-game names cannot contain control characters.')
    return name


def validated_links(submitter_id: int, teammate_ids: list[int],
                    names_by_discord_id: Mapping[int, str] | None) -> list[tuple[int, str]]:
    """Produce a stable unique mapping, disallowing undeclared payout recipients."""
    teammates = list(teammate_ids)
    if len(teammates) > 3 or len(set(teammates)) != len(teammates) or submitter_id in teammates:
        raise PlayerLinkError('Select at most three distinct teammates (not yourself).')
    allowed = {submitter_id, *teammates}
    provided = names_by_discord_id or {}
    if set(provided) - allowed:
        raise PlayerLinkError('Only the uploader and selected teammates may be linked.')
    missing = [uid for uid in teammates if not str(provided.get(uid, '')).strip()]
    if missing:
        raise PlayerLinkError('Provide an in-game name for every tagged teammate.')
    cleaned: list[tuple[int, str]] = []
    used_names = set()
    for uid in [submitter_id, *teammates]:
        raw = str(provided.get(uid, '')).strip()
        if not raw:
            continue  # Uploader's own player name is optional.
        handle = clean_player_name(raw)
        if handle.casefold() in used_names:
            raise PlayerLinkError('Each selected member needs a different in-game name.')
        used_names.add(handle.casefold())
        cleaned.append((uid, handle))
    return cleaned
