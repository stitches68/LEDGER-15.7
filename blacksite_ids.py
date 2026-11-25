"""Human-readable dispatch references; slash commands still use numeric IDs.

Do not use reference strings as database primary keys. The unpadded integer
stays the authoritative contract_id or request_id used by Discord commands.
"""
from __future__ import annotations


def contract_ref(contract_id: int) -> str:
    if int(contract_id) < 1:
        raise ValueError('Contract ID must be positive')
    return f'BMC-{int(contract_id):06d}'


def mentor_ref(request_id: int) -> str:
    if int(request_id) < 1:
        raise ValueError('Mentorship ID must be positive')
    return f'MTR-{int(request_id):06d}'


def contract_id_banner(contract_id: int) -> str:
    return (f'**CONTRACT ID:** `{int(contract_id)}`  •  '
            f'**FILE:** `{contract_ref(contract_id)}`\n'
            f'*Use `{int(contract_id)}` as `contract_id` in slash commands.*')


def mentor_id_banner(request_id: int) -> str:
    return (f'**MENTOR REQUEST ID:** `{int(request_id)}`  •  '
            f'**FILE:** `{mentor_ref(request_id)}`\n'
            f'*Use `{int(request_id)}` as `request_id` in `/mentor` commands.*')
