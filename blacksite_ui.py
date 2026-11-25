"""Visual identity and readable Discord embeds for LEDGER's black market contact.

Discord components are the working interface; this module only draws messages.
No special channel/role names are required for the fiction to make sense.
"""
from __future__ import annotations

import discord
from contract_objectives import requirement_text
from blacksite_ids import contract_ref, contract_id_banner

INK = 0x0E1713
GREEN = 0x7BDF16
TEAL = 0x64C967
GOLD = 0xA9D956
RED = 0xE96B6B
BLUE = 0x397A4B
GREY = 0x727E79


def base(title: str, body: str = '', *, color: int = GREEN) -> discord.Embed:
    e = discord.Embed(title=title, description=body, color=color,
                      timestamp=discord.utils.utcnow())
    e.set_footer(text='LEDGER // BLACKSITE NETWORK  •  A reputation bought in blood, paid in credits')
    return e


def field_lines(rows, fallback='No intel on file.') -> str:
    return '\n'.join(rows)[:1024] if rows else fallback


def contract_card(row, objectives, progress, fmt_money) -> discord.Embed:
    g = row['guild_id']
    claimed,pending,paid = progress
    open_ = row['status']=='OPEN'
    body = (contract_id_banner(row['id']) + '\n\n' +
            f'**CONTACT DISPATCH**  •  {"🟢 OPEN FOR BUSINESS" if open_ else "🔒 CONTRACT CLOSED"}\n'
            f'{discord.utils.escape_markdown(row["details"][:1500])}\n\n'
            'The broker accepts mission intel, verified with offline OCR. '
            'Optional objectives reward cumulative, attributed stats across the required screenshots.')
    e=base(f'▰ BLACK MARKET / {contract_ref(row["id"])}  •  {row["title"]}',body,
           color=GREEN if open_ else GREY)
    e.add_field(name='▸ BASE PAYMENT',value=f'**{fmt_money(g,row["reward"],full=True)}**')
    dest=('Each eligible contractor' if row['payout_mode']=='USER' else
          f'Unit treasury: <@&{row["target_unit_id"]}>' if row['target_unit_id']
          else 'Any registered unit treasury')
    e.add_field(name='▸ PAID TO',value=dest)
    required=row['required_submissions']
    repeat='REPEATABLE • new cycle after payout' if row['repeatable'] else 'ONE-TIME'
    proof_mode=row['proof_mode'] if 'proof_mode' in row.keys() else 'STATS'
    e.add_field(name='▸ EVIDENCE FORMAT',value={
        'STATS':'Individual post-mission statistics',
        'OPERATION':'Completed operation summary',
        'BOTH':'Stats screenshot + operation summary in one submission',
    }.get(proof_mode,'Individual post-mission statistics'),inline=False)
    goal=row['public_goal'] if 'public_goal' in row.keys() else 0
    if goal:
        e.add_field(name='▸ PUBLIC CONTRACT TARGET',value=f'**{goal:,}** unique accepted mission submissions',inline=False)
    e.add_field(name='▸ OPERATION TERMS',
        value=f'**{required}** accepted mission screenshot(s) per payout\n**{repeat}**',inline=False)
    bonus_lines=[f'`+{r["reward"]:,}` • **{discord.utils.escape_markdown(r["title"])}** — {requirement_text(r)}'
                 for r in objectives]
    e.add_field(name=f'▸ OPTIONAL BONUS OBJECTIVES ({len(objectives)})',
                value=field_lines(bonus_lines,'No optional bonuses posted.'),inline=False)
    e.add_field(name='▸ INTEL',value=('OCR reads the uploaded screenshot. Unreadable bonus stats '
                'do **not** earn bonus credits. Verified mission bonuses apply to every '
                'recipient paid from that submission. Duplicate/unclear proof goes to staff.'),inline=False)
    e.add_field(name='▸ OPERATIONS LOG',
                value=f'`{claimed}` claimed  ·  `{pending}` pending  ·  `{paid}` paid',inline=False)
    return e


def profile_card(member:discord.Member, data:dict, units:list, fmt_money, guild_id:int,
                 reported_handles:list|None=None) -> discord.Embed:
    """Player-focused black-market dossier, not a dump of Discord account metadata."""
    card=data.get('card') or {}
    def safe(value,limit=160):
        return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value or '')))[:limit]
    callsign=safe(card.get('callsign') or member.display_name,36)
    specialty=safe(card.get('specialty') or 'Independent Contractor',50)
    e=base(f'▰ BLACKSITE PERSONNEL / {callsign}',
           f'**{specialty}**  •  <@{member.id}>\n'
           f'{safe(card.get("motto") or "Your reputation is the only credential the broker needs.",120)}',color=GREEN)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name='▸ BLACKSITE WALLET',value=f'**{fmt_money(guild_id,data["balance"],full=True)}**',inline=True)
    e.add_field(name='▸ CONTRACT EARNINGS',value=f'**{fmt_money(guild_id,data["earned"])}**',inline=True)
    stats=data['stats']; totals=stats['totals']; observations=stats['available']
    lines=[f'**{stats["paid"]:,}** accepted mission proof(s)',
           f'**{totals.get("kills",0):,}** confirmed kills',
           f'**{totals.get("stims_used",0):,}** stims used',
           f'**{totals.get("deaths",0):,}** deaths',
           f'**{totals.get("shots_fired",0):,}** shots fired',
           f'**{totals.get("samples_collected",0):,}** samples collected']
    e.add_field(name='▸ SERVICE RECORD',value='\n'.join(lines),inline=False)
    if units:
        e.add_field(name='▸ UNIT AFFILIATIONS',value=' '.join(f'<@&{row["role_id"]}>' for row in units)[:1024],inline=False)
    if card.get('bio'):
        e.add_field(name='▸ CONTRACTOR INTEL',value=safe(card['bio'],220),inline=False)
    if reported_handles:
        e.add_field(name='▸ LINKED IN-GAME CALLSIGNS',
          value='\n'.join(f'`{safe(r["game_name"],64)}` • {r["sightings"]} screenshot(s)'
              for r in reported_handles)[:1024]+'\n*Uploader-reported associations.*',inline=False)
    mentor=data.get('mentorship') or {}
    if any(mentor.values()):
        e.add_field(name='▸ MENTOR RECORD',value=(
          f'Contractors trained: **{mentor.get("trained_contractors",0)}** • '
          f'Graduations: **{mentor.get("graduated_training",0)}**'),inline=False)
    e.set_footer(text='LEDGER // CONTRACTOR DOSSIER • Use /dossier edit to customize this card')
    return e


def draft_card(payload: dict, bonuses: list[dict], fmt_money,
               guild_id: int, target_unit: int | None = None) -> discord.Embed:
    e=base('▰ CONTACT OFFICE / CONTRACT DRAFT',
           f'**{discord.utils.escape_markdown(payload["title"])}**\n'
           f'{discord.utils.escape_markdown(payload["details"][:1200])}\n\n'
           'This draft is **not yet public**. Add bonuses, then publish.',color=GOLD)
    e.add_field(name='▸ BASE',value=fmt_money(guild_id,payload['reward'],full=True))
    e.add_field(name='▸ RECIPIENT',value='Individual contractors' if payload['kind']=='USER'
                else (f'<@&{target_unit}>' if target_unit else 'Any registered unit'))
    mode=payload.get('proof_mode','STATS')
    e.add_field(name='▸ SCREENSHOT FORMAT',value={'STATS':'Post-mission stats',
                'OPERATION':'Operation summary','BOTH':'Stats + operation summary'}.get(mode,mode))
    goal=payload.get('public_goal',0)
    if goal:
        e.add_field(name='▸ COMMUNITY GOAL',value=f'{goal:,} unique accepted mission proofs')
    e.add_field(name='▸ MISSION REQUIREMENTS',
        value=f'**{payload.get("required_submissions",1)}** verified screenshots per completion\n'
              f'**{"REPEATABLE" if payload.get("repeatable") else "ONE-TIME"}**',inline=False)
    lines=[f'`+{r["reward"]:,}` **{r["title"]}** — {requirement_text(r)}' for r in bonuses]
    e.add_field(name='▸ OPTIONAL OBJECTIVES',value=field_lines(lines,'None. The base payout still works.'),inline=False)
    e.add_field(name='▸ MAXIMUM POTENTIAL PAYOUT',
                value=fmt_money(guild_id,payload['reward']+sum(r['reward'] for r in bonuses),full=True),
                inline=False)
    e.set_footer(text='CONTACT OFFICE // Bonuses must pay less than the base • Max 4')
    return e
