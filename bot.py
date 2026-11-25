"""LEDGER — R.E.A.P.E.R. Divers Black Market Contracts and shop Discord bot."""
from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError

from storage import LedgerStorage, RuleError
from mentorship import MentorshipStore
from ocr_verifier import analyze_photo_sync, ocr_engine_status
from operation_ocr import analyze_operation_sync, combine_evidence
from blacksite_lore import intel_drop
from blacksite_ids import contract_ref, mentor_ref, contract_id_banner, mentor_id_banner
from contract_objectives import METRICS, MAX_OBJECTIVES, validate_bonus, requirement_text
from blacksite_ui import base as blacksite_embed, contract_card, profile_card, draft_card, GREEN, TEAL, GOLD, BLUE

load_dotenv(Path(__file__).resolve().parent / '.env')
TOKEN = os.getenv('DISCORD_TOKEN')
TEST_GUILD_ID = int(os.environ['TEST_GUILD_ID']) if os.getenv('TEST_GUILD_ID') else None
DB_PATH = os.getenv('DATABASE_PATH', 'ledger.sqlite3')
CURRENCY = 'Blacksite Credits'
EMOJI_FILE = Path(__file__).resolve().parent / 'assets' / 'blacksite_credits_emoji.png'
MAX_PROOF_BYTES = 8 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('ledger')
db = LedgerStorage(DB_PATH)
mentors = MentorshipStore(db)
MEMBER_SYNC_ENABLED = os.getenv("LEDGER_MEMBER_SYNC", "0").strip() == "1"


def member_dossier_data(guild_id:int,member_id:int) -> dict:
    data=db.member_summary(guild_id,member_id)
    data['mentorship']=mentors.profile_stats(guild_id,member_id)
    data['card']=db.contractor_card(guild_id,member_id)
    return data


def credit_icon(guild_id: int) -> str:
    """The server's installed custom Blacksite Credits emoji, if configured."""
    emoji_id = db.setting(guild_id, 'currency_emoji_id')
    if not emoji_id:
        return ''
    # Use the actual uploaded emoji name (which need not match the file name).
    uploaded = bot.get_emoji(emoji_id)
    return str(uploaded) if uploaded else f'<:blacksite_credits:{emoji_id}>'


def money(guild_id: int, amount: int, *, full: bool = False, signed: bool = False) -> str:
    """Show the user's supplied currency icon on each amount displayed in Discord."""
    number = f'{amount:+,}' if signed else f'{amount:,}'
    icon = credit_icon(guild_id)
    return f'{number} {icon}{" Blacksite Credits" if full else ""}' if icon else (
        f'{number} {CURRENCY if full else "BC"}')



def manager(interaction: discord.Interaction) -> bool:
    return (interaction.guild is not None and
            isinstance(interaction.user, discord.Member) and
            interaction.user.guild_permissions.manage_guild)


def moderator(interaction: discord.Interaction) -> bool:
    if manager(interaction):
        return True
    role_id = db.setting(interaction.guild_id, 'moderator_role_id') if interaction.guild_id else None
    return bool(role_id and isinstance(interaction.user, discord.Member) and
                any(role.id == role_id for role in interaction.user.roles))


async def deny(interaction: discord.Interaction, reason: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(reason, ephemeral=True)
    else:
        await interaction.response.send_message(reason, ephemeral=True)


async def require_moderator(interaction: discord.Interaction) -> bool:
    if interaction.guild_id and moderator(interaction):
        return True
    await deny(interaction, 'Only the **LEDGER Market Moderation** role or server managers can do that.')
    return False


async def require_manager(interaction: discord.Interaction) -> bool:
    if interaction.guild_id and manager(interaction):
        return True
    await deny(interaction, 'You need **Manage Server** permission to configure LEDGER moderation.')
    return False


def recipient_label(kind: str, recipient_id: int) -> str:
    return f'<@{recipient_id}>' if kind == 'USER' else f'<@&{recipient_id}>'


def contract_embed(row) -> discord.Embed:
    return contract_card(row, db.objectives(row['guild_id'], row['id']),
                         db.counts(row['guild_id'], row['id']), money)


def shop_embed(guild_id: int, shop_kind: str = 'USER') -> discord.Embed:
    items = db.items(guild_id, limit=20, shop_kind=shop_kind)
    is_unit = shop_kind == 'UNIT'
    embed = discord.Embed(
        title='▰ BLACKSITE / UNMARKED UNIT REQUISITIONS' if is_unit else '▰ BLACKSITE / THE UNDERCOUNTER',
        description=('Unit orders debit the **unit treasury**, not personal wallets. '
                     'Only approved unit purchasing officers or LEDGER moderators may order.'
                     if is_unit else 'Individual orders debit your **personal Blacksite Credits**.') +
                    '\nAll purchases require manual staff fulfillment.',
        color=GREEN)
    if not items:
        embed.description += '\n\nThe shelves are currently empty.'
    for item in items:
        stock = '∞' if item['stock'] is None else str(item['stock'])
        embed.add_field(name=f"#{item['id']} · {item['name']} — {money(guild_id, item['price'])}",
                        value=f"{item['description'][:180]}\n**Stock:** {stock}", inline=False)
    embed.set_footer(text=('Use /unitshop browse • /unitshop buy • /unit balance'
                            if is_unit else 'Use /shop browse • /shop buy • /balance'))
    return embed


def review_embed(claim, offer) -> discord.Embed:
    e = discord.Embed(title=f"▰ REVIEW DESK / REVIEW #{claim['id']} • {contract_ref(offer['id'])}",
                      description=contract_id_banner(offer['id']) + '\n\n' + offer['title'], color=GOLD)
    e.add_field(name='Recipient', value=recipient_label(claim['recipient_kind'],claim['recipient_id']))
    e.add_field(name='Submitted by', value=f"<@{claim['submitted_by']}>")
    e.add_field(name='Payment per recipient', value=money(offer['guild_id'],offer['reward'],full=True))
    attempt = db.latest_attempt(offer['guild_id'],claim['id'])
    if attempt:
        e.add_field(name='Mission metadata', value=(f"Planet: **{discord.utils.escape_markdown(attempt['planet'])}** "
                   f"• Difficulty **{attempt['difficulty']}**"),inline=False)
        others=json.loads(attempt['teammate_ids'])
        if others:
            e.add_field(name='Tagged teammate payouts (declared by submitter)',
                        value=' '.join(f'<@{rid}>' for rid in others)[:1000],inline=False)
        linked=db.attempt_player_links(offer['guild_id'],attempt['id'])
        if linked:
            e.add_field(name='In-game → Discord (uploader reported, NOT verified)',
                value='\n'.join(f'`{discord.utils.escape_markdown(discord.utils.escape_mentions(name))}` → <@{uid}>'
                    for uid,name in linked)[:1000],inline=False)
        if attempt['vision_stats']:
            try:
                facts=db.clean_vision_stats(json.loads(attempt['vision_stats']))
            except (ValueError,TypeError):
                facts={}
            if facts and facts.get('players'):
                details=[]
                for player in facts['players']:
                    ps=player['stats']
                    details.append(f'`{discord.utils.escape_markdown(discord.utils.escape_mentions(player["game_name"]))}` '
                                   f'• {ps.get("kills","?")} kills • {ps.get("stims_used","?")} stims '
                                   f'• {ps.get("deaths","?")} deaths')
                e.add_field(name='Per-player OCR facts (links are uploader-declared)',
                            value='\n'.join(details)[:900],inline=False)
            if facts and not facts.get('players'):
                labels={'kills':'Kills','deaths':'Deaths','stims_used':'Stims used','samples_collected':'Samples',
                        'shots_fired':'Shots fired','shots_hit':'Shots hit',
                        'stratagems_used':'Stratagems','mission_success':'Mission success',
                        'extracted':'Extracted','planet_visible':'Planet visible',
                        'difficulty_visible':'Difficulty visible'}
                visible=', '.join(f'{labels[key]}: {value}' for key,value in facts.items())
                e.add_field(name='OCR-read screenshot stats',value=visible[:900],inline=False)
                met=db.bonus_results(offer['guild_id'],offer['id'],attempt)
                if met:
                    e.add_field(name='Potential objective bonuses',
                        value='\n'.join(f"+{m['reward']:,} BC: {m['title']}" for m in met)[:900], inline=False)
        if attempt['duplicate_note']:
            e.add_field(name='Duplicate warning',value=attempt['duplicate_note'][:1000],inline=False)
    if claim['evidence']:
        e.add_field(name='Screenshot', value='Attached below. In-game names were supplied by the uploader and are not independently authenticated.',inline=False)
        if claim['evidence'].startswith('https://'):
            e.set_image(url=claim['evidence'])
    if claim['ai_verdict']:
        e.add_field(name='Local OCR image check',
                    value=f"**{claim['ai_verdict']}** — {(claim['ai_summary'] or '')[:900]}",inline=False)
    e.add_field(name='Status', value=claim['status'],inline=False)
    if claim['review_note']:
        e.add_field(name='Review note', value=claim['review_note'][:1024], inline=False)
    return e


def shop_catalog(guild_id: int, shop_kind: str = 'USER') -> str:
    items = db.items(guild_id, 25, shop_kind=shop_kind)
    if not items:
        return ('The Unit Supply Exchange' if shop_kind=='UNIT' else 'The Contractor Market') + ' currently has no items.'
    entries = []
    for item in items:
        stock = 'unlimited' if item['stock'] is None else str(item['stock'])
        entries.append(f"**#{item['id']} · {item['name']}** — {money(guild_id, item['price'])} · stock: {stock}\n"
                       f"{item['description'][:100]}")
    result = '\n'.join(entries)
    if len(result) > 1850:
        result = result[:1800] + '\n…More items may be available. Contact staff.'
    return result


class LedgerBot(commands.Bot):
    async def setup_hook(self) -> None:
        for row in db.board_contracts():
            self.add_view(ContractView(row['id']), message_id=row['board_message_id'])
        for row in db.outstanding_reviews():
            self.add_view(ReviewView(row['id']), message_id=row['review_message_id'])
        self.add_view(ShopView('USER'))
        self.add_view(ShopView('UNIT'))
        self.add_view(TopEarnersView())
        self.add_view(BlacksiteTerminal())
        self.add_view(MentorRequestPanel())
        for case in mentors.conn.execute('''SELECT id,message_id FROM mentor_requests
            WHERE message_id IS NOT NULL AND status IN ('OPEN','ASSIGNED','READY')'''):
            self.add_view(MentorCaseView(case['id']), message_id=case['message_id'])
        for session in mentors.conn.execute("SELECT id,guild_id FROM mentor_sessions WHERE status='PENDING'"):
            self.add_view(MentorSessionConfirmView(session['guild_id'],session['id']))
        if TEST_GUILD_ID:
            guild = discord.Object(id=TEST_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            cmds = await self.tree.sync(guild=guild)
        else:
            cmds = await self.tree.sync()
        log.info('Synced %s slash commands', len(cmds))


_intents = discord.Intents.default()
_intents.members = MEMBER_SYNC_ENABLED
bot = LedgerBot(command_prefix='!', intents=_intents,
                allowed_mentions=discord.AllowedMentions.none())


async def fetch_text_channel(channel_id: int):
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise RuleError('Configured channel is not a text channel.')
    return channel


# ---------------------------------------------------------------------------
# Persistent live Top Earners board. One editable Discord message per guild,
# refreshed on startup and every 30 minutes while LEDGER is online.
# ---------------------------------------------------------------------------
_top_earners_locks: dict[int, asyncio.Lock] = {}


def top_earners_embed(guild_id: int) -> discord.Embed:
    """Blacksite-styled static board; safe to edit in place every 30 minutes."""
    users=db.top_earners(guild_id,'USER',10)
    units=db.top_earners(guild_id,'UNIT',5)
    totals=db.earnings_totals(guild_id)
    user_total=totals.get('USER',{'earned':0,'payouts':0})
    unit_total=totals.get('UNIT',{'earned':0,'payouts':0})
    kills=db.stat_leaderboard(guild_id,'kills',5)
    stims=db.stat_leaderboard(guild_id,'stims_used',5)

    e=discord.Embed(
        title='▰ LEDGER // BLACKSITE RANKINGS',
        description=(
            '`SECURE BROKER FREQUENCY  //  CONTRACTOR REGISTRY`\n'
            '**THE PEOPLE GETTING PAID. THE UNITS GETTING RESULTS.**\n'
            '━━━━━━━━━━━━━━━━━━━━━━━━\n'
            'Ranks use **lifetime verified contract earnings**, not wallet balances.\n'
            'Field rankings use **linked, OCR-read player-card values only**.'
        ), color=GREEN, timestamp=discord.utils.utcnow())
    if bot.user is not None:
        e.set_thumbnail(url=bot.user.display_avatar.url)

    def ranked(rows, *, unit=False, stat=False) -> str:
        if not rows:
            return '`NO RECORDS`  •  Awaiting the first confirmed report.'
        lines=[]
        for i,r in enumerate(rows):
            pos=f'{i+1:02d}'
            lead='◆' if i==0 else '◇' if i<3 else '·'
            tag=(f'<@&{r["recipient_id"]}>' if unit else f'<@{r["recipient_id"]}>')
            if stat:
                value=f'{r["total"]:,}'
                details=f'{r["screenshots"]} verified cards'
            else:
                value=money(guild_id,r['earned'])
                details=f'{r["payouts"]} paid contract(s)'
            lines.append(f'`{pos}` {lead} {tag}  **{value}**\n'
                         f'       ↳ *{details}*')
        return '\n'.join(lines)[:1024]

    e.add_field(name='▰ 01  //  HIGH-VALUE CONTRACTORS',
                value=ranked(users),inline=False)
    e.add_field(name='▰ 02  //  UNIT TREASURY EARNINGS',
                value=ranked(units,unit=True),inline=False)
    e.add_field(name='▰ 03  //  HOSTILE ELIMINATIONS',
                value=ranked(kills,stat=True),inline=False)
    e.add_field(name='▰ 04  //  FIELD MEDICAL USAGE',
                value=ranked(stims,stat=True),inline=False)
    e.add_field(name='▰ NETWORK SETTLEMENTS',
                value=(f'**Contractor payouts:** `{user_total["payouts"]:,}`  '
                       f'• **Issued:** {money(guild_id,user_total["earned"])}\n'
                       f'**Unit payouts:** `{unit_total["payouts"]:,}`  '
                       f'• **Issued:** {money(guild_id,unit_total["earned"])}'),
                inline=False)
    e.set_footer(text='BLACKSITE NETWORK  •  Auto-refresh every 30 minutes  •  Numbers are OCR-reported, not authenticated gameplay')
    return e


class TopEarnersView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='My Wallet',style=discord.ButtonStyle.secondary,
                       custom_id='ledger:earnings:mine')
    async def my_earnings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild_id:
            await deny(interaction,'Use this inside your server.')
            return
        earned,payouts=db.earnings(interaction.guild_id,'USER',interaction.user.id)
        wallet=db.balance(interaction.guild_id,'USER',interaction.user.id)
        await interaction.response.send_message(
            f'**Your Blacksite Credits**\nLifetime contract earnings: **{money(interaction.guild_id,earned)}** '
            f'({payouts:,} payouts)\nCurrent spendable balance: **{money(interaction.guild_id,wallet)}**',
            ephemeral=True)

    @discord.ui.button(label='My Telemetry',style=discord.ButtonStyle.secondary,
                       custom_id='ledger:earnings:stats')
    async def my_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild_id:
            await deny(interaction,'Use this inside your server.')
            return
        await interaction.response.send_message(
            embed=stats_embed(interaction.guild_id,'YOUR CONTRACT STATS',
                              db.proof_stats(interaction.guild_id,member_id=interaction.user.id)),
            ephemeral=True)

    @discord.ui.button(label='My Dossier',style=discord.ButtonStyle.success,
                       custom_id='ledger:earnings:dossier')
    async def dossier(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
            await deny(interaction,'Use this in a server.')
            return
        member=interaction.user
        await interaction.response.send_message(embed=profile_card(
            member,member_dossier_data(interaction.guild_id,member.id),
            db.member_units(interaction.guild_id,[r.id for r in member.roles]),
            money,interaction.guild_id,
            db.member_handles(interaction.guild_id,member.id)),view=DossierActions(member.id),ephemeral=True)


async def publish_top_earners(guild_id: int) -> bool:
    """Edit the stored post, recovering if it was deleted, without spamming channels."""
    lock=_top_earners_locks.setdefault(guild_id,asyncio.Lock())
    async with lock:
        channel_id=db.setting(guild_id,'top_earners_channel_id')
        if not channel_id:
            return False
        try:
            channel=await fetch_text_channel(channel_id)
            message_id=db.setting(guild_id,'top_earners_message_id')
            embed=top_earners_embed(guild_id)
            if message_id:
                try:
                    msg=await channel.fetch_message(message_id)
                except discord.NotFound:
                    msg=None
                if msg:
                    await msg.edit(embed=embed,view=TopEarnersView(),
                                   allowed_mentions=discord.AllowedMentions.none())
                    return True
            msg=await channel.send(embed=embed,view=TopEarnersView(),
                                   allowed_mentions=discord.AllowedMentions.none())
            db.configure(guild_id,'top_earners_message_id',msg.id)
            return True
        except (discord.HTTPException,discord.Forbidden,RuleError) as exc:
            log.warning('Unable to publish Top Earners in guild %s: %s',guild_id,exc)
            return False


@tasks.loop(minutes=30)
async def refresh_top_earners_loop():
    for cfg in db.top_earners_channels():
        await publish_top_earners(cfg['guild_id'])


@refresh_top_earners_loop.before_loop
async def _wait_before_top_earners():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# BLACKSITE DISPATCH: one edited LIVE mission progress message per guild.
# Independent of the Top Earners board; accepted images count ONCE for the
# entire squad regardless of how many recipients earn Blacksite Credits.
# ---------------------------------------------------------------------------
_dispatch_locks:dict[int,asyncio.Lock]={}


def dispatch_embed(guild_id:int)->discord.Embed:
    rows=db.dispatch_rows(guild_id,limit=8)
    e=blacksite_embed('▰ BLACKSITE NETWORK // LIVE CONTRACT DISPATCH',
        '**SECURE BROKER FEED — ACTIVE CONTRACTS**\n'
        'Verified evidence feeds the live operation tally. One mission screenshot '
        'counts once, even if several contractors are linked to it.\n\n'
        'This message refreshes after submissions and every **2 minutes** while LEDGER is online.',color=GREEN)
    for row in rows:
        goal=row['public_goal'];count=row['accepted']
        if goal:
            filled=min(12,int(12*min(count,goal)/goal))
            bar='▰'*filled+'▱'*(12-filled)
            progress=f'`{bar}` **{count:,}/{goal:,}** mission reports ({min(100,count/goal*100):.0f}%)'
        else:
            progress=f'**{count:,}** unique mission report(s) received'
        body=(f'{progress}\n'
              f'**{row["payouts"]:,}** completed paid contracts • '
              f'**{row["in_progress"]:,}** active contract cycles\n'
              f'{row["required_submissions"]} submission(s) per payout'
              + (' • Repeatable' if row['repeatable'] else ' • One-time'))
        e.add_field(name=f'▸ {contract_ref(row["id"])} • ID {row["id"]} / {discord.utils.escape_markdown(row["title"][:52])}',
                    value=body[:1024],inline=False)
    if not rows:
        e.add_field(name='NO ACTIVE CONTRACTS',value='The contact is silent. Check back when new work drops.',inline=False)
    e.set_footer(text='LEDGER // BLACK MARKET DISPATCH • Auto-updating private ledger feed')
    return e


async def publish_dispatch(guild_id:int)->bool:
    lock=_dispatch_locks.setdefault(guild_id,asyncio.Lock())
    async with lock:
        channel_id=(db.setting(guild_id,'dispatch_channel_id') or
                    db.setting(guild_id,'board_channel_id'))
        if not channel_id:return False
        try:
            channel=await fetch_text_channel(channel_id)
            mid=db.setting(guild_id,'dispatch_message_id')
            if mid:
                try: msg=await channel.fetch_message(mid)
                except (discord.NotFound,discord.Forbidden):msg=None
                if msg:
                    await msg.edit(embed=dispatch_embed(guild_id),
                                   allowed_mentions=discord.AllowedMentions.none())
                    return True
            msg=await channel.send(embed=dispatch_embed(guild_id),
                                   allowed_mentions=discord.AllowedMentions.none())
            db.configure(guild_id,'dispatch_message_id',msg.id)
            if channel.permissions_for(channel.guild.me).manage_messages:
                try:await msg.pin(reason='LEDGER live contract dispatch')
                except discord.HTTPException:pass
            return True
        except (discord.HTTPException,RuleError) as exc:
            log.warning('LEDGER dispatch update failed for %s: %s',guild_id,exc)
            return False


@tasks.loop(minutes=2)
async def dispatch_update_loop():
    for row in db.conn.execute('''SELECT guild_id FROM ledger_settings WHERE
        (dispatch_channel_id IS NOT NULL AND dispatch_channel_id>0) OR
        (board_channel_id IS NOT NULL AND board_channel_id>0)'''):
        await publish_dispatch(row['guild_id'])


@dispatch_update_loop.before_loop
async def _await_dispatch_ready():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Contract screenshot stats. Declared values and OCR-visible facts are distinct.
# Teammate tags never duplicate screenshot numbers.
# ---------------------------------------------------------------------------
def stats_embed(guild_id: int, label: str, stats: dict) -> discord.Embed:
    embed=discord.Embed(title=f'▰ LEDGER DOSSIER / {label}', color=TEAL,
                        timestamp=discord.utils.utcnow())
    embed.description=('Figures come from screenshot verification. '
                       '**Planet/difficulty are submitter-reported unless OCR shows them.** '
                       'OCR-visible totals count only approved screenshots with a PASS verdict. '
                       'Player statistics are attributed only to explicitly linked in-game names, not all tagged teammates.')
    embed.add_field(name='Proof history',
        value=(f"Submitted: **{stats['submissions']:,}** · Verified: **{stats['paid']:,}**\n"
               f"In review: **{stats['review']:,}** · Rejected: **{stats['rejected']:,}**"
               + (f" · Processing: **{stats['processing']:,}**" if stats['processing'] else '')),
        inline=False)
    names={'kills':'Kills','deaths':'Deaths','stims_used':'Stims used','samples_collected':'Samples collected',
           'shots_fired':'Shots fired','shots_hit':'Shots hit','stratagems_used':'Stratagems used',
           'accidentals':'Accidentals','melee_kills':'Melee kills',
           'times_reinforcing':'Times reinforcing','friendly_fire_damage':'Friendly fire damage'}
    seen=stats['available']
    totals=stats['totals']
    lines=[f"**{names[key]}:** {totals[key]:,} ({seen[key]} screenshots)"
           for key in names if seen.get(key)]
    lines += [f"**Successful missions seen:** {stats['booleans']['mission_success']:,}"
              if stats['booleans'].get('mission_success') else '',
              f"**Extractions seen:** {stats['booleans']['extracted']:,}"
              if stats['booleans'].get('extracted') else '']
    lines=[x for x in lines if x]
    embed.add_field(name=f"OCR-visible statistics ({stats['ai_passed_paid']} verified screenshots)",
                    value='\n'.join(lines)[:1024] or 'No readable screenshot statistics yet. Run `/stats diagnose` to see why.',
                    inline=False)
    planet_lines=[f'{discord.utils.escape_markdown(name[:65])}: **{count}**'
                  for name,count in stats['planets'][:6]]
    embed.add_field(name='Reported planets (accepted submissions)',
                    value='\n'.join(planet_lines)[:1024] or 'None yet.',inline=True)
    difficulties=[f'D{difficulty}: **{count}**' for difficulty,count in stats['difficulties'][:8]]
    embed.add_field(name='Reported difficulties (accepted submissions)',
                    value='\n'.join(difficulties)[:1024] or 'None yet.',inline=True)
    verified_planets=[f'{discord.utils.escape_markdown(name[:65])}: **{count}**'
                      for name,count in stats['visible_planets'][:6]]
    verified_diffs=[f'D{diff}: **{count}**' for diff,count in stats['visible_difficulties'][:8]]
    if verified_planets or verified_diffs:
        embed.add_field(name='Planets actually visible in screenshots',
                        value='\n'.join(verified_planets)[:1024] or 'Not readable.',inline=True)
        embed.add_field(name='Difficulties actually visible in screenshots',
                        value='\n'.join(verified_diffs)[:1024] or 'Not readable.',inline=True)
    embed.set_footer(text='One screenshot is counted once, regardless of tagged teammates.')
    return embed


async def refresh_board(guild_id: int, contract_id: int) -> None:
    row = db.get_contract(guild_id,contract_id)
    if not row or not row['board_channel_id'] or not row['board_message_id']:
        return
    try:
        channel = await fetch_text_channel(row['board_channel_id'])
        msg = await channel.fetch_message(row['board_message_id'])
        await msg.edit(embed=contract_embed(row),
                       view=ContractView(contract_id) if row['status'] == 'OPEN' else None)
    except (discord.HTTPException, discord.Forbidden, RuleError) as exc:
        log.warning('Unable to refresh contract #%s: %s', contract_id, exc)


async def refresh_shop(guild_id: int, shop_kind: str = 'USER') -> None:
    prefix = 'unit_shop_' if shop_kind == 'UNIT' else 'shop_'
    channel_id = db.setting(guild_id, prefix+'channel_id')
    msg_id = db.setting(guild_id, prefix+'message_id')
    if not channel_id or not msg_id:
        return
    try:
        channel = await fetch_text_channel(channel_id)
        msg = await channel.fetch_message(msg_id)
        await msg.edit(embed=shop_embed(guild_id, shop_kind), view=ShopView(shop_kind))
    except (discord.HTTPException, discord.Forbidden, RuleError) as exc:
        log.warning('Unable to refresh published shop: %s', exc)


async def refresh_review(guild_id: int, claim_id: int) -> None:
    claim = db.claim_by_id(guild_id, claim_id)
    review_channel = db.setting(guild_id, 'review_channel_id')
    if not claim or not review_channel or not claim['review_message_id']:
        return
    offer = db.get_contract(guild_id, claim['contract_id'])
    try:
        channel = await fetch_text_channel(review_channel)
        msg = await channel.fetch_message(claim['review_message_id'])
        await msg.edit(embed=review_embed(claim,offer),
                       view=ReviewView(claim_id) if claim['status']=='PENDING' else None)
    except (discord.HTTPException, RuleError) as exc:
        log.warning('Unable to refresh review #%s: %s', claim_id,exc)


async def announce_review(guild_id: int, claim_id: int, data: bytes,
                          mime: str, operation_photo:bytes | None = None) -> bool:
    '''Post ONLY flagged evidence to the moderator channel, with its original image.'''
    channel_id = db.setting(guild_id, 'review_channel_id')
    if not channel_id:
        return False
    claim = db.claim_by_id(guild_id,claim_id)
    offer = db.get_contract(guild_id,claim['contract_id'])
    extension = {'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[mime]
    try:
        channel = await fetch_text_channel(channel_id)
        attachments=[discord.File(BytesIO(data),filename=f'ledger-review-{claim_id}.{extension}')]
        if operation_photo:
            attachments.append(discord.File(BytesIO(operation_photo),filename=f'operation-review-{claim_id}.png'))
        msg = await channel.send(
            content=f'LEDGER | flagged proof for contract #{offer["id"]} / review #{claim_id}',
            files=attachments,allowed_mentions=discord.AllowedMentions.none())
        db.attach_proof_review(guild_id,claim_id,msg.attachments[0].url,msg.id)
        await msg.edit(content=None,embed=review_embed(db.claim_by_id(guild_id,claim_id),offer),
                       view=ReviewView(claim_id))
        return True
    except (discord.HTTPException, RuleError) as exc:
        log.warning('Review notification failed: %s',type(exc).__name__)
        return False


async def approve_claim(interaction: discord.Interaction, claim_id: int,
                        with_teammates: bool = True) -> None:
    if not await require_moderator(interaction):
        return
    ok,note,recipients=db.approve_group(interaction.guild_id,claim_id,interaction.user.id,
                                       with_teammates=with_teammates)
    if not ok:
        await deny(interaction,note)
        return
    claim = db.claim_by_id(interaction.guild_id,claim_id)
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,claim['contract_id'])
    await refresh_review(interaction.guild_id,claim_id)
    await publish_dispatch(interaction.guild_id)
    await publish_top_earners(interaction.guild_id)
    await announce_payout(interaction.guild_id,claim_id,recipients,note)
    await interaction.followup.send(
        (f'**Payment authorized:** {note} Recipients: '+
         ', '.join(recipient_label(claim['recipient_kind'],r) for r in recipients)
         if recipients else f'**Verified mission counted toward progress.** {note}'),ephemeral=True)


async def reject_claim(interaction: discord.Interaction, claim_id: int, reason: str) -> None:
    if not await require_moderator(interaction):
        return
    ok, note = db.reject(interaction.guild_id,claim_id,reason)
    if not ok:
        await deny(interaction,note)
        return
    claim = db.claim_by_id(interaction.guild_id,claim_id)
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,claim['contract_id'])
    await refresh_review(interaction.guild_id,claim_id)
    await interaction.followup.send('Evidence rejected. The recipient can resubmit.',ephemeral=True)


def recognize_image(data: bytes) -> str | None:
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if len(data)>=12 and data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        return 'image/webp'
    return None


def image_fingerprint(data: bytes) -> tuple[str, bytes]:
    """Decode an actual image and compute visual fingerprint for near duplicate review."""
    with Image.open(BytesIO(data)) as src:
        if src.width*src.height>28_000_000:
            raise ValueError('Image dimensions exceed the safe limit.')
        img=ImageOps.exif_transpose(src).convert('RGB')
        small=img.resize((32,32),Image.Resampling.BILINEAR)
        gray=img.convert('L').resize((9,8),Image.Resampling.BILINEAR)
        px=list(gray.getdata())
        bits=0
        for y in range(8):
            for x in range(8):
                bits=(bits<<1)|int(px[y*9+x]>px[y*9+x+1])
        return f'{bits:016x}',small.tobytes()




def tracking_feedback(guild_id:int,attempt_id:int,uploader_id:int,ocr:dict) -> str:
    """Never silently claim that a paid screenshot also credited career stats."""
    players=ocr.get('players',[]) if isinstance(ocr,dict) else []
    if players:
        linked=db.attempt_player_records(guild_id,attempt_id)
        matches=[]
        for link in linked:
            uid=link['discord_user_id']
            hit=[p for p in players if db._player_matches_user(guild_id,attempt_id,
                       p['game_name'],uid,p.get('column'))]
            if len(hit)==1:
                stats=hit[0]['stats']
                matches.append(f'<@{uid}>: {stats.get("kills","?")} kills, {stats.get("stims_used","?")} stims')
        if matches:
            result='**OCR stats linked:** '+ ' · '.join(matches)
        else:
            result='⚠️ **No player stats linked.** The entered in-game names did not match OCR.'
        if len(matches)<len(linked):
            result+='\n⚠️ Unmatched links will not count toward personal stats or kill/stim rankings.'
        return result+'\nUse `/stats diagnose` for details.'
    numeric=any(k in ocr for k in ('kills','deaths','stims_used','shots_fired')) if isinstance(ocr,dict) else False
    if numeric:
        return 'OCR read some numbers, but no separate player columns. Check `/stats diagnose`.'
    return '⚠️ Screenshot accepted, but **no player numbers were extracted**. Credits and career stats are separate; check `/stats diagnose`.'

async def submit_photo(interaction:discord.Interaction, contract_id:int,
                       kind:str,recipient_id:int,attachment:discord.Attachment | bytes,
                       planet:str,difficulty:int,teammates:list[discord.Member] | None = None,
                       notes:str='',player_links:list[tuple[int,str]] | None = None,
                       player_columns:dict[int,int] | None = None,
                       precomputed_ocr:tuple[str,str,dict] | None = None,
                       operation_photo:bytes | None = None) -> None:
    if interaction.guild_id is None or interaction.guild is None:
        await deny(interaction,'Submit proof in the server.')
        return
    planet=planet.strip()
    if not (2<=len(planet)<=70) or not (1<=difficulty<=10):
        await deny(interaction,'Enter a planet (2–70 characters) and difficulty 1–10.')
        return
    offer=db.get_contract(interaction.guild_id,contract_id)
    if not offer or offer['status']!='OPEN' or offer['payout_mode']!=kind:
        await deny(interaction,'This contract is closed or has another payout type.')
        return
    if kind=='UNIT' and not eligible_unit(interaction,recipient_id):
        await deny(interaction,'You must belong to the selected unit.')
        return
    if kind=='UNIT' and offer['target_unit_id'] and offer['target_unit_id']!=recipient_id:
        await deny(interaction,'Contract is reserved for another unit.')
        return
    # On unit contracts these selections are STAT links only, not personal payouts.
    selected=list(teammates or [])
    if len(selected)>3 or any(not isinstance(m,discord.Member) or m.bot or
                               m.guild.id!=interaction.guild_id or m.id==interaction.user.id
                               for m in selected) or len({m.id for m in selected})!=len(selected):
        await deny(interaction,'Select up to three other human server members (not yourself).')
        return
    current=db.get_claim(interaction.guild_id,contract_id,kind,recipient_id)
    if current and (current['status']=='PENDING' or
                    (current['status']=='PAID' and not offer['repeatable'])):
        await deny(interaction,'This member or unit has pending proof or completed a one-time contract.')
        return
    if (len(attachment) if isinstance(attachment,bytes) else attachment.size)>MAX_PROOF_BYTES:
        await deny(interaction,'Upload a PNG, JPG or WebP screenshot under 8 MB.')
        return
    review_id=db.setting(interaction.guild_id,'review_channel_id')
    if not review_id:
        await deny(interaction,'Moderators must first configure `/board set_review_channel`.')
        return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        data=attachment if isinstance(attachment,bytes) else await attachment.read()
        mime=recognize_image(data)
        if not mime or len(data)>MAX_PROOF_BYTES:
            await interaction.followup.send('Upload an actual PNG, JPG or WebP image under 8 MB.',ephemeral=True)
            return
        dhash,thumb=image_fingerprint(data)
        # Validate channel before creating a pending claim.
        channel=await fetch_text_channel(review_id)
        perms=channel.permissions_for(interaction.guild.me)
        if not all((perms.view_channel,perms.send_messages,perms.attach_files,perms.embed_links)):
            await interaction.followup.send('LEDGER requires View, Send, Embed Links, and Attach Files in the private review channel.',ephemeral=True)
            return
        if channel.permissions_for(interaction.guild.default_role).view_channel:
            await interaction.followup.send('The review channel must be private from @everyone.',ephemeral=True)
            return
        ok,note,claim_id=db.submit(interaction.guild_id,contract_id,kind,recipient_id,
                                    interaction.user.id,'')
        if not ok:
            await interaction.followup.send(note,ephemeral=True)
            return
        attempt_id,duplicate=db.record_attempt(
            interaction.guild_id,contract_id,claim_id,interaction.user.id,
            hashlib.sha256(data).hexdigest(),dhash,thumb,planet,difficulty,
            [m.id for m in selected] if kind=='USER' else [],
            player_links=player_links,
            stat_link_ids=[m.id for m in selected] if kind=='UNIT' else None,
            player_columns=player_columns)
        if operation_photo:
            duplicate = db.track_companion(interaction.guild_id,attempt_id,
                           hashlib.sha256(operation_photo).hexdigest()) or duplicate
    except (discord.HTTPException,RuleError,ValueError,UnidentifiedImageError,Image.DecompressionBombError) as exc:
        log.warning('Proof submission error: %s',type(exc).__name__)
        await interaction.followup.send('Could not process this screenshot. Please verify its format and try again.',ephemeral=True)
        return
    # OCR runs locally; no API key or paid service is needed. Screenshots that
    # cannot be read safely go to the private moderation review channel.
    verdict,summary,stats=(precomputed_ocr if precomputed_ocr is not None else
                           await asyncio.to_thread(analyze_photo_sync,data,mime))
    # Personal contract payouts currently include the claimant/uploader by
    # default, so they may auto-pay only when that claimant is EXPLICITLY
    # assigned to a readable card. Blank cards never auto-enroll outsiders.
    # Unit contracts pay a unit treasury, and can still show an outside squad
    # without inventing individual career stats.
    if verdict=='PASS' and (offer['proof_mode'] if 'proof_mode' in offer.keys() else 'STATS') in ('STATS','BOTH'):
        players=stats.get('players',[]) if isinstance(stats,dict) else []
        from squad_ocr import normalize_handle
        identified={normalize_handle(p.get('game_name','')) for p in players}
        readable_columns={p.get('column') for p in players}
        matched={uid for uid,name in (player_links or [])
                 if ((player_columns and uid in player_columns and
                      player_columns[uid] in readable_columns) or
                     (not player_columns and normalize_handle(name) in identified))}
        if not players:
            verdict='UNCERTAIN'
            summary+=' No readable individual player cards; staff review required.'
        elif kind=='USER' and recipient_id not in matched:
            verdict='UNCERTAIN'
            summary+=(' The submitting contractor was not linked to a readable '
                      'player card. Staff review required before any personal payout.')
    try:
        db.save_analysis(interaction.guild_id,claim_id,verdict,summary)
        db.set_attempt_verdict(interaction.guild_id,attempt_id,verdict,summary,stats)
    except RuleError:
        await interaction.followup.send('This submission changed while being checked; ask a moderator to inspect it.',ephemeral=True)
        return
    # Member tags are the submitter's declaration of participating teammates.
    # They do not require player-name matching against the screenshot.
    # OCR checks the results screen only; exact/near duplicates and payment
    # conflicts still prevent automatic payments.
    payout_blocked=''
    if verdict=='PASS' and not duplicate:
        ok,note,recipients=db.approve_group(interaction.guild_id,claim_id,
            bot.user.id if bot.user else 0,auto=True,with_teammates=True,attempt_id=attempt_id)
        if ok:
            await refresh_board(interaction.guild_id,contract_id)
            await publish_dispatch(interaction.guild_id)
            await publish_top_earners(interaction.guild_id)
            if recipients:
                await announce_payout(interaction.guild_id,claim_id,recipients,note)
            heading=('💳 **Blacksite payout released**' if recipients else
                     '📋 **Verified mission logged; contract in progress**')
            await interaction.followup.send(
                (f'{heading} • Contract #{contract_id}\n{note}\n'
                + ('Paid: '+', '.join(recipient_label(kind,r) for r in recipients)
                   if recipients else 'No base or bonus payout until the mission requirement is met.')
                + '\n\n' + tracking_feedback(interaction.guild_id,attempt_id,
                    interaction.user.id,stats))[:1900],
                ephemeral=True)
            return
        payout_blocked=note
        summary += f' Automatic payout blocked: {note}'
        db.save_analysis(interaction.guild_id,claim_id,'PASS',summary)
    # Confident failures are returned to the player without spamming the review channel.
    if verdict=='FAIL' and not duplicate:
        db.reject(interaction.guild_id,claim_id,f'Automatic check failed: {summary[:450]}')
        await refresh_board(interaction.guild_id,contract_id)
        await interaction.followup.send(
            f'❌ **Proof did not meet contract #{contract_id}.** {summary}\n'
            'No credits paid. You may submit new evidence.',ephemeral=True)
        return
    db.set_attempt_status(interaction.guild_id,attempt_id,'REVIEW')
    notified=await announce_review(interaction.guild_id,claim_id,data,mime,operation_photo)
    await refresh_board(interaction.guild_id,contract_id)
    if not notified:
        # Let the user retry; don't strand a claim requiring an unavailable screenshot.
        db.reject(interaction.guild_id,claim_id,'Could not post evidence for staff review. Please retry.')
        await interaction.followup.send('Could not send evidence to staff; no credits were paid. Please try again or contact moderators.',ephemeral=True)
        return
    reasons=[]
    if duplicate: reasons.append('possible duplicate image')
    if payout_blocked: reasons.append('payment eligibility conflict')
    if verdict!='PASS': reasons.append(f'image check: {verdict}')
    await interaction.followup.send(
        f'🟡 **Contract #{contract_id} is in review** (review #{claim_id}). '
        +'; '.join(reasons)+'\nModerators were notified. No credits paid yet.',ephemeral=True)


# ---------------------------------------------------------------------------
# Post-upload squad linkage. A reporter chooses up to 3 server members and
# explicitly types the in-game handle shown for each. This is a declaration,
# not verified identity evidence. Nothing is paid until they finish the step.
# ---------------------------------------------------------------------------
class SquadMemberSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Tag up to 3 squadmates from this server',
                         min_values=0,max_values=3,row=0)

    async def callback(self,interaction:discord.Interaction):
        parent=self.view
        if not await parent.interaction_check(interaction):
            return
        users=list(self.values)
        if (len(users)>3 or len({u.id for u in users})!=len(users) or
            any(not isinstance(u,discord.Member) or u.bot or
                u.id==parent.owner_id or u.guild.id!=parent.guild_id for u in users)):
            await deny(interaction,'Select up to three OTHER human members of this server.')
            return
        parent.selected=users
        await interaction.response.edit_message(embed=parent.preview(),view=parent)


class SquadNamesModal(discord.ui.Modal, title='BLACKSITE • Link In-Game Players'):
    def __init__(self,squad_view):
        super().__init__(timeout=300)
        self.squad_view=squad_view
        self.inputs=[]
        members=[(squad_view.owner_id,'Your in-game player name',True)]
        members += [(m.id,f'Name for {m.display_name[:28]}',True)
                    for m in squad_view.selected]
        for user_id,label,required in members:
            old=db.member_handles(squad_view.guild_id,user_id,limit=1)
            field=discord.ui.TextInput(label=label,required=required,
                placeholder='Player name visible in the mission screen',
                default=old[0]['game_name'] if old else None,
                max_length=64,min_length=2 if required else 0)
            self.add_item(field)
            self.inputs.append((user_id,field))

    async def on_submit(self,interaction:discord.Interaction):
        if not await self.squad_view.interaction_check(interaction):
            return
        names={uid:str(item.value or '') for uid,item in self.inputs if str(item.value or '').strip()}
        from player_links import PlayerLinkError, validated_links
        try:
            pairs=validated_links(self.squad_view.owner_id,
                [m.id for m in self.squad_view.selected],names)
        except PlayerLinkError as exc:
            await deny(interaction,str(exc))
            return
        await self.squad_view.finish(interaction, list(self.squad_view.selected), pairs)


class SquadLinkView(discord.ui.View):
    """One short-lived, ephemeral upload session (not persisted across restarts)."""
    def __init__(self, owner_id:int, guild_id:int, contract_id:int, recipient_id:int,
                 screenshot:bytes, planet:str, difficulty:int, notes:str,
                 kind:str='USER',precomputed_ocr:tuple[str,str,dict] | None = None):
        super().__init__(timeout=600)
        self.kind=kind
        self.precomputed_ocr=precomputed_ocr
        self.owner_id,self.guild_id=owner_id,guild_id
        self.contract_id,self.recipient_id=contract_id,recipient_id
        self.screenshot=screenshot
        self.planet,self.difficulty,self.notes=planet,difficulty,notes
        self.selected=[]
        self.finished=False
        self.add_item(SquadMemberSelect())

    def preview(self) -> discord.Embed:
        tagged=' '.join(m.mention for m in self.selected) if self.selected else '*None selected*'
        payout_note=('Selected teammates receive contract payouts if eligible.'
                     if self.kind=='USER' else
                     'Unit contract: selected teammates are linked for STATS only. Credits go to the unit treasury.')
        verdict,_,ocr=self.precomputed_ocr or ('NOT CHECKED','',{})
        players=ocr.get('players',[]) if isinstance(ocr,dict) else []
        if players:
            recognized='\n'.join(
                f'• `{discord.utils.escape_markdown(discord.utils.escape_mentions(p["game_name"]))}` '
                f'— {p["stats"].get("kills","?")} kills / {p["stats"].get("stims_used","?")} stims'
                for p in players)[:900]
        else:
            recognized='No individual player columns recognized yet. You can still submit for review.'
        embed=discord.Embed(title='▰ BLACKSITE / LINK YOUR SQUAD',
            description=('**Screenshot received.** Select Discord squadmates and enter the '
                '**exact in-game names shown below** to associate OCR stats with their dossiers.\n\n'
                f'**OCR result:** {verdict}\n**Recognized players:**\n{recognized}\n\n'
                f'**Selected squadmates:** {tagged}\n{payout_note}\n\n'
                'Click **Link Names & Verify** or **Solo • Link My Name**. '
                'Names are uploader-reported and are not proof of participation.'),
            color=GREEN)
        embed.set_image(url='attachment://mission-proof.png')
        embed.set_footer(text='Private setup • 10-minute timeout • no payout before confirmation')
        return embed

    async def interaction_check(self,interaction:discord.Interaction) -> bool:
        if interaction.guild_id!=self.guild_id or interaction.user.id!=self.owner_id:
            await deny(interaction,'This screenshot-linking session belongs to another contractor.')
            return False
        if self.finished:
            await deny(interaction,'This screenshot has already been submitted. Start a new upload if needed.')
            return False
        return True

    async def finish(self,interaction:discord.Interaction,
                     members:list[discord.Member],pairs:list[tuple[int,str]]):
        if self.finished:
            await deny(interaction,'This screenshot is already being verified.')
            return
        self.finished=True
        screenshot=self.screenshot
        self.screenshot=b''
        self.stop()
        await submit_photo(interaction,self.contract_id,self.kind,self.recipient_id,
                           screenshot,self.planet,self.difficulty,members,self.notes,
                           player_links=pairs,precomputed_ocr=self.precomputed_ocr,
                           operation_photo=getattr(self,'operation_photo',None))

    @discord.ui.button(label='Link Names & Verify',style=discord.ButtonStyle.success,row=1)
    async def link_names(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_modal(SquadNamesModal(self))

    @discord.ui.button(label='Solo • Link My Name',style=discord.ButtonStyle.secondary,row=1)
    async def verify_solo(self,interaction:discord.Interaction,button:discord.ui.Button):
        self.selected=[]
        await interaction.response.send_modal(SquadNamesModal(self))

    @discord.ui.button(label='Cancel Upload',style=discord.ButtonStyle.danger,row=1)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button):
        self.finished=True
        self.screenshot=b''
        self.stop()
        await interaction.response.edit_message(content='Upload cancelled. No evidence or payout recorded.',embed=None,view=None)


class PlayerAssignmentSelect(discord.ui.UserSelect):
    """Optional member selection for one physical results-card slot."""
    def __init__(self, slot:dict, row:int):
        self.column=slot['column']
        self.player_name=slot.get('game_name')
        is_readable=bool(slot['readable'])
        if is_readable:
            # Put reliably read numbers FIRST; a mistaken OCR nickname should
            # never influence which teammate is linked to this physical card.
            vals=slot.get('stats',{})
            prompt=(f'Card {self.column}: {vals.get("kills","?")} kills / '
                    f'{vals.get("stims_used","?")} stims • member or blank')
        else:
            prompt=f'Card {self.column}: statistics unreadable • reupload to link'
        super().__init__(placeholder=prompt, min_values=0, max_values=1,
                         row=row, disabled=not is_readable)

    async def callback(self, interaction:discord.Interaction):
        parent=self.view
        if not await parent.interaction_check(interaction):
            return
        if self.values:
            user=self.values[0]
            member=interaction.guild.get_member(user.id) if interaction.guild else None
            if member is None and interaction.guild:
                try:
                    member=await interaction.guild.fetch_member(user.id)
                except discord.HTTPException:
                    pass
            if member is None or member.bot:
                await deny(interaction,'Select an existing human member of this Discord server, or leave it blank for an outsider.')
                return
            if any(m.id == member.id for column,m in parent.assignments.items()
                   if column != self.column):
                await deny(interaction,'That contractor is already assigned to another player card.')
                return
            parent.assignments[self.column]=member
        else:
            # A blank selection is deliberately NOT an error or a default user.
            parent.assignments.pop(self.column,None)
        await interaction.response.edit_message(embed=parent.preview(),view=parent)


class PlayerAssignmentView(discord.ui.View):
    """One optional member selector per visible squad card; outsiders stay blank."""
    def __init__(self, owner_id:int,guild_id:int,contract_id:int,recipient_id:int,
                 screenshot:bytes,planet:str,difficulty:int,notes:str,
                 analysis:tuple[str,str,dict],kind:str='USER', operation_photo:bytes|None=None):
        super().__init__(timeout=840)
        from squad_ocr import player_assignment_slots
        self.owner_id,self.guild_id,self.contract_id=owner_id,guild_id,contract_id
        self.recipient_id,self.screenshot=recipient_id,screenshot
        self.planet,self.difficulty,self.notes=planet,difficulty,notes
        self.analysis,self.kind,self.operation_photo=analysis,kind,operation_photo
        self.assignments={}
        self.finished=False
        facts=analysis[2] if isinstance(analysis[2],dict) else {}
        self.players=player_assignment_slots(facts.get('players',[]),
                                             facts.get('player_card_count',0))
        for i,slot in enumerate(self.players):
            self.add_item(PlayerAssignmentSelect(slot,i))

    def preview(self):
        status,summary,_=self.analysis
        embed=blacksite_embed('▰ BLACKSITE / ASSIGN PLAYER STATISTICS',
            '**Every player card on the screenshot is listed below.** Link only players '
            'who belong to this Discord server/faction. **Leave a dropdown blank** when '
            'the player is not in Discord or not part of R.E.A.P.E.R. Divers.\n\n'
            'Blank players remain visible in this report, but receive **no personal stats '
            'or teammate payment**. Names are OCR guesses; **match by the card position '
            'and stats**, not by the guessed name. A player card with clear numbers '
            'can still be linked when the name is unreadable.\n\n'
            'Member links are uploader-reported, not independently verified.',color=GREEN)
        for slot in self.players:
            column,name,stats=slot['column'],slot['game_name'],slot['stats']
            member=self.assignments.get(column)
            if not slot['readable']:
                who='Statistics unreadable — reupload for stat credit'
                stats_line='Statistics unavailable for this player card'
            else:
                who=member.mention if member else 'Outside Discord / Not in faction (blank)'
                stats_line=(f'**{stats.get("kills","?")} kills**  •  '
                            f'**{stats.get("stims_used","?")} stims**  •  '
                            f'{stats.get("deaths","?")} deaths')
            label=f'PLAYER CARD {column}'
            guess=(f'\n*Unverified OCR name guess:* '
                   f'`{discord.utils.escape_markdown(discord.utils.escape_mentions(name[:45]))}`'
                   if name else '\n*OCR name:* unreadable')
            embed.add_field(name=f'▸ {label}',
                value=f'{stats_line}{guess}\n**Linked contractor:** {who}', inline=False)
        embed.add_field(name='▸ SCREENSHOT CHECK',
                        value=f'**{status}** — {summary[:350]}',inline=False)
        embed.set_image(url='attachment://mission-proof.png')
        embed.set_footer(text='Blank = outsider • Link only server members • 14-minute private session')
        return embed

    async def interaction_check(self,interaction:discord.Interaction):
        if interaction.user.id!=self.owner_id or interaction.guild_id!=self.guild_id:
            await deny(interaction,'This is another contractor’s private assignment session.')
            return False
        if self.finished:
            await deny(interaction,'This submission has already been finalized.')
            return False
        return True

    @discord.ui.button(label='Confirm & Submit',style=discord.ButtonStyle.success,emoji='✅',row=4)
    async def confirm(self,interaction:discord.Interaction,button:discord.ui.Button):
        # No assignment is valid: the screenshot may depict an outside squad.
        # It will be sent to staff review for individual contracts, rather than
        # silently treating the uploader as one of the pictured contractors.
        teammates=list({m.id:m for m in self.assignments.values()
                        if m.id!=self.owner_id}.values())
        if len(teammates)>3:
            await deny(interaction,'You may link up to three other server members.')
            return
        pairs=[(self.assignments[slot['column']].id,
                slot['game_name'] or f'Card {slot["column"]} (name unclear)')
               for slot in self.players
               if slot['readable'] and slot['column'] in self.assignments]
        player_columns={self.assignments[slot['column']].id:slot['column']
                        for slot in self.players
                        if slot['readable'] and slot['column'] in self.assignments}
        self.finished=True
        proof=self.screenshot;operation=self.operation_photo
        self.screenshot=b'';self.operation_photo=None
        self.stop()
        await submit_photo(interaction,self.contract_id,self.kind,self.recipient_id,
            proof,self.planet,self.difficulty,teammates,self.notes,
            player_links=pairs,player_columns=player_columns,
            precomputed_ocr=self.analysis,operation_photo=operation)

    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary,row=4)
    async def cancel(self,interaction:discord.Interaction,button:discord.ui.Button):
        self.finished=True;self.screenshot=b'';self.operation_photo=None;self.stop()
        await interaction.response.edit_message(content='Upload discarded. No credits or stats were recorded.',
                                                embed=None,view=None,attachments=[])


class OperationMemberPicker(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Optional: tag up to 3 participating Discord contractors',
                         min_values=0,max_values=3,row=0)

    async def callback(self,interaction:discord.Interaction):
        owner=self.view
        if not await owner.interaction_check(interaction):return
        members=[]
        for selection in self.values:
            member=interaction.guild.get_member(selection.id) if interaction.guild else None
            if member is None and interaction.guild:
                try:member=await interaction.guild.fetch_member(selection.id)
                except discord.HTTPException:pass
            if member is None or member.bot or member.id==owner.owner_id:
                await deny(interaction,'Tag only OTHER human members of the server.');return
            members.append(member)
        owner.selected=members
        await interaction.response.edit_message(embed=owner.preview(),view=owner)


class OperationSubmitView(discord.ui.View):
    def __init__(self,owner_id,guild_id,contract_id,recipient_id,photo,planet,difficulty,notes,analysis,kind):
        super().__init__(timeout=840)
        self.owner_id,self.guild_id,self.contract_id=owner_id,guild_id,contract_id
        self.recipient_id,self.photo=recipient_id,photo
        self.planet,self.difficulty,self.notes=planet,difficulty,notes
        self.analysis,self.kind=analysis,kind
        self.selected=[];self.finished=False
        if kind=='USER':self.add_item(OperationMemberPicker())

    def preview(self):
        status,summary,facts=self.analysis
        e=blacksite_embed('▰ BLACKSITE / OPERATION INTEL DROP',
             '**Operation summary received.** Check the extracted details and optionally '
             'tag participating contractors. One accepted image counts as one contract report.\n\n'
             f'**OCR:** {status} — {summary[:250]}\n'
             f'**Planet on screen:** {facts.get("planet_visible","Unknown")}\n'
             f'**Difficulty on screen:** {facts.get("difficulty_visible","Unknown")}\n'
             f'**Operation status:** {facts.get("operation_success","Unknown")}\n'
             f'**Tagged teammates:** {" ".join(m.mention for m in self.selected) or "None"}\n\n'
             'No individual kill or stim statistics will be invented from this summary.',color=GREEN)
        e.set_image(url='attachment://mission-proof.png')
        e.set_footer(text='Review the evidence • Confirm & Submit to release to the broker')
        return e

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.owner_id or interaction.guild_id!=self.guild_id or self.finished:
            await deny(interaction,'This operation submission is unavailable or already finalized.');return False
        return True

    @discord.ui.button(label='Confirm & Submit',style=discord.ButtonStyle.success,row=1)
    async def confirm(self,interaction,button):
        self.finished=True;photo=self.photo;self.photo=b'';self.stop()
        await submit_photo(interaction,self.contract_id,self.kind,self.recipient_id,
            photo,self.planet,self.difficulty,self.selected,self.notes,
            player_links=[],precomputed_ocr=self.analysis)

    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary,row=1)
    async def cancel(self,interaction,button):
        self.finished=True;self.photo=b'';self.stop()
        await interaction.response.edit_message(content='Operation upload cancelled.',embed=None,view=None,attachments=[])


async def begin_squad_link(interaction:discord.Interaction,contract_id:int,recipient_id:int,
                           attachment:discord.Attachment,planet:str,difficulty:int,notes:str,
                           kind:str='USER',operation_attachment:discord.Attachment|None=None):
    if not interaction.guild_id or not interaction.guild:
        await deny(interaction,'Use this inside your server.');return
    if not (2<=len(planet.strip())<=70 and 1<=difficulty<=10):
        await deny(interaction,'Enter a valid planet and difficulty (1–10).');return
    if attachment.size>MAX_PROOF_BYTES or (operation_attachment and operation_attachment.size>MAX_PROOF_BYTES):
        await deny(interaction,'Each screenshot must be smaller than 8 MB.');return
    await interaction.response.defer(ephemeral=True,thinking=True)
    try:
        photo=await attachment.read()
        mime=recognize_image(photo)
        if len(photo)>MAX_PROOF_BYTES or not mime:
            await interaction.followup.send('Upload a valid PNG, JPG or WebP screenshot.',ephemeral=True);return
        image_fingerprint(photo)
        operation_photo=None
        if operation_attachment:
            operation_photo=await operation_attachment.read()
            if len(operation_photo)>MAX_PROOF_BYTES or not recognize_image(operation_photo):
                await interaction.followup.send('The operation image must be a PNG/JPG/WebP under 8 MB.',ephemeral=True);return
            image_fingerprint(operation_photo)
    except (discord.HTTPException,ValueError,UnidentifiedImageError,Image.DecompressionBombError):
        await interaction.followup.send('Unable to read the uploaded images. Try a clear uncropped screenshot.',ephemeral=True);return
    offer=db.get_contract(interaction.guild_id,contract_id)
    claim=db.get_claim(interaction.guild_id,contract_id,kind,recipient_id)
    if not offer or offer['status']!='OPEN' or offer['payout_mode']!=kind or (claim and (
        claim['status']=='PENDING' or (claim['status']=='PAID' and not offer['repeatable']))):
        await interaction.followup.send('Contract unavailable, already pending, or already paid.',ephemeral=True);return
    mode=offer['proof_mode'] if 'proof_mode' in offer.keys() else 'STATS'
    if mode=='BOTH' and not operation_photo:
        await interaction.followup.send('This contract requires BOTH a player statistics image and an operation summary image.',ephemeral=True);return
    if mode=='OPERATION':
        analysis=await asyncio.to_thread(analyze_operation_sync,photo)
    else:
        stats_result=await asyncio.to_thread(analyze_photo_sync,photo,mime)
        op_result=await asyncio.to_thread(analyze_operation_sync,operation_photo) if operation_photo else None
        analysis=combine_evidence(stats_result,op_result,
                                  mode='BOTH' if operation_photo or mode=='BOTH' else 'STATS')
    if mode=='OPERATION':
        view=OperationSubmitView(interaction.user.id,interaction.guild_id,contract_id,
            recipient_id,photo,planet.strip(),difficulty,notes,analysis,kind)
        await interaction.followup.send(embed=view.preview(),view=view,
            file=discord.File(BytesIO(photo),filename='mission-proof.png'),ephemeral=True)
        return
    players=analysis[2].get('players',[])
    if len(players)>=2:
        view=PlayerAssignmentView(interaction.user.id,interaction.guild_id,contract_id,
            recipient_id,photo,planet.strip(),difficulty,notes,
            analysis,kind=kind,operation_photo=operation_photo)
        await interaction.followup.send(embed=view.preview(),view=view,
            file=discord.File(BytesIO(photo),filename='mission-proof.png'),ephemeral=True)
    else:
        view=SquadLinkView(interaction.user.id,interaction.guild_id,contract_id,recipient_id,
            photo,planet.strip(),difficulty,notes,kind=kind,precomputed_ocr=analysis)
        view.operation_photo=operation_photo
        await interaction.followup.send(embed=view.preview(),view=view,
            file=discord.File(BytesIO(photo),filename='mission-proof.png'),ephemeral=True)


async def announce_payout(guild_id:int,claim_id:int,recipients:list[int],details:str):
    """Mention paid Discord members in the contracts channel, once after actual payment."""
    claim=db.claim_by_id(guild_id,claim_id)
    if not claim or claim['recipient_kind']!='USER' or not recipients:
        return
    offer=db.get_contract(guild_id,claim['contract_id'])
    if not offer:
        return
    channel_id=offer['board_channel_id'] or db.setting(guild_id,'board_channel_id')
    if not channel_id:
        return
    attempt=db.latest_attempt(guild_id,claim_id)
    linked=db.attempt_player_links(guild_id,attempt['id']) if attempt else []
    identity_lines=[f'`{discord.utils.escape_markdown(discord.utils.escape_mentions(name))}` → <@{uid}>'
                    for uid,name in linked if uid in recipients]
    content=(f'✅ **BLACKSITE // PAYMENT RELEASED** • Contract #{offer["id"]}\n'
             + ' '.join(f'<@{rid}>' for rid in recipients)
             + '\n'+details)
    if identity_lines:
        content+='\n**Uploader-reported player links:** '+ ' · '.join(identity_lines)
    try:
        channel=await fetch_text_channel(channel_id)
        await channel.send(content=content[:1950],
            allowed_mentions=discord.AllowedMentions(users=True,roles=False,everyone=False))
    except (discord.HTTPException,discord.Forbidden,RuleError) as exc:
        log.warning('Payment succeeded, but recipient notification failed: %s',type(exc).__name__)

class EvidenceModal(discord.ui.Modal, title='BLACKSITE • Secure Intel Upload'):
    proof=discord.ui.Label(text='Mission results screenshot',
        description='One PNG, JPG or WebP image; maximum 8 MB.',
        component=discord.ui.FileUpload(required=True,min_values=1,max_values=1))
    planet=discord.ui.Label(text='Planet',
        component=discord.ui.TextInput(placeholder='e.g. Genesis Prime',min_length=2,max_length=70))
    difficulty=discord.ui.Label(text='Difficulty (1–10)',
        component=discord.ui.TextInput(placeholder='e.g. 7',min_length=1,max_length=2))
    operation_proof=discord.ui.Label(text='Operation completion screenshot (optional)',
        description='Include when contract requires BOTH screenshots.',
        component=discord.ui.FileUpload(required=False,min_values=0,max_values=1))
    notes=discord.ui.Label(text='Notes (optional)',
        component=discord.ui.TextInput(style=discord.TextStyle.short,
                                       required=False,max_length=300))

    def __init__(self,contract_id:int,kind:str,recipient_id:int):
        super().__init__(timeout=300)
        self.contract_id,self.kind,self.recipient_id=contract_id,kind,recipient_id

    async def on_submit(self,interaction:discord.Interaction):
        try:
            diff=int(str(self.difficulty.component.value or '').strip())
        except ValueError:
            await deny(interaction,'Difficulty must be a number between 1 and 10.')
            return
        files=self.proof.component.values
        if not files:
            await deny(interaction,'You must attach a screenshot.')
            return
        await begin_squad_link(interaction,self.contract_id,self.recipient_id,
            files[0],str(self.planet.component.value),diff,
            str(self.notes.component.value or ''),
            operation_attachment=(self.operation_proof.component.values or [None])[0])


class UnitEvidenceModal(discord.ui.Modal, title='BLACKSITE • Unit Intel Drop'):
    proof=discord.ui.Label(text='Mission results screenshot',
        description='One PNG, JPG or WebP image; maximum 8 MB.',
        component=discord.ui.FileUpload(required=True,min_values=1,max_values=1))
    planet=discord.ui.Label(text='Planet',
        component=discord.ui.TextInput(placeholder='e.g. Genesis Prime',min_length=2,max_length=70))
    difficulty=discord.ui.Label(text='Difficulty (1–10)',
        component=discord.ui.TextInput(placeholder='e.g. 7',min_length=1,max_length=2))
    operation_proof=discord.ui.Label(text='Operation completion screenshot (optional)',
        description='Include when contract requires BOTH screenshots.',
        component=discord.ui.FileUpload(required=False,min_values=0,max_values=1))
    notes=discord.ui.Label(text='Notes (optional)',
        component=discord.ui.TextInput(style=discord.TextStyle.paragraph,
                                       required=False,max_length=300))

    def __init__(self,contract_id:int,recipient_id:int):
        super().__init__(timeout=300)
        self.contract_id,self.recipient_id=contract_id,recipient_id

    async def on_submit(self,interaction:discord.Interaction):
        try:
            diff=int(str(self.difficulty.component.value or '').strip())
        except ValueError:
            await deny(interaction,'Difficulty must be a number between 1 and 10.')
            return
        files=self.proof.component.values
        if not files:
            await deny(interaction,'You must attach a screenshot.')
            return
        await begin_squad_link(interaction,self.contract_id,self.recipient_id,
            files[0],str(self.planet.component.value),diff,
            str(self.notes.component.value or ''),kind='UNIT',
            operation_attachment=(self.operation_proof.component.values or [None])[0])


def eligible_unit(interaction: discord.Interaction, role_id: int) -> bool:
    # Live Discord member roles are authoritative. Unit membership may be earned
    # through the unit's registered role OR any moderator-linked existing role.
    return bool(interaction.guild_id and isinstance(interaction.user,discord.Member) and
                db.member_in_unit(interaction.guild_id,role_id,
                                  (role.id for role in interaction.user.roles)))


def eligible_unit_ids(interaction: discord.Interaction, contract) -> list[int]:
    if interaction.guild_id is None or not isinstance(interaction.user,discord.Member):
        return []
    units = db.member_units(interaction.guild_id,
                            (role.id for role in interaction.user.roles))
    return [unit['role_id'] for unit in units
            if contract['target_unit_id'] is None or
               contract['target_unit_id'] == unit['role_id']]


class UnitPicker(discord.ui.View):
    def __init__(self, contract_id: int, action: str, role_ids: list[int], guild: discord.Guild):
        super().__init__(timeout=180)
        self.contract_id,self.action=contract_id,action
        select=discord.ui.Select(placeholder='Choose which unit will receive the payout',
            min_values=1,max_values=1,
            options=[discord.SelectOption(label=(guild.get_role(rid).name if guild.get_role(rid) else str(rid))[:100],
                                          value=str(rid)) for rid in role_ids[:25]])
        select.callback=self.on_selection
        self.add_item(select)

    async def on_selection(self, interaction: discord.Interaction):
        role_id=int(self.children[0].values[0])
        if not eligible_unit(interaction,role_id):
            await deny(interaction,'You must have a role paired with the selected unit.')
            return
        if self.action=='claim':
            ok,msg=db.claim(interaction.guild_id,self.contract_id,'UNIT',role_id,interaction.user.id)
            await interaction.response.send_message(msg,ephemeral=True)
            if ok:
                await refresh_board(interaction.guild_id,self.contract_id)
        else:
            await interaction.response.send_modal(UnitEvidenceModal(self.contract_id,role_id))


async def begin_contract_action(interaction: discord.Interaction, contract_id: int, action: str) -> None:
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    offer=db.get_contract(interaction.guild_id,contract_id)
    if offer is None or offer['status']!='OPEN':
        await deny(interaction,'This contract is unavailable.')
        return
    if offer['payout_mode']=='USER':
        if action=='claim':
            ok,note=db.claim(interaction.guild_id,contract_id,'USER',interaction.user.id,interaction.user.id)
            await interaction.response.send_message(note,ephemeral=True)
            if ok:
                await refresh_board(interaction.guild_id,contract_id)
        else:
            await interaction.response.send_modal(EvidenceModal(contract_id,'USER',interaction.user.id))
    else:
        ids=eligible_unit_ids(interaction,offer)
        if not ids:
            await deny(interaction,'This is a unit contract. You need a role paired with a registered unit.')
        elif len(ids)==1:
            rid=ids[0]
            if action=='claim':
                ok,note=db.claim(interaction.guild_id,contract_id,'UNIT',rid,interaction.user.id)
                await interaction.response.send_message(note,ephemeral=True)
                if ok:
                    await refresh_board(interaction.guild_id,contract_id)
            else:
                await interaction.response.send_modal(UnitEvidenceModal(contract_id,rid))
        else:
            await interaction.response.send_message('Select your unit:',ephemeral=True,
                view=UnitPicker(contract_id,action,ids,interaction.guild))


class ContractButton(discord.ui.Button):
    def __init__(self, contract_id: int, action: str):
        super().__init__(label={'claim':'Claim Contract','submit':'Upload Intel','intel':'Contract Intel','progress':'My Progress'}[action],
                         style=(discord.ButtonStyle.success if action=='claim' else
                                discord.ButtonStyle.primary if action=='submit' else discord.ButtonStyle.secondary),
                         custom_id=f'ledger:contract:{action}:{contract_id}')
        self.contract_id,self.action=contract_id,action

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action=='progress':
            row=db.get_contract(interaction.guild_id,self.contract_id) if interaction.guild_id else None
            if not row:
                await deny(interaction,'Contract not found.');return
            if row['payout_mode']=='USER':
                kind,rid='USER',interaction.user.id
            else:
                available=eligible_unit_ids(interaction,row)
                if len(available)!=1:
                    await deny(interaction,'Choose your unit using `/contract progress`.');return
                kind,rid='UNIT',available[0]
            p=db.progress_for(interaction.guild_id,self.contract_id,kind,rid)
            await interaction.response.send_message(
                f'▰ **BROKER / {contract_ref(self.contract_id)}**\n'
                f'{contract_id_banner(self.contract_id)}\n'
                f'**{p["count"]}/{p["required"]} missions** accepted for '
                f'{recipient_label(kind,rid)} • cycle **#{p["cycle"]}**\n'
                f'**{p["status"]}** • {"Repeatable" if p["repeatable"] else "One-time"}\n'
                + ('Claim the next cycle to earn again.' if p['status']=='PAID' and p['repeatable']
                   else 'Submit new mission proof to advance.' if p['status']!='PAID'
                   else 'Contract completed.'),ephemeral=True)
            return
        if self.action=='intel':
            row=db.get_contract(interaction.guild_id,self.contract_id)
            if not row:
                await deny(interaction,'Contract unavailable.')
            else:
                await interaction.response.send_message(embed=contract_embed(row),ephemeral=True)
        else:
            await begin_contract_action(interaction,self.contract_id,self.action)


class ContractView(discord.ui.View):
    def __init__(self, contract_id: int):
        super().__init__(timeout=None)
        self.add_item(ContractButton(contract_id,'claim'))
        self.add_item(ContractButton(contract_id,'submit'))
        self.add_item(ContractButton(contract_id,'intel'))
        self.add_item(ContractButton(contract_id,'progress'))


class RejectModal(discord.ui.Modal, title='Reject Contract Evidence'):
    reason=discord.ui.TextInput(label='Required changes / rejection reason',min_length=3,
                                max_length=500,style=discord.TextStyle.paragraph)

    def __init__(self,claim_id: int):
        super().__init__(timeout=300)
        self.claim_id=claim_id

    async def on_submit(self,interaction: discord.Interaction):
        await reject_claim(interaction,self.claim_id,str(self.reason.value).strip())


class ReviewButton(discord.ui.Button):
    def __init__(self,claim_id: int,action: str):
        super().__init__(label=('Approve All & Pay' if action=='approve' else
                                'Pay Submitter Only' if action=='approve_self' else 'Reject Proof'),
                         style=(discord.ButtonStyle.success if action=='approve' else
                                discord.ButtonStyle.secondary if action=='approve_self' else discord.ButtonStyle.danger),
                         custom_id=f'ledger:review:{action}:{claim_id}')
        self.claim_id,self.action=claim_id,action

    async def callback(self,interaction: discord.Interaction):
        if self.action in ('approve','approve_self'):
            await approve_claim(interaction,self.claim_id,with_teammates=self.action=='approve')
        else:
            if not await require_moderator(interaction):
                return
            await interaction.response.send_modal(RejectModal(self.claim_id))


class ReviewView(discord.ui.View):
    def __init__(self,claim_id: int):
        super().__init__(timeout=None)
        self.add_item(ReviewButton(claim_id,'approve'))
        self.add_item(ReviewButton(claim_id,'approve_self'))
        self.add_item(ReviewButton(claim_id,'reject'))


class ShopActionButton(discord.ui.Button):
    def __init__(self, shop_kind: str, action: str):
        prefix='unitshop' if shop_kind=='UNIT' else 'shop'
        label = ('Unit Balances' if shop_kind=='UNIT' else 'My Balance') if action=='balance' else 'Browse Catalog'
        super().__init__(label=label,
                         style=discord.ButtonStyle.primary if action=='browse' else discord.ButtonStyle.secondary,
                         custom_id=f'ledger:{prefix}:{action}')
        self.shop_kind,self.action=shop_kind,action

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await deny(interaction,'Use this in a server.')
            return
        if self.action=='browse':
            await interaction.response.send_message(
                embed=shop_embed(interaction.guild_id,self.shop_kind),
                view=ShopCatalogView(interaction.guild_id,self.shop_kind),ephemeral=True)
            return
        if self.shop_kind=='USER':
            amt=db.balance(interaction.guild_id,'USER',interaction.user.id)
            await interaction.response.send_message(f'You have **{money(interaction.guild_id, amt, full=True)}**.',ephemeral=True)
            return
        rows=db.units(interaction.guild_id)
        lines=[f"<@&{r['role_id']}> — {money(interaction.guild_id, db.balance(interaction.guild_id,'UNIT',r['role_id']))}"
               for r in rows if (moderator(interaction) or eligible_unit(interaction,r['role_id']))]
        await interaction.response.send_message('\n'.join(lines)[:1900] or 'No registered units available.',
                                                ephemeral=True)


class ShopView(discord.ui.View):
    def __init__(self, shop_kind: str = 'USER'):
        super().__init__(timeout=None)
        self.add_item(ShopActionButton(shop_kind,'browse'))
        self.add_item(ShopActionButton(shop_kind,'balance'))


# ---------------------------------------------------------------------------
# Interactive market terminals. The existing /shop commands remain available.
# Unit funds are accessible only to officers / moderators; buys are atomic.
# ---------------------------------------------------------------------------
class MarketHubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label='Contractor Market',emoji='🪙',style=discord.ButtonStyle.success)
    async def personal(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.edit_message(embed=shop_embed(interaction.guild_id,'USER'),
                                                view=ShopCatalogView(interaction.guild_id,'USER'))

    @discord.ui.button(label='Unit Supply Exchange',emoji='🛡️',style=discord.ButtonStyle.primary)
    async def unit(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.edit_message(embed=shop_embed(interaction.guild_id,'UNIT'),
                                                view=ShopCatalogView(interaction.guild_id,'UNIT'))


class ShopCatalogSelect(discord.ui.Select):
    def __init__(self, items):
        super().__init__(placeholder='Select an item to purchase',min_values=1,max_values=1,
            options=[discord.SelectOption(label=f'#{r["id"]} · {r["name"][:75]}',
                value=str(r['id']),description=f'{r["price"]:,} Blacksite Credits') for r in items[:25]],row=0)

    async def callback(self,interaction:discord.Interaction):
        self.view.selected_id=int(self.values[0])
        item=db.item(interaction.guild_id,self.view.selected_id,self.view.kind)
        if not item or not item['active']:
            await deny(interaction,'This listing is no longer active.')
            return
        stock='Unlimited' if item['stock'] is None else str(item['stock'])
        e=blacksite_embed(f'▰ MARKET LISTING / #{item["id"]} • {item["name"]}',
            f'{item["description"]}\n\n**PRICE:** {money(interaction.guild_id,item["price"],full=True)}\n'
            f'**AVAILABLE STOCK:** {stock}\n\n'
            'Purchase is deducted instantly. A moderator will fulfill the order.',
            color=GREEN)
        await interaction.response.edit_message(embed=e,view=self.view)


class ShopCatalogView(discord.ui.View):
    def __init__(self,guild_id:int,kind:str):
        super().__init__(timeout=300)
        self.kind=kind
        self.selected_id=None
        items=db.items(guild_id,limit=25,shop_kind=kind)
        if items:
            self.add_item(ShopCatalogSelect(items))

    @discord.ui.button(label='Purchase Selected',emoji='🧾',style=discord.ButtonStyle.success,row=1)
    async def purchase(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not self.selected_id:
            await deny(interaction,'Select an item from the dropdown first.')
            return
        item=db.item(interaction.guild_id,self.selected_id,self.kind)
        if not item or not item['active']:
            await deny(interaction,'This listing is no longer available.')
            return
        if self.kind=='USER':
            await interaction.response.send_modal(ShopPurchaseModal('USER',self.selected_id))
            return
        eligible=[u['role_id'] for u in db.units(interaction.guild_id)
                  if can_buy_for_unit(interaction,u['role_id'])]
        if not eligible:
            await deny(interaction,'You are not an authorized purchasing officer for any unit.')
        elif len(eligible)==1:
            await interaction.response.send_modal(ShopPurchaseModal('UNIT',self.selected_id,eligible[0]))
        else:
            await interaction.response.send_message('Select the treasury paying for the order:',
                view=UnitPurchasePicker(self.selected_id,eligible,interaction.guild),ephemeral=True)

    @discord.ui.button(label='Switch Market',style=discord.ButtonStyle.secondary,row=1)
    async def back(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.edit_message(embed=blacksite_embed(
            '▰ BLACKSITE / THE UNDERCOUNTER',
            'Select your market. Personal and unit credits are separate.',color=GOLD),
            view=MarketHubView())


class UnitPurchaseSelect(discord.ui.Select):
    def __init__(self,item_id:int,units:list[int],guild:discord.Guild):
        super().__init__(placeholder='Select the unit treasury',
            options=[discord.SelectOption(label=(guild.get_role(rid).name
                if guild.get_role(rid) else f'Unit {rid}')[:100],value=str(rid))
                     for rid in units[:25]],min_values=1,max_values=1)
        self.item_id=item_id

    async def callback(self,interaction:discord.Interaction):
        rid=int(self.values[0])
        if not can_buy_for_unit(interaction,rid):
            await deny(interaction,'This unit did not authorize you to spend its funds.')
            return
        await interaction.response.send_modal(ShopPurchaseModal('UNIT',self.item_id,rid))


class UnitPurchasePicker(discord.ui.View):
    def __init__(self,item_id:int,units:list[int],guild:discord.Guild):
        super().__init__(timeout=180)
        self.add_item(UnitPurchaseSelect(item_id,units,guild))


class ShopPurchaseModal(discord.ui.Modal,title='BLACKSITE • Confirm Purchase'):
    quantity=discord.ui.Label(text='Quantity to buy',
        description='Credits will be debited when you submit this order.',
        component=discord.ui.TextInput(placeholder='1',default='1',min_length=1,max_length=4))

    def __init__(self,kind:str,item_id:int,unit_id:int | None = None):
        super().__init__(timeout=300)
        self.kind,self.item_id,self.unit_id=kind,item_id,unit_id

    async def on_submit(self,interaction:discord.Interaction):
        if not interaction.guild_id:
            await deny(interaction,'Purchases must be made in a server.')
            return
        if self.kind=='UNIT' and not can_buy_for_unit(interaction,self.unit_id):
            await deny(interaction,'You are no longer authorized to use that treasury.')
            return
        try:
            quantity=int(str(self.quantity.component.value or '').strip())
            if not 1<=quantity<=100:
                raise ValueError('Quantity must be 1–100.')
            receipt,cost=db.purchase(interaction.guild_id,self.item_id,interaction.user.id,
                quantity,shop_kind=self.kind,recipient_id=self.unit_id)
        except (RuleError,ValueError) as exc:
            await deny(interaction,f'Order declined: {exc}')
            return
        await interaction.response.defer(ephemeral=True)
        await refresh_shop(interaction.guild_id,self.kind)
        item=db.item(interaction.guild_id,self.item_id,self.kind)
        e=blacksite_embed(f'▰ RECEIPT / ORDER #{receipt}',
            f'**Item:** {item["name"]}\n**Quantity:** {quantity}\n'
            f'**Debited:** {money(interaction.guild_id,cost,full=True)}\n'
            f'**Account:** {"Personal wallet" if self.kind=="USER" else f"Unit treasury <@&{self.unit_id}>"}\n\n'
            'Order transmitted. Market staff will fulfill it.',color=GREEN)
        await interaction.followup.send(embed=e,ephemeral=True)
        review=db.setting(interaction.guild_id,'review_channel_id')
        if review:
            try:
                channel=await fetch_text_channel(review)
                await channel.send(embed=blacksite_embed(f'▰ MARKET FULFILLMENT / #{receipt}',
                    f'<@{interaction.user.id}> ordered **{quantity} × {item["name"]}**\n'
                    f'Cost: {money(interaction.guild_id,cost)}\n'
                    f'Purchase type: **{self.kind}**\n'
                    f'Use `/unitshop fulfill` or `/shop fulfill` with `purchase_id:{receipt}`.',
                    color=GOLD))
            except (RuleError,discord.HTTPException):
                log.warning('LEDGER market order #%s could not be announced',receipt)


# ---------------------------------------------------------------------------
# Moderator-friendly setup panels. Channels and roles are selected from Discord's
# actual guild data, not typed manually. Panels are ephemeral and access checked.
# ---------------------------------------------------------------------------
CHANNEL_SLOTS = (
    ('board_channel_id', 'BLACK MARKET CONTRACTS', 'Contract board'),
    ('review_channel_id', 'Private proof review', 'Staff review'),
    ('shop_channel_id', 'Contractor Market', 'Individual shop'),
    ('unit_shop_channel_id', 'Unit Supply Exchange', 'Unit shop'),
    ('top_earners_channel_id', 'Top Earners • Live Leaderboard', 'Top earners'),
)


def channel_setup_embed(guild_id: int) -> discord.Embed:
    embed = discord.Embed(
        title='LEDGER • CHANNEL CONFIGURATION',
        description='Choose a text channel in each dropdown. **Each choice saves immediately.** '
                    'Existing configured channels are shown below. The proof review channel must be private. '
                    'This five-dropdown panel closes automatically after 10 minutes.',
        color=GREEN,
    )
    for key, title, _ in CHANNEL_SLOTS:
        value = db.setting(guild_id, key)
        embed.add_field(name=title, value=f'<#{value}>' if value else '⚠ Not configured', inline=False)
    embed.set_footer(text='Only LEDGER moderators and server managers can change these settings.')
    return embed


class ChannelSetupSelect(discord.ui.ChannelSelect):
    def __init__(self, key: str, label: str, row: int):
        super().__init__(channel_types=[discord.ChannelType.text], min_values=1, max_values=1,
                         placeholder=f'Select: {label}', row=row)
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not self.values:
            await deny(interaction, 'Choose a channel in your server.')
            return
        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id)
        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(selected.id)
            except discord.HTTPException:
                await deny(interaction, 'Could not access that channel.')
                return
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
            await deny(interaction, 'Choose a normal text channel in this server.')
            return
        member = interaction.guild.me
        if member is None and bot.user is not None:
            try:
                member = await interaction.guild.fetch_member(bot.user.id)
            except discord.HTTPException:
                pass
        if member is None:
            await deny(interaction, 'LEDGER could not check its permissions in this server.')
            return
        perms = channel.permissions_for(member)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links, perms.read_message_history)):
            await deny(interaction, 'LEDGER needs View Channel, Send Messages, Embed Links and Read Message History there.')
            return
        if self.key == 'review_channel_id':
            if channel.permissions_for(interaction.guild.default_role).view_channel:
                await deny(interaction, 'The review channel must hide messages from @everyone. Use a private staff channel.')
                return
            if not perms.attach_files:
                await deny(interaction, 'LEDGER also needs Attach Files in the private review channel.')
                return
        if self.key in ('shop_channel_id', 'unit_shop_channel_id'):
            other_key = 'unit_shop_channel_id' if self.key == 'shop_channel_id' else 'shop_channel_id'
            if channel.id == db.setting(interaction.guild_id, other_key):
                await deny(interaction, 'The individual and unit shops must use different channels.')
                return
        old = db.setting(interaction.guild_id, self.key)
        db.configure(interaction.guild_id, self.key, channel.id)
        # An already-published shop card is in the OLD channel. Do not try to
        # edit that card in the newly configured channel; publish again there.
        if channel.id != old and self.key == 'top_earners_channel_id':
            db.configure(interaction.guild_id,'top_earners_message_id',0)
        if channel.id != old and self.key in ('shop_channel_id', 'unit_shop_channel_id'):
            msg_key = 'shop_message_id' if self.key == 'shop_channel_id' else 'unit_shop_message_id'
            db.configure(interaction.guild_id, msg_key, 0)
        await interaction.response.edit_message(embed=channel_setup_embed(interaction.guild_id), view=self.view)
        if self.key == 'top_earners_channel_id':
            await publish_top_earners(interaction.guild_id)


class ChannelSetupView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        for row, (key, _, name) in enumerate(CHANNEL_SLOTS):
            self.add_item(ChannelSetupSelect(key, name, row))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not moderator(interaction):
            await deny(interaction, 'Only the moderator who opened this panel can use it.')
            return False
        return True


async def show_channel_setup(interaction: discord.Interaction) -> None:
    if not await require_moderator(interaction):
        return
    await interaction.response.send_message(embed=channel_setup_embed(interaction.guild_id),
                                            view=ChannelSetupView(interaction.user.id), ephemeral=True)


class CreateUnitsRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='Select unit roles (up to 25)', min_values=1,
                         max_values=25, row=0)

    async def callback(self, interaction: discord.Interaction):
        roles = list(self.values)
        invalid = [role for role in roles if role.is_default() or role.managed]
        if invalid:
            await deny(interaction, 'Choose regular existing roles, not @everyone or integration-managed roles.')
            return
        try:
            created, existing = db.register_units_from_roles(
                interaction.guild_id, [(role.id, role.name) for role in roles])
        except RuleError as exc:
            await deny(interaction, str(exc))
            return
        lines = []
        if created:
            lines.append('**New unit treasuries:** ' + ', '.join(f'<@&{rid}>' for rid in created))
        if existing:
            lines.append('**Already registered (preserved):** ' + ', '.join(f'<@&{rid}>' for rid in existing))
        lines.append('Members holding those roles are recognized **automatically**, including when roles change. '
                     'No extra Discord role assignments are needed.')
        if MEMBER_SYNC_ENABLED:
            asyncio.create_task(sync_guild_unit_roster(interaction.guild))
        await interaction.response.send_message('\n'.join(lines)[:1950], ephemeral=True)


class UnitSetupView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.add_item(CreateUnitsRoleSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not moderator(interaction):
            await deny(interaction, 'Only the moderator who opened this panel can use it.')
            return False
        return True


class UnitLinkPrimarySelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='First: choose the registered unit', min_values=1,
                         max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if not db.get_unit(interaction.guild_id, role.id):
            await deny(interaction, 'That role is not a registered unit. Use `/unit setup` first.')
            return
        self.view.selected_unit_id = role.id
        await interaction.response.send_message(
            f'Selected {role.mention}. Now select one or more **additional membership roles** in the second dropdown.',
            ephemeral=True)


class UnitLinkAliasSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='Then: choose extra member roles (up to 25)',
                         min_values=1, max_values=25, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        unit_id = self.view.selected_unit_id
        if not unit_id or not db.get_unit(interaction.guild_id, unit_id):
            await deny(interaction, 'Select the registered unit from the first dropdown before choosing member roles.')
            return
        added, already, failures = [], [], []
        for role in self.values:
            if role.is_default() or role.managed:
                failures.append(f'{role.name}: not an assignable member role')
                continue
            try:
                made = db.link_unit_role(interaction.guild_id, unit_id, role.id, interaction.user.id)
            except RuleError as exc:
                failures.append(f'{role.name}: {exc}')
                continue
            (added if made else already).append(role.id)
        lines = [f'**Unit:** <@&{unit_id}>']
        if added:
            lines.append('**Linked roles:** ' + ', '.join(f'<@&{rid}>' for rid in added))
        if already:
            lines.append('**Already linked:** ' + ', '.join(f'<@&{rid}>' for rid in already))
        if failures:
            lines.append('**Skipped:** ' + '; '.join(failures)[:850])
        lines.append('Members with the linked role(s) now count toward this unit automatically.')
        if MEMBER_SYNC_ENABLED:
            asyncio.create_task(sync_guild_unit_roster(interaction.guild))
        await interaction.response.send_message('\n'.join(lines)[:1950], ephemeral=True)


class UnitLinkView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.selected_unit_id: Optional[int] = None
        self.add_item(UnitLinkPrimarySelect())
        self.add_item(UnitLinkAliasSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not moderator(interaction):
            await deny(interaction, 'Only the moderator who opened this panel can use it.')
            return False
        return True


async def publish_new_contract(interaction: discord.Interaction, *, title: str,
                               details: str, reward: int, kind: str,
                               criteria: Optional[str], target_unit_id: Optional[int] = None,
                               objectives: Optional[list[dict]] = None,
                               required_submissions: int = 1,
                               repeatable: bool = False,
                               proof_mode: str = 'STATS',
                               public_goal: int = 0) -> bool:
    """Shared safe posting path for both individual and unit contract modals."""
    if not await require_moderator(interaction):
        return False
    if not (3 <= len(title) <= 100 and 10 <= len(details) <= 1500 and 1 <= reward <= 1_000_000):
        await deny(interaction, 'Title 3–100 chars; details 10–1500; reward 1–1,000,000.')
        return False
    if proof_mode not in ('STATS','OPERATION','BOTH') or not 0<=public_goal<=100000:
        await deny(interaction,'Choose valid screenshot format and optional community target.');return False
    if criteria is not None and not 8 <= len(criteria) <= 1000:
        await deny(interaction, 'Screenshot criteria must be 8–1000 characters.')
        return False
    if kind not in ('USER', 'UNIT') or (kind != 'UNIT' and target_unit_id):
        await deny(interaction, 'Invalid payout destination.')
        return False
    if target_unit_id and not db.get_unit(interaction.guild_id, target_unit_id):
        await deny(interaction, 'The target unit is not registered. Use `/unit setup`.')
        return False
    board_id = db.setting(interaction.guild_id, 'board_channel_id')
    if not board_id:
        await deny(interaction, 'Set the contract-board channel using `/ledger channels` first.')
        return False
    try:
        channel = await fetch_text_channel(board_id)
    except (discord.HTTPException, RuleError):
        await deny(interaction, 'The contract board channel is missing or inaccessible.')
        return False
    await interaction.response.defer(ephemeral=True)
    try:
        cid = db.create_contract(interaction.guild_id, title, details, reward, interaction.user.id,
                                 board_id, kind, target_unit_id, criteria, objectives=objectives,
                                 required_submissions=required_submissions, repeatable=repeatable)
        db.set_proof_mode(interaction.guild_id,cid,proof_mode)
        db.set_public_goal(interaction.guild_id,cid,public_goal)
    except RuleError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return False
    try:
        msg = await channel.send(embed=contract_embed(db.get_contract(interaction.guild_id, cid)),
                                 view=ContractView(cid))
    except discord.HTTPException:
        db.delete_unposted(interaction.guild_id, cid)
        await interaction.followup.send('Could not post the contract. Check LEDGER channel permissions.', ephemeral=True)
        return False
    db.attach_board(interaction.guild_id, cid, msg.id)
    await interaction.followup.send(f'**CONTRACT ID: `{cid}`** • `{contract_ref(cid)}` posted: {msg.jump_url}', ephemeral=True)
    await publish_dispatch(interaction.guild_id)
    return True


class ContractUnitSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='Choose a registered target unit', min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if not db.get_unit(interaction.guild_id, role.id):
            await deny(interaction, 'Choose a registered unit; use `/unit setup` to register roles.')
            return
        self.view.locked = True
        ok = await publish_new_contract(interaction, **self.view.payload, target_unit_id=role.id)
        if not ok:
            self.view.locked = False
        else:
            self.view.stop()


class ContractUnitTargetView(discord.ui.View):
    def __init__(self, owner_id: int, payload: dict):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.payload = payload
        self.locked = False
        self.add_item(ContractUnitSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not moderator(interaction):
            await deny(interaction, 'Only the moderator who created this draft can use it.')
            return False
        if self.locked:
            await deny(interaction, 'This contract is already being posted.')
            return False
        return True

    @discord.ui.button(label='Available to any registered unit', style=discord.ButtonStyle.success, row=1)
    async def any_unit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.locked = True
        ok = await publish_new_contract(interaction, **self.payload, target_unit_id=None)
        if not ok:
            self.locked = False
        else:
            self.stop()


class ContractCreateModal(discord.ui.Modal, title='BLACK MARKET • New Contract'):
    title_field = discord.ui.Label(text='Contract title',
        component=discord.ui.TextInput(placeholder='e.g. Emergency Extraction', min_length=3, max_length=100))
    details_field = discord.ui.Label(text='Contract description (display only)',
        component=discord.ui.TextInput(style=discord.TextStyle.paragraph,
                                       placeholder='Describe the contract; payout only needs a verified screenshot.',
                                       min_length=10, max_length=1500))
    reward_field = discord.ui.Label(text='Blacksite Credits per recipient',
        component=discord.ui.TextInput(placeholder='1500', min_length=1, max_length=7))
    payout_field = discord.ui.Label(text='Who receives the payout?',
        component=discord.ui.Select(placeholder='Choose payout type', min_values=1, max_values=1,
            options=[discord.SelectOption(label='Each individual contractor', value='USER'),
                     discord.SelectOption(label='Unit treasury', value='UNIT')]))
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_moderator(interaction):
            return
        try:
            reward = int(str(self.reward_field.component.value).strip())
        except ValueError:
            await deny(interaction, 'Reward must be a whole number of Blacksite Credits.')
            return
        payload = {
            'title': str(self.title_field.component.value).strip(),
            'details': str(self.details_field.component.value).strip(),
            'reward': reward,
            'kind': self.payout_field.component.values[0],
            'criteria': None,
            'required_submissions': 1,
            'repeatable': False,
            'proof_mode': 'STATS',
            'public_goal': 0,
        }
        draft=ContractDraftView(interaction.user.id,payload)
        await interaction.response.send_message(embed=draft.preview(interaction.guild_id),
                                                view=draft,ephemeral=True)


# ---------------------------------------------------------------------------
# The Broker's contract desk: draft, inspect optional bonuses, then publish.
# A modal can hold only five fields; the draft view supports up to 4 bonuses.
# ---------------------------------------------------------------------------
class BonusObjectiveModal(discord.ui.Modal, title='THE BROKER • Add Bonus Objective'):
    bonus_name = discord.ui.Label(text='Bonus objective name',
        component=discord.ui.TextInput(placeholder='e.g. Leave No One Behind',
                                       min_length=3,max_length=80))
    metric_field = discord.ui.Label(text='Stat to check in screenshot',
        component=discord.ui.Select(placeholder='Choose a readable mission stat',
            min_values=1,max_values=1,
            options=[discord.SelectOption(label=v.name,value=k) for k,v in METRICS.items()]))
    target_field = discord.ui.Label(text='Threshold (number)',
        component=discord.ui.TextInput(placeholder='e.g. 15',min_length=1,max_length=7))
    bonus_field = discord.ui.Label(text='Bonus payout (less than base)',
        component=discord.ui.TextInput(placeholder='e.g. 200',min_length=1,max_length=7))

    def __init__(self, *, draft=None, contract_id: int | None = None,
                 base_reward: int | None = None):
        super().__init__(timeout=300)
        self.draft=draft
        self.contract_id=contract_id
        self.base_reward=base_reward if base_reward is not None else draft.payload['reward']

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_moderator(interaction):
            return
        try:
            data=validate_bonus(title=str(self.bonus_name.component.value),
                metric=self.metric_field.component.values[0],
                target=int(str(self.target_field.component.value).strip()),
                reward=int(str(self.bonus_field.component.value).strip()),
                base_reward=self.base_reward)
        except (ValueError, IndexError) as exc:
            await deny(interaction,f'Bonus not saved: {exc}')
            return
        if self.draft:
            if self.draft.published or len(self.draft.bonuses)>=MAX_OBJECTIVES:
                await deny(interaction,'This draft was published, cancelled, or has no bonus slots.')
                return
            if any(x['title'].casefold()==data['title'].casefold() for x in self.draft.bonuses):
                await deny(interaction,'Bonus names must be unique.')
                return
            self.draft.bonuses.append(data)
            await interaction.response.edit_message(
                embed=self.draft.preview(interaction.guild_id),view=self.draft)
            return
        try:
            db.add_objective(interaction.guild_id,self.contract_id,data)
        except RuleError as exc:
            await deny(interaction,str(exc))
            return
        await interaction.response.defer(ephemeral=True)
        await refresh_board(interaction.guild_id,self.contract_id)
        await interaction.followup.send('Bonus objective added to the live contract.',ephemeral=True)


class ProgressTermsModal(discord.ui.Modal, title='THE BROKER • Contract Terms'):
    required_field = discord.ui.Label(text='Screenshots needed per completion (1–25)',
        component=discord.ui.TextInput(placeholder='Example: 3',min_length=1,max_length=2))
    repeat_field = discord.ui.Label(text='Can contractors repeat this contract?',
        component=discord.ui.Select(placeholder='Choose recurrence',min_values=1,max_values=1,
            options=[discord.SelectOption(label='Yes • restart after each payout',value='yes'),
                     discord.SelectOption(label='No • one completion per contractor',value='no')]))

    evidence_field = discord.ui.Label(text='Required screenshot format',
        component=discord.ui.Select(placeholder='Choose what OCR must read',min_values=1,max_values=1,
            options=[discord.SelectOption(label='Individual mission statistics',value='STATS'),
                     discord.SelectOption(label='Operation completion',value='OPERATION'),
                     discord.SelectOption(label='Both screenshots together',value='BOTH')]))
    goal_field = discord.ui.Label(text='Public network target (0 = off)',
        component=discord.ui.TextInput(placeholder='e.g. 50',min_length=1,max_length=6,
                                       default='0'))

    def __init__(self,draft):
        super().__init__(timeout=300)
        self.draft=draft

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_moderator(interaction):
            return
        try:
            required=int(str(self.required_field.component.value).strip())
            repeatable=self.repeat_field.component.values[0]=='yes'
            db._validate_progress_settings(required,repeatable)
            proof_mode=self.evidence_field.component.values[0]
            public_goal=int(str(self.goal_field.component.value or '0').strip())
            if proof_mode not in ('STATS','OPERATION','BOTH') or not 0<=public_goal<=100000:
                raise ValueError('Invalid format or public goal')
        except (ValueError,IndexError,RuleError) as exc:
            await deny(interaction,f'Invalid mission settings: {exc}')
            return
        self.draft.payload['required_submissions']=required
        self.draft.payload['repeatable']=repeatable
        self.draft.payload['proof_mode']=proof_mode
        self.draft.payload['public_goal']=public_goal
        await interaction.response.edit_message(embed=self.draft.preview(interaction.guild_id),
                                                view=self.draft)


class DraftUnitSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='Optional: restrict the contract to one registered unit',
                         min_values=0,max_values=1,row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            selected=self.values[0]
            if not db.get_unit(interaction.guild_id,selected.id):
                await deny(interaction,'That role is not a registered unit; use `/unit setup` first.')
                return
            self.view.target_unit_id=selected.id
        else:
            self.view.target_unit_id=None
        await interaction.response.edit_message(embed=self.view.preview(interaction.guild_id),
                                                view=self.view)


class ContractDraftView(discord.ui.View):
    def __init__(self, owner_id: int, payload: dict):
        super().__init__(timeout=600)
        self.owner_id=owner_id
        self.payload=payload
        self.bonuses=[]
        self.target_unit_id=None
        self.published=False
        if payload['kind']=='UNIT':
            self.add_item(DraftUnitSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.published or self.owner_id!=interaction.user.id or not moderator(interaction):
            await deny(interaction,'Only the moderator editing this active draft may use it.')
            return False
        return True

    def preview(self,guild_id:int):
        return draft_card(self.payload,self.bonuses,money,guild_id,self.target_unit_id)

    @discord.ui.button(label='Add Bonus',emoji='🎯',style=discord.ButtonStyle.primary,row=0)
    async def add_bonus(self,interaction:discord.Interaction,button:discord.ui.Button):
        if len(self.bonuses)>=MAX_OBJECTIVES:
            await deny(interaction,f'Maximum {MAX_OBJECTIVES} objectives per contract.')
            return
        await interaction.response.send_modal(BonusObjectiveModal(draft=self))

    @discord.ui.button(label='Undo Bonus',style=discord.ButtonStyle.secondary,row=0)
    async def remove_bonus(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not self.bonuses:
            await deny(interaction,'There are no bonuses to remove.')
            return
        self.bonuses.pop()
        await interaction.response.edit_message(embed=self.preview(interaction.guild_id),view=self)

    @discord.ui.button(label='Mission / OCR / Goal',emoji='🔁',
                       style=discord.ButtonStyle.secondary,row=2)
    async def set_progress(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_modal(ProgressTermsModal(self))

    @discord.ui.button(label='Publish Contract',emoji='📡',style=discord.ButtonStyle.success,row=0)
    async def publish(self,interaction:discord.Interaction,button:discord.ui.Button):
        self.published=True
        success=await publish_new_contract(interaction,**self.payload,
            target_unit_id=self.target_unit_id,objectives=list(self.bonuses))
        if success:
            self.stop()
            try:
                await interaction.message.edit(view=None)
            except (discord.HTTPException, AttributeError):
                pass
        else:
            self.published=False

    @discord.ui.button(label='Discard',style=discord.ButtonStyle.danger,row=0)
    async def discard(self,interaction:discord.Interaction,button:discord.ui.Button):
        self.published=True
        self.stop()
        await interaction.response.edit_message(content='Draft discarded. No contract was published.',
                                                embed=None,view=None)


class DossierActions(discord.ui.View):
    def __init__(self,owner_id:int):
        super().__init__(timeout=240)
        self.owner_id=owner_id

    async def interaction_check(self,interaction:discord.Interaction):
        if interaction.user.id!=self.owner_id:
            await deny(interaction,'Only the owner can edit this contractor dossier.');return False
        return True

    @discord.ui.button(label='Customize My Dossier',emoji='✏️',style=discord.ButtonStyle.success)
    async def edit(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_modal(DossierEditModal(interaction.guild_id,interaction.user.id))

    @discord.ui.button(label='View Mission Telemetry',emoji='📊',style=discord.ButtonStyle.secondary)
    async def telemetry(self,interaction:discord.Interaction,button:discord.ui.Button):
        await interaction.response.send_message(embed=stats_embed(interaction.guild_id,
            'YOUR MISSION TELEMETRY',db.proof_stats(interaction.guild_id,member_id=interaction.user.id)),
            ephemeral=True)


# Persistent navigation terminals (no extra permissions beyond a normal member).
class BlacksiteTerminal(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='My Dossier',emoji='🪪',style=discord.ButtonStyle.success,
                       custom_id='ledger:terminal:profile',row=0)
    async def dossier(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
            await deny(interaction,'Use this inside your Discord server.')
            return
        member=interaction.user
        embed=profile_card(member,member_dossier_data(interaction.guild_id,member.id),
            db.member_units(interaction.guild_id,[r.id for r in member.roles]),money,interaction.guild_id,
            db.member_handles(interaction.guild_id,member.id))
        await interaction.response.send_message(embed=embed,view=DossierActions(member.id),ephemeral=True)

    @discord.ui.button(label='Open Contracts',emoji='📋',style=discord.ButtonStyle.primary,
                       custom_id='ledger:terminal:contracts',row=0)
    async def contracts(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not interaction.guild_id:
            await deny(interaction,'Use this in the server.')
            return
        rows=db.conn.execute('''SELECT id,title,reward,board_channel_id,board_message_id
            FROM contracts WHERE guild_id=? AND status='OPEN' ORDER BY id DESC LIMIT 12''',
            (interaction.guild_id,)).fetchall()
        lines=[]
        for r in rows:
            link=(f'https://discord.com/channels/{interaction.guild_id}/{r["board_channel_id"]}/'
                  f'{r["board_message_id"]}' if r['board_channel_id'] and r['board_message_id'] else None)
            name=f'**#{r["id"]} {discord.utils.escape_markdown(r["title"][:65])}**'
            if link:
                name=f'[{name}]({link})'
            lines.append(f'{name}  •  {money(interaction.guild_id,r["reward"])} base')
        embed=blacksite_embed('▰ BROKER FEED / OPEN CONTRACTS',
            '\n'.join(lines) if lines else 'The contact has nothing available. Check back later.',color=TEAL)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    @discord.ui.button(label='Markets',emoji='🛒',style=discord.ButtonStyle.secondary,
                       custom_id='ledger:terminal:markets',row=0)
    async def markets(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not interaction.guild_id:
            await deny(interaction,'Use this in the server.')
            return
        e=blacksite_embed('▰ BLACKSITE / THE UNDERCOUNTER',
            'No receipts. No refunds. No questions. Private funds buy personal goods; unit funds buy unit supplies.\n'
            'Choose your market below to open an interactive catalog.',color=GOLD)
        for kind,label in [('USER','CONTRACTOR MARKET'),('UNIT','UNIT SUPPLY EXCHANGE')]:
            items=db.items(interaction.guild_id,limit=5,shop_kind=kind)
            lines=[f'`#{r["id"]}` **{discord.utils.escape_markdown(r["name"][:65])}** • '
                   f'{money(interaction.guild_id,r["price"])}' for r in items]
            e.add_field(name=label,value='\n'.join(lines) or 'No active listings.',inline=False)
        e.add_field(name='BUY',value='Use `/shop buy` or `/unitshop buy` for a listing ID.',inline=False)
        await interaction.response.send_message(embed=e,view=MarketHubView(),ephemeral=True)

    @discord.ui.button(label='My Units',emoji='🛡️',style=discord.ButtonStyle.secondary,
                       custom_id='ledger:terminal:units',row=1)
    async def units(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
            await deny(interaction,'Use this in the server.')
            return
        rows=db.member_units(interaction.guild_id,[role.id for role in interaction.user.roles])
        lines=[f'<@&{r["role_id"]}>  •  {money(interaction.guild_id,db.balance(interaction.guild_id,"UNIT",r["role_id"]))} in treasury'
               for r in rows]
        await interaction.response.send_message(embed=blacksite_embed(
            '▰ AFFILIATION RECORD', '\n'.join(lines) if lines else 'No unit role detected. Contact a unit lead.'),
            ephemeral=True)

    @discord.ui.button(label='Find a Mentor',emoji='🤝',style=discord.ButtonStyle.primary,
                       custom_id='ledger:terminal:mentor',row=1)
    async def mentor_request(self,interaction:discord.Interaction,button:discord.ui.Button):
        await begin_mentor_request(interaction)

    @discord.ui.button(label='Unlisted Intel',emoji='📻',style=discord.ButtonStyle.secondary,
                       custom_id='ledger:terminal:intel',row=2)
    async def easter_egg(self,interaction:discord.Interaction,button:discord.ui.Button):
        title,body,rare=intel_drop(interaction.user.id,discord.utils.utcnow().date().isoformat())
        e=blacksite_embed(f'▰ ENCRYPTED DROP / {title}',body,color=GREEN)
        if rare:e.set_footer(text='YOU WERE NOT SUPPOSED TO FIND THIS FREQUENCY.')
        await interaction.response.send_message(embed=e,ephemeral=True)

    @discord.ui.button(label='How It Works',emoji='🔎',style=discord.ButtonStyle.secondary,
                       custom_id='ledger:terminal:help',row=1)
    async def instructions(self,interaction:discord.Interaction,button:discord.ui.Button):
        e=blacksite_embed('▰ MESSAGE FROM THE BROKER',
            '**1.** Find work via **Open Contracts**.\n'
            '**2.** Claim a contract and upload one end-of-mission stats screenshot.\n'
            '**3.** After upload, tag participating Discord members and enter their in-game names. OCR verifies the results screen, not player identity.\n'
            '**4.** Collect base pay on PASS; earn bonus objectives when screenshot numbers qualify.\n'
            '**5.** Spend at the two Blacksite markets.\n'
            '**6.** New contractors can request a mentor with `/mentor request`.\n\n'
            'Names are user-linked to Discord members; OCR reads numbers but cannot prove authenticity. Flagged proof goes to market moderation.',
            color=GREEN)
        await interaction.response.send_message(embed=e,ephemeral=True)


class DossierEditModal(discord.ui.Modal,title='BLACKSITE / Customize Contractor Card'):
    def __init__(self,guild_id:int,user_id:int):
        super().__init__(timeout=300)
        self.guild_id,self.user_id=guild_id,user_id
        card=db.contractor_card(guild_id,user_id)
        self.fields=[]
        for label,name,cap,style in (
            ('Callsign','callsign',36,discord.TextStyle.short),
            ('Specialty / Job','specialty',50,discord.TextStyle.short),
            ('Contractor bio','bio',220,discord.TextStyle.paragraph),
            ('Personal motto','motto',120,discord.TextStyle.short)):
            field=discord.ui.TextInput(placeholder=f'Enter {label.lower()}',required=False,
                default=card.get(name) or None,max_length=cap,style=style)
            self.add_item(discord.ui.Label(text=label,component=field))
            self.fields.append((name,field))

    async def on_submit(self,interaction:discord.Interaction):
        if interaction.guild_id!=self.guild_id or interaction.user.id!=self.user_id:
            await deny(interaction,'You may only edit your own dossier.');return
        try:db.update_contractor_card(self.guild_id,self.user_id,
                          **{key:str(field.value or '') for key,field in self.fields})
        except RuleError as exc:
            await deny(interaction,str(exc));return
        await interaction.response.send_message(embed=profile_card(interaction.user,
            member_dossier_data(self.guild_id,self.user_id),
            db.member_units(self.guild_id,[r.id for r in interaction.user.roles]),
            money,self.guild_id,db.member_handles(self.guild_id,self.user_id)),ephemeral=True)


dossier_group=app_commands.Group(name='dossier',description='Your customizable Blacksite contractor card')

@dossier_group.command(name='edit',description='Customize your callsign, specialty, bio, and motto')
async def dossier_edit(interaction:discord.Interaction):
    if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
        await deny(interaction,'Use this command inside your server.');return
    await interaction.response.send_modal(DossierEditModal(interaction.guild_id,interaction.user.id))


@dossier_group.command(name='view',description='View a contractor’s Blacksite service record')
async def dossier_view(interaction:discord.Interaction,member:Optional[discord.Member]=None):
    if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
        await deny(interaction,'Use this command in your server.');return
    m=member or interaction.user
    await interaction.response.send_message(embed=profile_card(m,member_dossier_data(interaction.guild_id,m.id),
        db.member_units(interaction.guild_id,[r.id for r in m.roles]),money,interaction.guild_id,
        db.member_handles(interaction.guild_id,m.id)),ephemeral=True)


ledger_group=app_commands.Group(name='ledger',description='Configure LEDGER market moderation')
board_group=app_commands.Group(name='board',description='Configure Black Market bounty channels')
contract_group=app_commands.Group(name='contract',description='Claim, submit and moderate Black Market contracts')
unit_group=app_commands.Group(name='unit',description='Register unit treasuries and check balances')
credits_group=app_commands.Group(name='credits',description='Staff adjustments to Blacksite Credits')
shop_group=app_commands.Group(name='shop',description='Personal Contractor Market paid from individual wallets')
unitshop_group=app_commands.Group(name='unitshop',description='Unit Supply Exchange paid from unit treasuries')
mentor_group=app_commands.Group(name='mentor',description='Adopt a Contractor training and mentor contracts')


@ledger_group.command(name='create_mod_role',description='Create the LEDGER Market Moderation role (server manager only)')
async def ledger_create_mod_role(interaction:discord.Interaction):
    if not await require_manager(interaction):return
    existing=db.setting(interaction.guild_id,'moderator_role_id')
    if existing and interaction.guild.get_role(existing):
        await deny(interaction,f'Already configured: <@&{existing}>. Use `/ledger set_mod_role` to change it.')
        return
    try:
        role=await interaction.guild.create_role(name='LEDGER | Market Moderation',
                         reason=f'LEDGER configured by {interaction.user.id}')
    except discord.Forbidden:
        await deny(interaction,'The bot needs **Manage Roles**, and its own role must be sufficiently high.')
        return
    db.configure(interaction.guild_id,'moderator_role_id',role.id)
    await interaction.response.send_message(
        f'Created {role.mention}. **Assign it to your trusted moderation team in Discord → Server Settings → Roles.**',
        ephemeral=True)


@ledger_group.command(name='set_mod_role',description='Authorize an existing role to moderate LEDGER')
async def ledger_set_mod_role(interaction:discord.Interaction,role:discord.Role):
    if not await require_manager(interaction):return
    if role.is_default() or role.managed:
        await deny(interaction,'Choose a normal assignable server role.')
        return
    db.configure(interaction.guild_id,'moderator_role_id',role.id)
    await interaction.response.send_message(f'{role.mention} can now manage contracts, credits, units and shop listings.',ephemeral=True)


async def update_currency_displays(guild_id: int) -> None:
    """Refresh existing persistent contract cards and both published shops."""
    for row in db.board_contracts():
        if row['guild_id'] == guild_id:
            await refresh_board(guild_id, row['id'])
    await refresh_shop(guild_id, 'USER')
    await refresh_shop(guild_id, 'UNIT')


@ledger_group.command(name='install_credit_icon', description='Upload the supplied Blacksite Credits artwork as a server emoji')
async def ledger_install_credit_icon(interaction: discord.Interaction):
    if not await require_moderator(interaction):
        return
    guild = interaction.guild
    if guild is None:
        await deny(interaction, 'Use this command in your Discord server.')
        return
    old_id = db.setting(guild.id, 'currency_emoji_id')
    if old_id and guild.get_emoji(old_id):
        await deny(interaction, f'Blacksite Credits icon is already installed: {credit_icon(guild.id)}. '
                   'Use `/ledger set_credit_icon` to select a different uploaded emoji.')
        return
    if not EMOJI_FILE.is_file():
        await deny(interaction, 'The supplied icon file is missing from LEDGER/assets.')
        return
    await interaction.response.defer(ephemeral=True)
    try:
        created = await guild.create_custom_emoji(
            name='blacksite_credits', image=EMOJI_FILE.read_bytes(),
            reason=f'LEDGER Blacksite Credits icon installed by {interaction.user.id}')
    except discord.Forbidden:
        await interaction.followup.send(
            'LEDGER needs **Create Expressions / Manage Emojis** permission to upload. '
            'You can instead upload `assets/blacksite_credits_emoji.png` in Server Settings → Emoji, '
            'then use `/ledger set_credit_icon`.', ephemeral=True)
        return
    except discord.HTTPException as exc:
        await interaction.followup.send(
            f'Discord rejected the custom emoji upload ({exc}). Check your server emoji slots. '
            'You can upload the image manually and use `/ledger set_credit_icon`.', ephemeral=True)
        return
    db.configure(guild.id, 'currency_emoji_id', created.id)
    await update_currency_displays(guild.id)
    await interaction.followup.send(
        f'{created} **Blacksite Credits** is now LEDGER’s currency icon across both shops, '
        'balances, contract payouts and transaction history.', ephemeral=True)


@ledger_group.command(name='set_credit_icon',description='Use an existing server emoji as the Blacksite Credits icon')
@app_commands.describe(emoji='Paste the custom server emoji, e.g. <:blacksite_credits:123456789012345678>')
async def ledger_set_credit_icon(interaction: discord.Interaction, emoji: str):
    if not await require_moderator(interaction):
        return
    if not interaction.guild:
        await deny(interaction, 'Use this command in your server.')
        return
    parsed = discord.PartialEmoji.from_str(emoji.strip())
    existing = interaction.guild.get_emoji(parsed.id) if parsed.id else None
    if existing is None:
        await deny(interaction, 'Paste a custom emoji **from this server**, not a name or a standard emoji. '
                   'For example, type the emoji in chat, copy it, and paste it into this command.')
        return
    db.configure(interaction.guild_id, 'currency_emoji_id', existing.id)
    await interaction.response.defer(ephemeral=True)
    await update_currency_displays(interaction.guild_id)
    await interaction.followup.send(
        f'{existing} is now the Blacksite Credits icon throughout LEDGER.', ephemeral=True)


@ledger_group.command(name='channels', description='Configure all five LEDGER channels using dropdown menus')
async def ledger_channels(interaction: discord.Interaction):
    await show_channel_setup(interaction)


@ledger_group.command(name='refresh_top_earners', description='Refresh the public Top Earners board immediately')
async def ledger_refresh_top_earners(interaction: discord.Interaction):
    if not await require_moderator(interaction):
        return
    if not db.setting(interaction.guild_id,'top_earners_channel_id'):
        await deny(interaction,'Configure a Top Earners channel first with /ledger channels.')
        return
    await interaction.response.defer(ephemeral=True)
    ok=await publish_top_earners(interaction.guild_id)
    await interaction.followup.send('Top Earners board refreshed.' if ok else
        'Unable to refresh: check LEDGER channel permissions and bot logs.',ephemeral=True)



@ledger_group.command(name='ocr_status', description='Check whether local screenshot OCR is installed and ready')
async def ledger_ocr_status(interaction: discord.Interaction):
    if not await require_moderator(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    ok, message = await asyncio.to_thread(ocr_engine_status)
    await interaction.followup.send(('✅ ' if ok else '⚠️ ') + message, ephemeral=True)


@ledger_group.command(name='publish_dispatch',description='Create or refresh the live Blacksite contract progress board')
async def ledger_publish_dispatch(interaction:discord.Interaction,
                                  channel:Optional[discord.TextChannel]=None):
    if not await require_moderator(interaction):return
    if channel:
        db.configure(interaction.guild_id,'dispatch_channel_id',channel.id)
        db.configure(interaction.guild_id,'dispatch_message_id',0)
    await interaction.response.defer(ephemeral=True)
    ok=await publish_dispatch(interaction.guild_id)
    await interaction.followup.send('Blacksite Dispatch is live and refreshing every 2 minutes.' if ok else
        'Could not publish; configure `/ledger channels` or select a channel and check permissions.',ephemeral=True)


@ledger_group.command(name='refresh_dispatch',description='Force-refresh the live contract dispatch board')
async def ledger_refresh_dispatch(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    await interaction.response.defer(ephemeral=True)
    ok=await publish_dispatch(interaction.guild_id)
    await interaction.followup.send('Broker feed refreshed.' if ok else 'Could not refresh the feed.',ephemeral=True)


@ledger_group.command(name='settings',description='Show LEDGER channels and moderator role')
async def ledger_settings(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    info=[]
    for key,label,prefix in [('board_channel_id','Contract board','<#'),
                             ('review_channel_id','Staff review','<#'),
                             ('shop_channel_id','Contractor Market','<#'),
                             ('unit_shop_channel_id','Unit Supply Exchange','<#'),
                             ('top_earners_channel_id','Top Earners','<#'),
                             ('dispatch_channel_id','Live Dispatch','<#'),
                             ('moderator_role_id','Moderator role','<@&')]:
        val=db.setting(interaction.guild_id,key)
        info.append(f'**{label}:** {prefix}{val}>' if val else f'**{label}:** Not configured')
    info.append(f'**Blacksite Credits icon:** {credit_icon(interaction.guild_id) or "Not installed — run /ledger install_credit_icon"}')
    await interaction.response.send_message('\n'.join(info),ephemeral=True)


async def configure_text_channel(interaction:discord.Interaction,channel:discord.TextChannel,key:str) -> bool:
    if not await require_moderator(interaction):return False
    if interaction.guild.me is None:
        await deny(interaction,'Could not find the bot member in this server.')
        return False
    perms=channel.permissions_for(interaction.guild.me)
    if not all((perms.view_channel,perms.send_messages,perms.embed_links,perms.read_message_history)):
        await deny(interaction,'The bot needs View Channel, Send Messages, Embed Links and Read Message History there.')
        return False
    db.configure(interaction.guild_id,key,channel.id)
    await interaction.response.send_message(f'Configured {channel.mention}.',ephemeral=True)
    return True


@board_group.command(name='set_channel',description='Open LEDGER channel configuration dropdowns')
async def board_set_channel(interaction:discord.Interaction):
    await show_channel_setup(interaction)


@board_group.command(name='set_review_channel',description='Open LEDGER channel configuration dropdowns')
async def board_review_channel(interaction:discord.Interaction):
    await show_channel_setup(interaction)


@contract_group.command(name='post', description='Open a popup to create a Black Market contract')
async def contract_post(interaction: discord.Interaction):
    if not await require_moderator(interaction):
        return
    if not db.setting(interaction.guild_id, 'board_channel_id'):
        await deny(interaction, 'Configure the contract board with `/ledger channels` first.')
        return
    await interaction.response.send_modal(ContractCreateModal(timeout=300))


@contract_group.command(name='status',description='Show an open contract and the number of paid recipients')
async def contract_status(interaction:discord.Interaction,contract_id:int):
    row=db.get_contract(interaction.guild_id,contract_id) if interaction.guild_id else None
    if not row:
        await deny(interaction,'Unknown contract.')
        return
    await interaction.response.send_message(embed=contract_embed(row),ephemeral=True)


@contract_group.command(name='claim',description='Claim an open contract independently of other contractors')
async def contract_claim(interaction:discord.Interaction,contract_id:int,unit:Optional[discord.Role]=None):
    row=db.get_contract(interaction.guild_id,contract_id) if interaction.guild_id else None
    if not row:
        await deny(interaction,'Contract not found.')
        return
    if row['payout_mode']=='USER':
        if unit:
            await deny(interaction,'This is an individual contract, not a unit contract.')
            return
        await begin_contract_action(interaction,contract_id,'claim')
    elif unit:
        if not eligible_unit(interaction,unit.id):
            await deny(interaction,'You must have a Discord role paired with that registered unit.')
            return
        if row['target_unit_id'] and row['target_unit_id']!=unit.id:
            await deny(interaction,'The contract is for a different unit.')
            return
        ok,msg=db.claim(interaction.guild_id,contract_id,'UNIT',unit.id,interaction.user.id)
        await interaction.response.send_message(msg,ephemeral=True)
        if ok:await refresh_board(interaction.guild_id,contract_id)
    else:
        await begin_contract_action(interaction,contract_id,'claim')


@contract_group.command(name='submit',description='Upload proof, then link in-game players to Discord teammates')
async def contract_submit(interaction:discord.Interaction,contract_id:int,
                          unit:Optional[discord.Role]=None):
    row=db.get_contract(interaction.guild_id,contract_id) if interaction.guild_id else None
    if not row:
        await deny(interaction,'Contract not found.')
        return
    if row['payout_mode']=='USER':
        if unit:
            await deny(interaction,'This contract pays individual contractors.')
            return
        await interaction.response.send_modal(EvidenceModal(contract_id,'USER',interaction.user.id))
    elif unit:
        if not eligible_unit(interaction,unit.id):
            await deny(interaction,'You must belong to the selected unit.')
            return
        await interaction.response.send_modal(UnitEvidenceModal(contract_id,unit.id))
    else:
        await begin_contract_action(interaction,contract_id,'submit')


@contract_group.command(name='pending',description='List pending contract completions (moderators)')
async def contract_pending(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    claims=db.pending_claims(interaction.guild_id)
    if not claims:
        await interaction.response.send_message('No contract completions are awaiting review.',ephemeral=True)
        return
    lines=[f"**REVIEW ID `{c['id']}`** · **CONTRACT ID `{c['contract_id']}`** (`{contract_ref(c['contract_id'])}`) · "
           f"{recipient_label(c['recipient_kind'],c['recipient_id'])} · {money(interaction.guild_id, c['reward'])}\n"
           f"Proof: {(c['evidence'] or '')[:140]}" for c in claims]
    await interaction.response.send_message(('\n\n'.join(lines))[:1950],ephemeral=True)


@contract_group.command(name='public_goal',description='Moderator: set public community target for live dispatch (0 to disable)')
async def contract_public_goal(interaction:discord.Interaction,contract_id:int,goal:int):
    if not await require_moderator(interaction):return
    try:db.set_public_goal(interaction.guild_id,contract_id,goal)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,contract_id)
    await publish_dispatch(interaction.guild_id)
    await interaction.followup.send('Public dispatch target updated.',ephemeral=True)


@contract_group.command(name='evidence_type',description='Moderator: choose STATS, OPERATION, or BOTH proof types')
@app_commands.choices(mode=[app_commands.Choice(name='Mission statistics',value='STATS'),
                       app_commands.Choice(name='Operation summary',value='OPERATION'),
                       app_commands.Choice(name='Both screenshots',value='BOTH')])
async def contract_evidence_type(interaction:discord.Interaction,contract_id:int,
                                 mode:app_commands.Choice[str]):
    if not await require_moderator(interaction):return
    try:db.set_proof_mode(interaction.guild_id,contract_id,mode.value)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,contract_id)
    await interaction.followup.send(f'Contract now requires **{mode.value}** evidence.',ephemeral=True)


@contract_group.command(name='approve',description='Approve and pay screenshot submitter plus requested teammates')
async def contract_approve(interaction:discord.Interaction,review_id:int,
                           include_teammates:bool=True):
    await approve_claim(interaction,review_id,with_teammates=include_teammates)


@contract_group.command(name='reject',description='Reject a completion so the recipient can resubmit')
async def contract_reject(interaction:discord.Interaction,review_id:int,reason:str):
    if not 3<=len(reason.strip())<=500:
        await deny(interaction,'Reason must be 3–500 characters.')
        return
    await reject_claim(interaction,review_id,reason.strip())


@contract_group.command(name='progress',description='View your verified missions for a contract')
async def contract_progress(interaction:discord.Interaction,contract_id:int,
                            unit:Optional[discord.Role]=None):
    if not interaction.guild_id:
        await deny(interaction,'Use this command in your server.');return
    kind='UNIT' if unit else 'USER'
    rid=unit.id if unit else interaction.user.id
    if unit and not eligible_unit(interaction,unit.id):
        await deny(interaction,'You are not a member of that registered unit.');return
    try:
        p=db.progress_for(interaction.guild_id,contract_id,kind,rid)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message(
        f'▰ **BLACKSITE / {contract_ref(contract_id)}**\n'
        f'{contract_id_banner(contract_id)}\n'
        f'Progress: **{p["count"]}/{p["required"]}** verified mission(s)'
        f' • Cycle **#{p["cycle"]}** • Status **{p["status"]}**\n'
        f'Repeatable: **{"YES" if p["repeatable"] else "NO"}**\n'
        + ('Complete the remaining missions to release the base pay and accumulated bonuses.'
           if p['status']!='PAID' else
           ('Use `/contract claim` to begin another cycle.' if p['repeatable'] else
            'This one-time contract has been completed.')),
        ephemeral=True)


@contract_group.command(name='configure_progress',
                        description='Staff: set required screenshot count and repeatable terms')
async def contract_configure_progress(interaction:discord.Interaction,contract_id:int,
                                      required_missions:app_commands.Range[int,1,25],
                                      repeatable:bool):
    if not await require_moderator(interaction):return
    try:
        db.configure_progress(interaction.guild_id,contract_id,required_missions,repeatable)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,contract_id)
    await interaction.followup.send(
        f'Contract #{contract_id}: **{required_missions}** mission screenshot(s) per completion, '
        f'**{"repeatable" if repeatable else "one-time"}**. Settings are locked once proof is submitted.',
        ephemeral=True)


@contract_group.command(name='close',description='Stop new submissions for a contract (unpaid awards remain paid)')
async def contract_close(interaction:discord.Interaction,contract_id:int):
    if not await require_moderator(interaction):return
    if not db.set_contract_open(interaction.guild_id,contract_id,False):
        await deny(interaction,'Contract not found or already closed.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,contract_id)
    await interaction.followup.send('Contract closed. No new claims or approvals until reopened.',ephemeral=True)


@contract_group.command(name='reopen',description='Reopen a previously closed repeatable contract')
async def contract_reopen(interaction:discord.Interaction,contract_id:int):
    if not await require_moderator(interaction):return
    if not db.set_contract_open(interaction.guild_id,contract_id,True):
        await deny(interaction,'Contract not found or already open.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_board(interaction.guild_id,contract_id)
    await interaction.followup.send('Contract reopened; existing paid recipients cannot be paid twice.',ephemeral=True)


@unit_group.command(name='setup', description='Select multiple existing roles to create separate units automatically')
async def unit_setup(interaction: discord.Interaction):
    if not await require_moderator(interaction):
        return
    await interaction.response.send_message(
        '**LEDGER • UNIT REGISTRATION**\n'
        'Select up to **25 existing Discord roles** from the dropdown. Each selected role '
        'becomes its **own unit with its own treasury**. Members who already have the role '
        'will belong to the unit automatically; future role changes also update membership. '
        'Existing treasuries are preserved.',
        view=UnitSetupView(interaction.user.id), ephemeral=True)


@unit_group.command(name='link_menu', description='Link additional existing membership roles using dropdowns')
async def unit_link_menu(interaction: discord.Interaction):
    if not await require_moderator(interaction):
        return
    if not db.units(interaction.guild_id):
        await deny(interaction, 'Create your units first with `/unit setup`.')
        return
    await interaction.response.send_message(
        '**LEDGER • ADDITIONAL MEMBERSHIP ROLES**\n'
        'First select an existing unit, then choose up to 25 additional roles that count as members. '
        'Roles are not changed; LEDGER checks them automatically.',
        view=UnitLinkView(interaction.user.id), ephemeral=True)


@unit_group.command(name='register',description='Register a Discord role as a unit with its own treasury')
async def unit_register(interaction:discord.Interaction,role:discord.Role):
    if not await require_moderator(interaction):return
    if role.is_default() or role.managed:
        await deny(interaction,'Select a normal unit role.')
        return
    try:
        db.register_unit(interaction.guild_id,role.id,role.name)
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    await interaction.response.send_message(
        f'{role.mention} registered with a shared treasury. This primary role also counts as unit membership. '
        'Use `/unit link_role` to pair other existing Discord roles.',ephemeral=True)


@unit_group.command(name='link_role',description='Pair an existing Discord member role with a registered unit')
@app_commands.describe(unit='Registered unit / treasury role',member_role='Existing role that identifies members of that unit')
async def unit_link_role(interaction:discord.Interaction,unit:discord.Role,member_role:discord.Role):
    if not await require_moderator(interaction):return
    if member_role.is_default() or member_role.managed:
        await deny(interaction,'Choose an existing regular Discord role, not @everyone or an integration-managed role.')
        return
    try:
        created=db.link_unit_role(interaction.guild_id,unit.id,member_role.id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    if created:
        message = (f'{member_role.mention} is now paired with {unit.mention}. '
                   'Members holding this role are recognized for unit contracts and unit-shop eligibility. '
                   'Spending unit funds still requires the designated purchasing-officer role.')
    else:
        message = f'{member_role.mention} was already linked to {unit.mention}.'
    await interaction.response.send_message(message,ephemeral=True)


@unit_group.command(name='unlink_role',description='Remove a linked membership role from a unit')
@app_commands.describe(unit='Registered unit / treasury role',member_role='Existing membership role to unlink')
async def unit_unlink_role(interaction:discord.Interaction,unit:discord.Role,member_role:discord.Role):
    if not await require_moderator(interaction):return
    try:
        removed=db.unlink_unit_role(interaction.guild_id,unit.id,member_role.id)
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    await interaction.response.send_message(
        f'{member_role.mention} unlinked from {unit.mention}. Discord roles were not changed.'
        if removed else 'That role is not linked to that unit.',ephemeral=True)


@unit_group.command(name='roles',description='View the Discord roles currently identifying unit members')
async def unit_roles(interaction:discord.Interaction,unit:discord.Role):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    if not db.get_unit(interaction.guild_id,unit.id):
        await deny(interaction,'Register that unit first using `/unit register`.')
        return
    linked=db.unit_member_roles(interaction.guild_id,unit.id)
    roles=', '.join(f'<@&{rid}>' for rid in linked) if linked else 'None yet'
    await interaction.response.send_message(
        f'**{unit.mention}** membership mapping\nPrimary role: {unit.mention} (always included)\n'
        f'Additional linked roles: {roles}',ephemeral=True)


@unit_group.command(name='check_member',description='Show which registered units a contractor belongs to via Discord roles')
async def unit_check_member(interaction:discord.Interaction,member:discord.Member):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    memberships=db.member_units(interaction.guild_id,(r.id for r in member.roles))
    names=', '.join(f"<@&{u['role_id']}>" for u in memberships) if memberships else 'No mapped units'
    await interaction.response.send_message(
        f'{member.mention} belongs to: {names}',ephemeral=True)


@unit_group.command(name='set_buyer_role',description='Authorize unit leadership to spend its treasury in the unit shop')
@app_commands.describe(unit='Registered unit role',buyer_role='Role permitted to make unit purchases; omit to disable purchases by members')
async def unit_set_buyer_role(interaction:discord.Interaction,unit:discord.Role,
                              buyer_role:Optional[discord.Role]=None):
    if not await require_moderator(interaction):return
    if buyer_role and (buyer_role.is_default() or buyer_role.managed):
        await deny(interaction,'Choose a normal Discord role for authorized unit purchasers.')
        return
    if not db.set_unit_buyer_role(interaction.guild_id,unit.id,buyer_role.id if buyer_role else None):
        await deny(interaction,'Register that unit first using `/unit register`.')
        return
    await interaction.response.send_message(
        f"{unit.mention} purchasing officers: {buyer_role.mention if buyer_role else 'None (moderators only)'}. "
        'Purchasers must also hold the unit’s primary role or a linked membership role.',ephemeral=True)


def can_buy_for_unit(interaction:discord.Interaction,role_id:int) -> bool:
    if interaction.guild_id is None or not db.get_unit(interaction.guild_id,role_id):
        return False
    if moderator(interaction):
        return True
    if not eligible_unit(interaction,role_id):
        return False
    unit=db.get_unit(interaction.guild_id,role_id)
    return bool(unit['buyer_role_id'] and isinstance(interaction.user,discord.Member) and
                any(role.id==unit['buyer_role_id'] for role in interaction.user.roles))


@unit_group.command(name='list',description='List registered units and treasuries')
async def unit_list(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    rows=db.units(interaction.guild_id)
    entries=[f"<@&{r['role_id']}> — {money(interaction.guild_id, db.balance(interaction.guild_id,'UNIT',r['role_id']))} "
             f"· {len(db.unit_member_roles(interaction.guild_id,r['role_id']))} extra linked roles" for r in rows]
    await interaction.response.send_message(('\n'.join(entries) or 'No registered units.')[:1950],ephemeral=True)


@unit_group.command(name='balance',description='Check the shared balance for a registered unit')
async def unit_balance(interaction:discord.Interaction,role:discord.Role):
    if not interaction.guild_id or not db.get_unit(interaction.guild_id,role.id):
        await deny(interaction,'That role is not a registered unit.')
        return
    amt=db.balance(interaction.guild_id,'UNIT',role.id)
    await interaction.response.send_message(f'{role.mention} treasury: **{money(interaction.guild_id, amt, full=True)}**.',ephemeral=True)


async def credit_adjust(interaction:discord.Interaction,amount:int,reason:str,
                        member:Optional[discord.Member],unit:Optional[discord.Role],positive:bool):
    if not await require_moderator(interaction):return
    if amount<1 or amount>1_000_000 or not 3<=len(reason.strip())<=200:
        await deny(interaction,'Amount must be 1–1,000,000 and reason 3–200 characters.')
        return
    if (member is None)==(unit is None):
        await deny(interaction,'Choose **exactly one** recipient: `member` OR `unit`.')
        return
    kind,rid=('USER',member.id) if member else ('UNIT',unit.id)
    delta=amount if positive else -amount
    try:
        current=db.adjust(interaction.guild_id,kind,rid,delta,interaction.user.id,reason.strip())
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    await interaction.response.send_message(
        f"{'Added' if positive else 'Subtracted'} **{money(interaction.guild_id, amount, full=True)}** "
        f"{'to' if positive else 'from'} {recipient_label(kind,rid)}. New balance: **{money(interaction.guild_id, current)}**. "
        'Adjustment recorded in the ledger.',ephemeral=True)


@credits_group.command(name='add',description='Moderation team: credit a member or unit treasury')
async def credits_add(interaction:discord.Interaction,amount:int,reason:str,
                      member:Optional[discord.Member]=None,unit:Optional[discord.Role]=None):
    await credit_adjust(interaction,amount,reason,member,unit,True)


@credits_group.command(name='subtract',description='Moderation team: deduct credits from a member or unit')
async def credits_subtract(interaction:discord.Interaction,amount:int,reason:str,
                           member:Optional[discord.Member]=None,unit:Optional[discord.Role]=None):
    await credit_adjust(interaction,amount,reason,member,unit,False)


@shop_group.command(name='set_channel',description='Open LEDGER channel configuration dropdowns')
async def shop_channel(interaction:discord.Interaction):
    await show_channel_setup(interaction)


@shop_group.command(name='publish',description='Publish the current shop catalog in its configured channel')
async def shop_publish(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    channel_id=db.setting(interaction.guild_id,'shop_channel_id')
    if not channel_id:
        await deny(interaction,'Configure `/shop set_channel` first.')
        return
    try:
        channel=await fetch_text_channel(channel_id)
        msg=await channel.send(embed=shop_embed(interaction.guild_id),view=ShopView('USER'))
    except (discord.HTTPException,RuleError):
        await deny(interaction,'Unable to publish the shop. Check channel permissions.')
        return
    db.configure(interaction.guild_id,'shop_message_id',msg.id)
    await interaction.response.send_message(f'Black Market Exchange published: {msg.jump_url}',ephemeral=True)


@shop_group.command(name='add',description='Add a purchasable listing to the shop')
@app_commands.describe(stock='-1 means unlimited stock, 0 means sold out')
async def shop_add(interaction:discord.Interaction,name:str,description:str,price:int,stock:int=-1):
    if not await require_moderator(interaction):return
    name,description=name.strip(),description.strip()
    if not (2<=len(name)<=80 and 3<=len(description)<=250 and 1<=price<=1_000_000 and -1<=stock<=1_000_000):
        await deny(interaction,'Name 2–80 chars; description 3–250; price 1–1,000,000; stock -1 to 1,000,000.')
        return
    item_id=db.add_item(interaction.guild_id,name,description,price,None if stock==-1 else stock,
                        interaction.user.id)
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id)
    await interaction.followup.send(f'Added **#{item_id} — {name}** to the Black Market Exchange.',ephemeral=True)


@shop_group.command(name='edit',description='Update an existing shop listing')
async def shop_edit(interaction:discord.Interaction,item_id:int,
                    name:Optional[str]=None,description:Optional[str]=None,price:Optional[int]=None):
    if not await require_moderator(interaction):return
    if name is None and description is None and price is None:
        await deny(interaction,'Supply at least one field to edit.')
        return
    if ((name is not None and not 2<=len(name.strip())<=80) or
        (description is not None and not 3<=len(description.strip())<=250) or
        (price is not None and not 1<=price<=1_000_000)):
        await deny(interaction,'Invalid name, description or price.')
        return
    ok=db.update_item(interaction.guild_id,item_id,name=name.strip() if name else None,
                      description=description.strip() if description else None,price=price)
    if not ok:
        await deny(interaction,'Listing not found or already removed.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id)
    await interaction.followup.send('Listing updated.',ephemeral=True)


@shop_group.command(name='remove',description='Remove a listing from sale without deleting purchase receipts')
async def shop_remove(interaction:discord.Interaction,item_id:int):
    if not await require_moderator(interaction):return
    if not db.remove_item(interaction.guild_id,item_id):
        await deny(interaction,'Listing not found or already removed.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id)
    await interaction.followup.send('Listing removed from the catalog.',ephemeral=True)


@shop_group.command(name='stock_add',description='Increase an item’s inventory')
async def shop_stock_add(interaction:discord.Interaction,item_id:int,quantity:int):
    await stock_change(interaction,item_id,quantity,True)


@shop_group.command(name='stock_subtract',description='Decrease an item’s inventory')
async def shop_stock_subtract(interaction:discord.Interaction,item_id:int,quantity:int):
    await stock_change(interaction,item_id,quantity,False)


async def stock_change(interaction:discord.Interaction,item_id:int,quantity:int,add:bool,
                       shop_kind:str='USER'):
    if not await require_moderator(interaction):return
    if not 1<=quantity<=1_000_000:
        await deny(interaction,'Quantity must be 1–1,000,000.')
        return
    try:
        stock=db.change_stock(interaction.guild_id,item_id,quantity if add else -quantity,
                              shop_kind=shop_kind)
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id)
    await interaction.followup.send(f'Inventory updated: **{stock}** remaining.',ephemeral=True)


@shop_group.command(name='stock_set',description='Set stock to a finite quantity, or -1 for unlimited')
async def shop_stock_set(interaction:discord.Interaction,item_id:int,stock:int):
    if not await require_moderator(interaction):return
    if not -1<=stock<=1_000_000:
        await deny(interaction,'Set stock from -1 (unlimited) to 1,000,000.')
        return
    if not db.set_stock(interaction.guild_id,item_id,None if stock==-1 else stock):
        await deny(interaction,'Item not found.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id)
    await interaction.followup.send('Stock set.',ephemeral=True)


@shop_group.command(name='browse',description='Browse the Black Market Exchange')
async def shop_browse(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    await interaction.response.send_message(shop_catalog(interaction.guild_id),ephemeral=True)


@shop_group.command(name='buy',description='Purchase a listing using Blacksite Credits')
async def shop_buy(interaction:discord.Interaction,item_id:int,quantity:int=1):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    try:
        purchase_id,cost=db.purchase(interaction.guild_id,item_id,interaction.user.id,quantity)
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id)
    await interaction.followup.send(
        f'**Purchase #{purchase_id} placed.** Debited **{money(interaction.guild_id, cost, full=True)}**. '
        'Your item is awaiting fulfillment by the moderation team.',ephemeral=True)
    review_id=db.setting(interaction.guild_id,'review_channel_id')
    if review_id:
        try:
            channel=await fetch_text_channel(review_id)
            item=db.item(interaction.guild_id,item_id)
            await channel.send(f"**LEDGER SHOP ORDER #{purchase_id}** · <@{interaction.user.id}> "
                               f"purchased {quantity} × **{item['name']}** for {money(interaction.guild_id, cost)}. "
                               f"Use `/shop fulfill purchase_id:{purchase_id}` to mark delivered.")
        except (discord.HTTPException,RuleError):
            log.warning('Could not post shop receipt #%d to staff review channel',purchase_id)


@shop_group.command(name='orders',description='List outstanding shop purchases for manual fulfillment')
async def shop_orders(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    rows=db.pending_purchases(interaction.guild_id)
    if not rows:
        await interaction.response.send_message('No pending shop orders.',ephemeral=True)
        return
    lines=[f"**#{r['id']}** · <@{r['user_id']}> · {r['quantity']} × {r['item_name']} "
           f"({money(interaction.guild_id, r['total_price'])})" for r in rows]
    await interaction.response.send_message('\n'.join(lines)[:1900],ephemeral=True)


@shop_group.command(name='fulfill',description='Mark a shop purchase as manually delivered')
async def shop_fulfill(interaction:discord.Interaction,purchase_id:int):
    if not await require_moderator(interaction):return
    if not db.fulfill(interaction.guild_id,purchase_id,interaction.user.id):
        await deny(interaction,'Purchase not found or already fulfilled.')
        return
    await interaction.response.send_message(f'Purchase **#{purchase_id}** marked delivered.',ephemeral=True)


# Unit Supply Exchange is a different storefront and never charges a personal wallet.
# Item IDs and receipt IDs are globally unique, but all shop actions are scoped to
# their own catalog, so a /shop ID cannot be managed or purchased via /unitshop.

@unitshop_group.command(name='set_channel',description='Open LEDGER channel configuration dropdowns')
async def unitshop_channel(interaction:discord.Interaction):
    await show_channel_setup(interaction)


@unitshop_group.command(name='publish',description='Publish the Unit Supply Exchange catalog')
async def unitshop_publish(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    channel_id=db.setting(interaction.guild_id,'unit_shop_channel_id')
    if not channel_id:
        await deny(interaction,'Run `/unitshop set_channel` first.')
        return
    try:
        channel=await fetch_text_channel(channel_id)
        msg=await channel.send(embed=shop_embed(interaction.guild_id,'UNIT'),view=ShopView('UNIT'))
    except (discord.HTTPException,RuleError):
        await deny(interaction,'Unable to publish unit shop. Check channel permissions.')
        return
    db.configure(interaction.guild_id,'unit_shop_message_id',msg.id)
    await interaction.response.send_message(f'Unit Supply Exchange published: {msg.jump_url}',ephemeral=True)


@unitshop_group.command(name='add',description='List an item available only to unit treasuries')
@app_commands.describe(stock='-1 = unlimited stock; 0 = sold out')
async def unitshop_add(interaction:discord.Interaction,name:str,description:str,price:int,stock:int=-1):
    if not await require_moderator(interaction):return
    name,description=name.strip(),description.strip()
    if not (2<=len(name)<=80 and 3<=len(description)<=250 and 1<=price<=1_000_000 and -1<=stock<=1_000_000):
        await deny(interaction,'Name 2–80 chars; description 3–250; price 1–1,000,000; stock -1 to 1,000,000.')
        return
    item_id=db.add_item(interaction.guild_id,name,description,price,None if stock==-1 else stock,
                        interaction.user.id,shop_kind='UNIT')
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id,'UNIT')
    await interaction.followup.send(f'Added **unit item #{item_id} — {name}**.',ephemeral=True)


@unitshop_group.command(name='edit',description='Change a Unit Supply Exchange listing')
async def unitshop_edit(interaction:discord.Interaction,item_id:int,
                        name:Optional[str]=None,description:Optional[str]=None,price:Optional[int]=None):
    if not await require_moderator(interaction):return
    if name is None and description is None and price is None:
        await deny(interaction,'Supply at least one field to edit.')
        return
    if ((name is not None and not 2<=len(name.strip())<=80) or
        (description is not None and not 3<=len(description.strip())<=250) or
        (price is not None and not 1<=price<=1_000_000)):
        await deny(interaction,'Invalid name, description or price.')
        return
    if not db.update_item(interaction.guild_id,item_id,name=name.strip() if name else None,
                          description=description.strip() if description else None,
                          price=price,shop_kind='UNIT'):
        await deny(interaction,'Unit listing not found or removed.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id,'UNIT')
    await interaction.followup.send('Unit listing updated.',ephemeral=True)


@unitshop_group.command(name='remove',description='Remove a listing from the Unit Supply Exchange')
async def unitshop_remove(interaction:discord.Interaction,item_id:int):
    if not await require_moderator(interaction):return
    if not db.remove_item(interaction.guild_id,item_id,shop_kind='UNIT'):
        await deny(interaction,'Unit listing not found or already removed.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id,'UNIT')
    await interaction.followup.send('Unit listing removed.',ephemeral=True)


@unitshop_group.command(name='stock_add',description='Increase unit-shop inventory')
async def unitshop_stock_add(interaction:discord.Interaction,item_id:int,quantity:int):
    await stock_change(interaction,item_id,quantity,True,shop_kind='UNIT')


@unitshop_group.command(name='stock_subtract',description='Decrease unit-shop inventory')
async def unitshop_stock_subtract(interaction:discord.Interaction,item_id:int,quantity:int):
    await stock_change(interaction,item_id,quantity,False,shop_kind='UNIT')


@unitshop_group.command(name='stock_set',description='Set unit item stock; -1 means unlimited')
async def unitshop_stock_set(interaction:discord.Interaction,item_id:int,stock:int):
    if not await require_moderator(interaction):return
    if not -1<=stock<=1_000_000:
        await deny(interaction,'Set stock from -1 (unlimited) to 1,000,000.')
        return
    if not db.set_stock(interaction.guild_id,item_id,None if stock==-1 else stock,shop_kind='UNIT'):
        await deny(interaction,'Unit item not found.')
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id,'UNIT')
    await interaction.followup.send('Unit-shop stock set.',ephemeral=True)


@unitshop_group.command(name='browse',description='View items available for unit treasury purchase')
async def unitshop_browse(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    await interaction.response.send_message(shop_catalog(interaction.guild_id,'UNIT'),ephemeral=True)


@unitshop_group.command(name='buy',description='Use a unit treasury to purchase supplies (authorized buyers only)')
@app_commands.describe(unit='The registered unit treasury paying for this order')
async def unitshop_buy(interaction:discord.Interaction,item_id:int,unit:discord.Role,quantity:int=1):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    if not can_buy_for_unit(interaction,unit.id):
        await deny(interaction,'You must hold the unit’s buyer role and a mapped unit membership role, '
                   'or LEDGER Market Moderation staff, to spend this treasury.')
        return
    try:
        purchase_id,cost=db.purchase(interaction.guild_id,item_id,interaction.user.id,quantity,
                                     shop_kind='UNIT',recipient_id=unit.id)
    except RuleError as exc:
        await deny(interaction,str(exc))
        return
    await interaction.response.defer(ephemeral=True)
    await refresh_shop(interaction.guild_id,'UNIT')
    await interaction.followup.send(
        f'**Unit order #{purchase_id} placed** for {unit.mention}. '
        f'Debited **{money(interaction.guild_id, cost, full=True)}** from its **unit treasury**. Awaiting staff fulfillment.',
        ephemeral=True)
    review_id=db.setting(interaction.guild_id,'review_channel_id')
    if review_id:
        try:
            channel=await fetch_text_channel(review_id)
            item=db.item(interaction.guild_id,item_id,'UNIT')
            await channel.send(f'**LEDGER UNIT ORDER #{purchase_id}** · <@{interaction.user.id}> '
                               f'ordered {quantity} × **{item["name"]}** on behalf of {unit.mention} '
                               f'for {money(interaction.guild_id, cost)}. Use `/unitshop fulfill purchase_id:{purchase_id}`.')
        except (discord.HTTPException,RuleError):
            log.warning('Could not send unit order #%d to staff review channel',purchase_id)


@unitshop_group.command(name='orders',description='Show pending Unit Supply Exchange orders')
async def unitshop_orders(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    rows=db.pending_purchases(interaction.guild_id,shop_kind='UNIT')
    if not rows:
        await interaction.response.send_message('No pending unit orders.',ephemeral=True)
        return
    lines=[f"**#{r['id']}** · <@&{r['recipient_id']}> ordered by <@{r['user_id']}> · "
           f"{r['quantity']} × {r['item_name']} ({money(interaction.guild_id, r['total_price'])})" for r in rows]
    await interaction.response.send_message('\n'.join(lines)[:1900],ephemeral=True)


@unitshop_group.command(name='fulfill',description='Mark a unit order as delivered')
async def unitshop_fulfill(interaction:discord.Interaction,purchase_id:int):
    if not await require_moderator(interaction):return
    if not db.fulfill(interaction.guild_id,purchase_id,interaction.user.id,shop_kind='UNIT'):
        await deny(interaction,'Unit purchase not found or already fulfilled.')
        return
    await interaction.response.send_message(f'Unit order **#{purchase_id}** marked delivered.',ephemeral=True)


@bot.tree.command(name='balance',description='Check a member’s Blacksite Credits')
async def balance(interaction:discord.Interaction,member:Optional[discord.Member]=None):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    target=member or interaction.user
    amt=db.balance(interaction.guild_id,'USER',target.id)
    await interaction.response.send_message(f'**{target.display_name}** · **{money(interaction.guild_id, amt, full=True)}**.',ephemeral=True)


@bot.tree.command(name='payments',description='Show recent account activity including earnings and purchases')
async def payments(interaction:discord.Interaction,member:Optional[discord.Member]=None):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    target=member or interaction.user
    rows=db.history(interaction.guild_id,'USER',target.id)
    lines=[f"{r['created_at']} UTC · {money(interaction.guild_id, r['amount'], signed=True)} · {r['note'][:70]}" for r in rows]
    await interaction.response.send_message(
        f'**LEDGER — {target.display_name}**\n'+('\n'.join(lines) if lines else 'No transactions yet.'),
        ephemeral=True)


@bot.tree.command(name='leaderboard',description='Show the ten richest contractors')
async def leaderboard(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this in a server.')
        return
    rows=db.leaderboard(interaction.guild_id)
    lines=[f"**{n}.** <@{r['recipient_id']}> — {money(interaction.guild_id, r['balance'])}" for n,r in enumerate(rows,1)]
    await interaction.response.send_message(embed=discord.Embed(
        title='LEDGER • BLACKSITE CREDIT LEADERBOARD',
        description='\n'.join(lines) if lines else 'No balances yet.',color=GREEN))


stats_group=app_commands.Group(name='stats',description='Black Market Contract mission statistics')


@stats_group.command(name='me',description='Show your tracked mission screenshot statistics')
async def stats_me(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this command in a server.')
        return
    await interaction.response.send_message(embed=stats_embed(interaction.guild_id,
        'YOUR CONTRACT STATS',db.proof_stats(interaction.guild_id,member_id=interaction.user.id)),
        ephemeral=True)


@stats_group.command(name='member',description='Show screenshot statistics submitted by a member')
async def stats_member(interaction:discord.Interaction, member:discord.Member):
    if interaction.guild_id is None:
        await deny(interaction,'Use this command in a server.')
        return
    await interaction.response.send_message(embed=stats_embed(interaction.guild_id,
        f'CONTRACT STATS • {member.display_name[:60]}',
        db.proof_stats(interaction.guild_id,member_id=member.id)))


@stats_group.command(name='unit',description='Show mission stats for paid unit contracts')
async def stats_unit(interaction:discord.Interaction, unit:discord.Role):
    if interaction.guild_id is None:
        await deny(interaction,'Use this command in a server.')
        return
    if not db.get_unit(interaction.guild_id,unit.id):
        await deny(interaction,'That is not a registered unit.')
        return
    await interaction.response.send_message(embed=stats_embed(interaction.guild_id,
        f'UNIT CONTRACT STATS • {unit.name[:50]}',
        db.proof_stats(interaction.guild_id,unit_id=unit.id)))


@stats_group.command(name='server',description='Show aggregate server contract verification statistics')
async def stats_server(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this command in a server.')
        return
    await interaction.response.send_message(embed=stats_embed(interaction.guild_id,
        'SERVER CONTRACT STATS',db.proof_stats(interaction.guild_id)))


@stats_group.command(name='diagnose',description='Explain whether your screenshot OCR stats were recorded and linked')
async def stats_diagnose(interaction:discord.Interaction,attempt_id:int|None=None):
    if interaction.guild_id is None:
        await deny(interaction,'Use this command in a server.')
        return
    if attempt_id is None:
        rows=db.recent_proof_stats(interaction.guild_id,interaction.user.id,limit=1)
        if not rows:
            await deny(interaction,'No submissions found. Upload a contract screenshot first.')
            return
        attempt_id=rows[0]['id']
    attempt=db.attempt(interaction.guild_id,attempt_id)
    if not attempt or (attempt['submitter_id']!=interaction.user.id and not moderator(interaction)):
        await deny(interaction,'No accessible screenshot attempt with that ID.')
        return
    try:
        facts=db.clean_vision_stats(json.loads(attempt['vision_stats'] or '{}'))
    except (TypeError,ValueError):
        facts={}
    players=facts.get('players',[])
    links=db.attempt_player_links(interaction.guild_id,attempt_id)
    embed=discord.Embed(title=f'▰ BLACKSITE / OCR DIAGNOSTIC #{attempt_id}',color=TEAL)
    embed.add_field(name='Submission status',value=(
        f'**{attempt["status"]}** · OCR: **{attempt["ai_verdict"] or "NOT RUN"}**'),inline=False)
    if players:
        names='\n'.join(
            f'**Card {p.get("column","?")}** • '
            f'{discord.utils.escape_markdown(discord.utils.escape_mentions(p["game_name"] or "name unclear"))} '
            f'*(unverified OCR label)* — '
            f'{p["stats"].get("kills","?")} kills / {p["stats"].get("stims_used","?")} stims'
            for p in players)
        embed.add_field(name='Detected player cards (names unverified)',value=names[:1024],inline=False)
        if links:
            matched=[]
            for link in db.attempt_player_records(interaction.guild_id,attempt_id):
                uid,name=link['discord_user_id'],link['game_name']
                found=[p for p in players if db._player_matches_user(
                    interaction.guild_id,attempt_id,p['game_name'],uid,p.get('column'))]
                column=link['player_column']
                basis=(f'card {column}' if column is not None else 'legacy name')
                matched.append(f'<@{uid}> • {basis} '
                    + ('✅ LINKED' if len(found)==1 else '⚠️ UNRESOLVED'))
            embed.add_field(name='Discord player links',value='\n'.join(matched)[:1024],inline=False)
        else:
            embed.add_field(name='Discord player links',value='⚠️ No in-game names were linked; personal stats cannot be attributed.',inline=False)
    elif facts:
        embed.add_field(name='OCR numbers',value=', '.join(f'{k}: {v}' for k,v in facts.items())[:1024],inline=False)
    else:
        embed.add_field(name='OCR numbers',value='⚠️ No readable player statistics stored for this screenshot.',inline=False)
    if attempt['status']!='PAID':
        reason='This submission has not been accepted; it is not counted in career statistics.'
    elif attempt['ai_verdict']!='PASS':
        reason='Staff approval does not override uncertain OCR numbers; they are not included in automated rankings.'
    elif players and not links:
        reason='OCR read player numbers, but no Discord identities were linked.'
    elif players and not any(db._player_matches_user(
             interaction.guild_id,attempt_id,p['game_name'],uid,p.get('column'))
             for uid,_ in links for p in players):
        reason=('No Discord member is linked to a readable player card. '
                'For new uploads, assign the member using the physical card position.')
    elif not players and not any(k in facts for k in ('kills','deaths','stims_used','shots_fired')):
        reason='The image passed the results-screen check, but no numeric stats were recognized.'
    else:
        reason='Readable, uniquely matched numbers from this accepted screenshot should count toward statistics.'
    embed.add_field(name='Tracking explanation',value=reason[:1024],inline=False)
    embed.set_footer(text='New uploads link by card position. OCR names are guesses, not identity verification.')
    await interaction.response.send_message(embed=embed,ephemeral=True)


@stats_group.command(name='recent',description='View your five most recent screenshot checks')
async def stats_recent(interaction:discord.Interaction):
    if interaction.guild_id is None:
        await deny(interaction,'Use this command in a server.')
        return
    rows=db.recent_proof_stats(interaction.guild_id,interaction.user.id)
    lines=[f"**#{r['id']}** · Contract #{r['contract_id']} · "
           f"{r['status']} / {r['ai_verdict'] or 'NOT CHECKED'} · "
           f"{discord.utils.escape_markdown(r['planet'][:60])} D{r['difficulty']}"
           for r in rows]
    await interaction.response.send_message('\n'.join(lines) or 'No submissions yet.',ephemeral=True)


# ---------------------------------------------------------------------------
# The Broker's public front door and user-facing dossier.
# ---------------------------------------------------------------------------
@bot.tree.command(name='hub',description='Open the Blacksite contract terminal (private dashboard)')
async def hub_command(interaction:discord.Interaction):
    if not interaction.guild_id:
        await deny(interaction,'The terminal only connects from a server.')
        return
    e=blacksite_embed('▰ LEDGER / UNDERGROUND CONTACT',
       '**CONNECTION ESTABLISHED.** Your broker is listening.\n\n'
       'Find contracts, check your dossier, browse both markets and examine unit funds '
       'without memorizing dozens of commands.\n\n'
       '*Every deal is logged. Every credit has a trail.*', color=GREEN)
    await interaction.response.send_message(embed=e,view=BlacksiteTerminal(),ephemeral=True)


@bot.tree.command(name='profile',description='Open a contractor dossier with Discord profile, credits and stats')
async def profile_command(interaction:discord.Interaction,member:Optional[discord.Member]=None):
    if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
        await deny(interaction,'Run this in a server.')
        return
    target=member or interaction.user
    e=profile_card(target,member_dossier_data(interaction.guild_id,target.id),
        db.member_units(interaction.guild_id,[role.id for role in target.roles]),
        money,interaction.guild_id,
        db.member_handles(interaction.guild_id,target.id))
    await interaction.response.send_message(embed=e,ephemeral=member is None,
                                            view=DossierActions(target.id) if member is None else None)


@ledger_group.command(name='unlink_player',description='Remove a disputed uploader-reported game-name association')
async def ledger_unlink_player(interaction:discord.Interaction,attempt_id:int,member:discord.Member):
    if not await require_moderator(interaction):
        return
    if db.unlink_player_name(interaction.guild_id,attempt_id,member.id):
        await interaction.response.send_message(
            f'Removed the player-name link for {member.mention} on screenshot attempt #{attempt_id}. '
            'This does not reverse a previous payout.',ephemeral=True)
    else:
        await deny(interaction,'No game-name link for that member on this screenshot.')


@ledger_group.command(name='publish_hub',description='Publish or relocate the public Blacksite terminal')
async def ledger_publish_hub(interaction:discord.Interaction,channel:discord.TextChannel):
    if not await require_moderator(interaction):
        return
    if not channel.permissions_for(interaction.guild.me).send_messages or not channel.permissions_for(interaction.guild.me).embed_links:
        await deny(interaction,'LEDGER requires Send Messages and Embed Links in this channel.')
        return
    await interaction.response.defer(ephemeral=True)
    e=blacksite_embed('▰ BLACKSITE NETWORK / PRIVATE CONTRACTOR ACCESS',
        '**Your underground contact is online.**\n'
        'Contracts. Blacksite Credits. Unmarked supply channels. Discreet dossiers.\n\n'
        'Use the buttons below to contact the Broker. Your selections are private.',color=GREEN)
    old_channel=db.setting(interaction.guild_id,'hub_channel_id')
    old_message=db.setting(interaction.guild_id,'hub_message_id')
    try:
        msg=None
        if old_channel==channel.id and old_message:
            try:
                msg=await channel.fetch_message(old_message)
            except discord.NotFound:
                pass
        if msg:
            await msg.edit(embed=e,view=BlacksiteTerminal())
        else:
            msg=await channel.send(embed=e,view=BlacksiteTerminal())
        db.configure(interaction.guild_id,'hub_channel_id',channel.id)
        db.configure(interaction.guild_id,'hub_message_id',msg.id)
        await interaction.followup.send(f'Blacksite terminal online: {msg.jump_url}',ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send('Could not publish the terminal; check permissions.',ephemeral=True)


@ledger_group.command(name='guide',description='Quick guide to the Blacksite network')
async def ledger_quick_guide(interaction:discord.Interaction):
    e=blacksite_embed('▰ LEDGER // FIELD MANUAL',
        '**START HERE**\n'
        '`/hub` — interactive underground contractor terminal\n'
        '`/profile` — personal dossier, account and OCR stats\n'
        '`/contract status` — view a contract by ID\n'
        '`/contract submit` — screenshot, planet, difficulty, tagged squad\n'
        '`/shop browse` & `/unitshop browse` — market listings\n'
        '`/stats me` — detailed mission statistics\n\n'
        '**MODERATOR DESK**\n'
        '`/ledger channels` — configure public + review channels\n'
        '`/ledger publish_hub` — public contractor terminal\n'
        '`/unit setup` — register roles as units\n'
        '`/contract post` — draft and publish a contract with optional bonuses\n'
        '`/ledger refresh_top_earners` — refresh the 30-minute rankings\n'
        '`/mentor setup` — private mentoring intake and officer notifications',color=TEAL)
    await interaction.response.send_message(embed=e,ephemeral=True)


@contract_group.command(name='objectives',description='Inspect a contract and its optional paid bonuses')
async def contract_objectives(interaction:discord.Interaction,contract_id:int):
    if not interaction.guild_id:
        await deny(interaction,'Use this in your server.')
        return
    row=db.get_contract(interaction.guild_id,contract_id)
    if not row:
        await deny(interaction,'Contract not found.')
        return
    await interaction.response.send_message(embed=contract_embed(row),ephemeral=True)


@contract_group.command(name='bonus_add',description='Add a screenshot-tracked bonus before any payments occur')
async def contract_bonus_add(interaction:discord.Interaction,contract_id:int):
    if not await require_moderator(interaction):
        return
    offer=db.get_contract(interaction.guild_id,contract_id)
    if not offer or offer['status']!='OPEN':
        await deny(interaction,'Contract is missing or closed.')
        return
    await interaction.response.send_modal(BonusObjectiveModal(
        contract_id=contract_id,base_reward=offer['reward']))


# ---------------------------------------------------------------------------
# ADOPT A CONTRACTOR — officer-led training contracts and verified payments.
# ---------------------------------------------------------------------------
def mentor_can_manage(interaction:discord.Interaction,case=None) -> bool:
    if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
        return False
    if moderator(interaction):
        return True
    unit_id=case['unit_id'] if case else None
    return mentors.allowed_manager(interaction.guild_id,
                [role.id for role in interaction.user.roles],unit_id)


def mentor_case_embed(guild_id:int,case_id:int) -> discord.Embed:
    c=mentors.request_by_id(guild_id,case_id)
    if not c:
        return blacksite_embed('BLACKSITE // INVALID CASE','Request not found.',color=GOLD)
    done=mentors.confirmed_count(guild_id,case_id)
    color=GREEN if c['status']=='PAID' else GOLD if c['status']=='READY' else TEAL
    affiliation = f'<@&{c["unit_id"]}>' if c['unit_id'] else 'Unassigned / General'
    mentor_display = f'<@{c["mentor_id"]}>' if c['mentor_id'] else 'Awaiting assignment'
    e=blacksite_embed(f'▰ BLACKSITE TRAINING // {mentor_ref(case_id)}',
        mentor_id_banner(case_id) + '\n\n' +
        f'**STATUS: {c["status"]}**\n'
        f'**Trainee:** <@{c["trainee_id"]}>\n'
        f'**Affiliation:** {affiliation}\n'
        f'**Mentor:** {mentor_display}\n\n'
        f'**Experience:** {discord.utils.escape_mentions(c["experience"])[:120]}\n'
        f'**Needs help with:** {discord.utils.escape_mentions(c["goal"])[:520]}\n'
        f'**Availability:** {discord.utils.escape_mentions(c["availability"])[:250]}',color=color)
    e.add_field(name='Training clearance',value=(
        f'`{done}/{c["required_sessions"]}` trainee-confirmed sessions\n'
        f'Mentor accepted: {"Yes" if c["mentor_accepted"] else "No"}\n'
        f'Trainee sign-off: {"Yes" if c["trainee_signed"] else "No"}'),inline=True)
    e.add_field(name='Contract reward',value=money(guild_id,c['reward'],full=True),inline=True)
    e.set_footer(text='Session checks → trainee sign-off → independent officer approval → mentor payout')
    return e


async def mentor_refresh_case(guild_id:int,request_id:int):
    c=mentors.request_by_id(guild_id,request_id)
    if not c or not c['channel_id'] or not c['message_id']:return
    try:
        channel=await fetch_text_channel(c['channel_id'])
        message=await channel.fetch_message(c['message_id'])
        await message.edit(embed=mentor_case_embed(guild_id,request_id),
            view=MentorCaseView(request_id) if c['status'] in ('OPEN','ASSIGNED','READY') else None)
    except (discord.HTTPException,RuleError):
        log.warning('Could not refresh mentorship case %s in %s',request_id,guild_id)


class MentorRequestModal(discord.ui.Modal,title='BLACKSITE • Mentor Intake'):
    experience=discord.ui.Label(text='How long have you been playing?',component=discord.ui.TextInput(
        placeholder='e.g. New to Helldivers, level 12',min_length=2,max_length=120))
    goals=discord.ui.Label(text='What do you want to learn?',component=discord.ui.TextInput(
        style=discord.TextStyle.paragraph,placeholder='Stratagems, loadouts, team coordination...',
        min_length=5,max_length=600))
    availability=discord.ui.Label(text='Timezone / availability',component=discord.ui.TextInput(
        placeholder='e.g. PST, weekends after 4 PM',min_length=2,max_length=250))

    def __init__(self,unit_id:int|None):
        super().__init__()
        self.unit_id=unit_id

    async def on_submit(self,interaction:discord.Interaction):
        if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
            await deny(interaction,'Mentorship requests must be filed in your Discord server.');return
        member=interaction.user
        role_ids=[r.id for r in member.roles]
        if self.unit_id and not db.member_in_unit(interaction.guild_id,self.unit_id,role_ids):
            await deny(interaction,'You no longer hold a role associated with that unit. Please try again.');return
        try:
            cid=mentors.request(interaction.guild_id,member.id,self.unit_id,
                str(self.experience.component.value),str(self.goals.component.value),
                str(self.availability.component.value))
        except RuleError as exc:
            await deny(interaction,str(exc));return
        await interaction.response.defer(ephemeral=True)
        cfg=mentors.settings(interaction.guild_id)
        case=mentors.request_by_id(interaction.guild_id,cid)
        try:
            channel=await fetch_text_channel(cfg['channel_id'])
            raw_ids=set(mentors.officer_roles(interaction.guild_id))
            if self.unit_id:
                raw_ids.update(mentors.lead_roles(interaction.guild_id,self.unit_id))
            # Fallback to Market Moderation if leadership mappings are still empty.
            if not raw_ids and db.setting(interaction.guild_id,'moderator_role_id'):
                raw_ids.add(db.setting(interaction.guild_id,'moderator_role_id'))
            roles=[r for rid in sorted(raw_ids) if (r:=interaction.guild.get_role(rid))]
            ping=' '.join(r.mention for r in roles)
            msg=await channel.send(content=(ping+'\n' if ping else '')+
                   f'**NEW MENTOR REQUEST ID `{cid}`** • `{mentor_ref(cid)}`. A contractor is requesting guidance.',
                   embed=mentor_case_embed(interaction.guild_id,cid),view=MentorCaseView(cid),
                   allowed_mentions=discord.AllowedMentions(everyone=False,users=False,roles=roles))
            mentors.attach_message(interaction.guild_id,cid,channel.id,msg.id)
        except (RuleError,discord.HTTPException) as exc:
            # Keep request pending but inform staff about channel configuration failure.
            log.exception('Mentor request #%s could not be announced: %s',cid,exc)
            await interaction.followup.send(f'Mentor request ID `{cid}` (`{mentor_ref(cid)}`) saved, but LEDGER could not notify staff. '
                'Ask moderators to check channel permissions and `/mentor queue`.',ephemeral=True)
            return
        await interaction.followup.send(f'Your **Mentor Request ID `{cid}`** (`{mentor_ref(cid)}`) is on the desk. '
            f'Staff will assign a mentor when one is available. Training contract reward: '
            f'{money(interaction.guild_id,case["reward"])} paid to your mentor **after** training is confirmed.',ephemeral=True)


class MentorUnitChoice(discord.ui.Select):
    def __init__(self,units):
        options=[discord.SelectOption(label='General / no unit',value='0',description='Ask for a mentor from any unit')]
        options.extend(discord.SelectOption(label=u['name'][:100],value=str(u['role_id'])) for u in units[:24])
        super().__init__(placeholder='Which unit should help you?',min_values=1,max_values=1,
                         options=options)
    async def callback(self,interaction:discord.Interaction):
        view=self.view
        if interaction.user.id!=view.owner_id:
            await deny(interaction,'Only the requesting contractor can use this menu.');return
        uid=int(self.values[0]) or None
        await interaction.response.send_modal(MentorRequestModal(uid))


class MentorUnitChoiceView(discord.ui.View):
    def __init__(self,owner_id,units):
        super().__init__(timeout=300)
        self.owner_id=owner_id
        self.add_item(MentorUnitChoice(units))


async def begin_mentor_request(interaction:discord.Interaction):
    if not interaction.guild_id or not isinstance(interaction.user,discord.Member):
        await deny(interaction,'Use this inside your server.');return
    if not mentors.settings(interaction.guild_id)['channel_id']:
        await deny(interaction,'The mentorship desk is not configured. Ask staff to run `/mentor setup`.');return
    existing=mentors.active_for_trainee(interaction.guild_id,interaction.user.id)
    if existing:
        await deny(interaction,f'You already have an active **Mentor Request ID `{existing["id"]}`** (`{mentor_ref(existing["id"])}`). '
                   'Use `/mentor status` for progress.');return
    units=db.member_units(interaction.guild_id,[r.id for r in interaction.user.roles])
    if len(units)==1:
        await interaction.response.send_modal(MentorRequestModal(units[0]['role_id']))
    elif units:
        await interaction.response.send_message('Which unit should handle your mentorship?',
                   view=MentorUnitChoiceView(interaction.user.id,units),ephemeral=True)
    else:
        await interaction.response.send_modal(MentorRequestModal(None))


class MentorSessionConfirmView(discord.ui.View):
    """Private trainee button; a persistent callback survives a Railway restart."""
    def __init__(self,guild_id:int,session_id:int):
        super().__init__(timeout=None)
        self.guild_id=guild_id
        self.session_id=session_id
        button=discord.ui.Button(label='Confirm I attended',emoji='✅',
            style=discord.ButtonStyle.success,
            custom_id=f'ledger:mentor:confirm:{guild_id}:{session_id}')
        button.callback=self.confirm
        self.add_item(button)

    async def confirm(self,interaction:discord.Interaction):
        try:
            request_id=mentors.confirm_session(self.guild_id,self.session_id,interaction.user.id)
        except RuleError as exc:
            await deny(interaction,str(exc));return
        await interaction.response.send_message('Your training session has been confirmed. '
            'When all required sessions are complete, use `/mentor finish` in your server.',
            ephemeral=bool(interaction.guild_id))
        await mentor_refresh_case(self.guild_id,request_id)
        if interaction.message:
            try:await interaction.message.edit(view=None)
            except discord.HTTPException:pass


class MentorRequestPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label='Find a Mentor',emoji='🤝',style=discord.ButtonStyle.success,
                       custom_id='ledger:mentor:request')
    async def request(self,interaction:discord.Interaction,button:discord.ui.Button):
        await begin_mentor_request(interaction)


async def mentor_notify_assignment(guild_id:int,case_id:int,mentor_id:int):
    try:
        user=await bot.fetch_user(mentor_id)
        await user.send(f'BLACKSITE // ADOPT A CONTRACTOR\n'
                        f'You have been invited to mentor request **ID `{case_id}`** (`{mentor_ref(case_id)}`). '
                        f'If you accept, run `/mentor accept request_id:{case_id}` in your server. '
                        f'If you cannot help, run `/mentor decline request_id:{case_id}`. '
                        'You are not obligated to accept.')
    except discord.HTTPException:
        pass


class MentorAssignSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder='Choose a willing mentor from this server',
                         min_values=1,max_values=1)
    async def callback(self,interaction:discord.Interaction):
        view=self.view
        c=mentors.request_by_id(interaction.guild_id,view.case_id)
        if not c or not mentor_can_manage(interaction,c):
            await deny(interaction,'Only designated unit leads or officers may assign mentors.');return
        target=self.values[0]
        if target.bot or target.id==c['trainee_id']:
            await deny(interaction,'Choose a human member other than the trainee.');return
        try:
            mentors.assign(interaction.guild_id,view.case_id,target.id,interaction.user.id)
        except RuleError as exc:
            await deny(interaction,str(exc));return
        await interaction.response.send_message(f'Assigned {target.mention} to mentor <@{c["trainee_id"]}>. '
            f'{target.mention} must run `/mentor accept request_id:{view.case_id}` before logging sessions.',
            ephemeral=True)
        await mentor_refresh_case(interaction.guild_id,view.case_id)
        await mentor_notify_assignment(interaction.guild_id,view.case_id,target.id)
        # Only mention the assigned mentor privately in the officer channel if possible.
        if c['channel_id']:
            try:
                ch=await fetch_text_channel(c['channel_id'])
                await ch.send(f'<@{target.id}> — **Mentor Request ID `{view.case_id}`** (`{mentor_ref(view.case_id)}`) assigned to you. '
                    'Run `/mentor accept` if you agree to mentor this contractor.',
                    allowed_mentions=discord.AllowedMentions(users=[target],roles=False,everyone=False))
            except (discord.HTTPException,RuleError):pass


class MentorAssignView(discord.ui.View):
    def __init__(self,case_id):
        super().__init__(timeout=300)
        self.case_id=case_id
        self.add_item(MentorAssignSelect())


async def mentor_approve_case(interaction:discord.Interaction,request_id:int):
    c=mentors.request_by_id(interaction.guild_id,request_id) if interaction.guild_id else None
    if not c or not mentor_can_manage(interaction,c):
        await deny(interaction,'Only unit leads, officer roles, or LEDGER moderators may clear mentor contracts.');return
    try:
        mentor_id,trainee_id,reward=mentors.payout(interaction.guild_id,request_id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message('**Mentor contract paid.** '
        f'<@{mentor_id}> earned {money(interaction.guild_id,reward)} for training <@{trainee_id}>.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)
    await publish_top_earners(interaction.guild_id)
    if c['channel_id']:
        try:
            ch=await fetch_text_channel(c['channel_id'])
            await ch.send(f'**BLACKSITE / TRAINING CONTRACT CLEARED**\n'
                f'<@{mentor_id}> trained <@{trainee_id}>. **{money(interaction.guild_id,reward)}** released. '
                f'Approved by <@{interaction.user.id}>.',
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=mentor_id),
                        discord.Object(id=trainee_id)],roles=False,everyone=False))
        except (discord.HTTPException,RuleError):pass


class MentorCaseView(discord.ui.View):
    def __init__(self,case_id:int):
        super().__init__(timeout=None)
        self.case_id=case_id
        self.add_item(discord.ui.Button(label='Assign Mentor',emoji='🛡️',style=discord.ButtonStyle.primary,
                            custom_id=f'ledger:mentor:assign:{case_id}'))
        self.add_item(discord.ui.Button(label='Clear Contract',emoji='💳',style=discord.ButtonStyle.success,
                            custom_id=f'ledger:mentor:approve:{case_id}'))
        self.children[0].callback=self.assign
        self.children[1].callback=self.approve
    async def assign(self,interaction:discord.Interaction):
        c=mentors.request_by_id(interaction.guild_id,self.case_id)
        if not c or not mentor_can_manage(interaction,c):
            await deny(interaction,'Only designated unit leads or officers can assign mentors.');return
        if c['status']!='OPEN':
            await deny(interaction,'This case has already been assigned.');return
        await interaction.response.send_message('Choose a mentor:',
               view=MentorAssignView(self.case_id),ephemeral=True)
    async def approve(self,interaction:discord.Interaction):
        await mentor_approve_case(interaction,self.case_id)


class MentorChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder='Choose the PRIVATE mentorship dispatch channel',
            channel_types=[discord.ChannelType.text],min_values=1,max_values=1,row=0)
    async def callback(self,interaction:discord.Interaction):
        if not manager(interaction):
            await deny(interaction,'You need Manage Server permission.');return
        channel=interaction.guild.get_channel(self.values[0].id)
        if not isinstance(channel,discord.TextChannel):
            await deny(interaction,'Choose a standard text channel.');return
        if channel.permissions_for(interaction.guild.default_role).view_channel:
            await deny(interaction,'Mentorship dispatch must be private (hidden from @everyone).');return
        me=interaction.guild.me
        p=channel.permissions_for(me)
        if not (p.send_messages and p.view_channel and p.embed_links and p.read_message_history):
            await deny(interaction,'LEDGER needs View Channel, Send Messages, Embed Links and Read History.');return
        mentors.configure(interaction.guild_id,channel_id=channel.id)
        await interaction.response.send_message(f'Mentor dispatch channel: {channel.mention}. '
            'Configure officer roles in the other dropdown.',ephemeral=True)


class MentorOfficerSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder='Select higher officer roles to ping (up to 25)',
                         min_values=1,max_values=25,row=1)
    async def callback(self,interaction:discord.Interaction):
        if not manager(interaction):
            await deny(interaction,'You need Manage Server permission.');return
        roles=[r for r in self.values if not r.is_default() and not r.managed]
        if len(roles)!=len(self.values):
            await deny(interaction,'Do not select @everyone or integration-managed roles.');return
        mentors.configure(interaction.guild_id,officer_role_ids=[r.id for r in roles])
        channel=interaction.guild.get_channel(mentors.settings(interaction.guild_id)['channel_id'])
        missing=[r.mention for r in roles if isinstance(channel,discord.TextChannel)
                 and not channel.permissions_for(r).view_channel]
        warning=('\n⚠ Give these roles **View Channel** access in mentor dispatch: '
                 + ', '.join(missing)) if missing else ''
        await interaction.response.send_message('Higher officers assigned: '+
            ', '.join(r.mention for r in roles)+warning,ephemeral=True)


class MentorSetupView(discord.ui.View):
    def __init__(self,owner_id):
        super().__init__(timeout=600)
        self.owner_id=owner_id
        self.add_item(MentorChannelSelect())
        self.add_item(MentorOfficerSelect())
    async def interaction_check(self,interaction):
        if interaction.user.id != self.owner_id or not manager(interaction):
            await deny(interaction,'Only the server manager who opened this menu may use it.');return False
        return True


@mentor_group.command(name='setup',description='Configure private mentorship dispatch and higher-officer roles')
async def mentor_setup(interaction:discord.Interaction):
    if not await require_manager(interaction):return
    cfg=mentors.settings(interaction.guild_id)
    channel_label = f'<#{cfg["channel_id"]}>' if cfg['channel_id'] else 'Not configured'
    officers_label = ', '.join(f'<@&{r}>' for r in mentors.officer_roles(interaction.guild_id)) or 'None'
    e=blacksite_embed('▰ ADOPT A CONTRACTOR / DISPATCH DESK',
        'Select a **private** mentorship channel and one or more higher-officer roles.\n'
        f'Current channel: {channel_label}\n'
        f'Officer roles: {officers_label}\n'
        'You can set individual unit-lead roles with `/mentor set_lead`. '
        'Configure the reward and session count with `/mentor terms`.',color=TEAL)
    await interaction.response.send_message(embed=e,view=MentorSetupView(interaction.user.id),ephemeral=True)


@mentor_group.command(name='terms',description='Set credit payout and number of trainee-confirmed sessions')
async def mentor_terms(interaction:discord.Interaction,reward:int,required_sessions:int=2):
    if not await require_moderator(interaction):return
    try:cfg=mentors.configure(interaction.guild_id,reward=reward,required_sessions=required_sessions)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message(f'New mentorship contracts pay '
        f'**{money(interaction.guild_id,cfg["reward"])}** after '
        f'**{cfg["required_sessions"]} trainee-confirmed training sessions**, trainee sign-off '
        'and independent officer approval. Existing requests retain their original terms.',ephemeral=True)


@mentor_group.command(name='set_lead',description='Link a unit leadership role for mentor request notifications')
async def mentor_set_lead(interaction:discord.Interaction,unit:discord.Role,lead_role:discord.Role):
    if not await require_moderator(interaction):return
    if lead_role.is_default() or lead_role.managed:
        await deny(interaction,'Choose a regular server leadership role.');return
    existing=mentors.lead_roles(interaction.guild_id,unit.id)
    try:mentors.set_lead_roles(interaction.guild_id,unit.id,sorted(set(existing+[lead_role.id])))
    except RuleError as exc:
        await deny(interaction,str(exc));return
    channel=interaction.guild.get_channel(mentors.settings(interaction.guild_id)['channel_id'])
    warning=('\n⚠ Allow that role to View the private mentorship channel, or the ping will not be seen.'
             if isinstance(channel,discord.TextChannel) and
             not channel.permissions_for(lead_role).view_channel else '')
    await interaction.response.send_message(f'{lead_role.mention} will be pinged about new '
        f'mentor requests from {unit.mention}, alongside configured higher officers.'+warning,ephemeral=True)


@mentor_group.command(name='request',description='Newer contractors: ask to be assigned a mentor')
async def mentor_request(interaction:discord.Interaction):
    await begin_mentor_request(interaction)


@mentor_group.command(name='publish',description='Publish an Adopt a Contractor request button in a channel')
async def mentor_publish(interaction:discord.Interaction,channel:discord.TextChannel):
    if not await require_moderator(interaction):return
    p=channel.permissions_for(interaction.guild.me)
    if not (p.view_channel and p.send_messages and p.embed_links):
        await deny(interaction,'LEDGER cannot post embeds in that channel.');return
    e=blacksite_embed('▰ ADOPT A CONTRACTOR / TRAINING PROGRAM',
        '**New to the front?** You do not need to figure everything out alone.\n\n'
        'Request a mentor, tell us what you want to learn, and your unit leads '
        'and officers will work to pair you with an experienced contractor.\n\n'
        'Mentors earn Blacksite Credits **only after completed sessions are confirmed '
        'by the trainee and cleared by an officer**. Participation is voluntary.',color=GREEN)
    await channel.send(embed=e,view=MentorRequestPanel())
    await interaction.response.send_message(f'Published the recruitment panel in {channel.mention}.',ephemeral=True)


@mentor_group.command(name='assign',description='Unit lead or officer: appoint a mentor to a trainee')
async def mentor_assign(interaction:discord.Interaction,request_id:int,member:discord.Member):
    c=mentors.request_by_id(interaction.guild_id,request_id)
    if not c or not mentor_can_manage(interaction,c):
        await deny(interaction,'Only designated unit leads or officers can assign a mentor.');return
    if member.bot:
        await deny(interaction,'Mentor must be a real member.');return
    try:mentors.assign(interaction.guild_id,request_id,member.id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message(f'{member.mention} assigned to train '
        f'<@{c["trainee_id"]}>. The mentor must accept with `/mentor accept`.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)
    await mentor_notify_assignment(interaction.guild_id,request_id,member.id)


@mentor_group.command(name='accept',description='Assigned mentor: accept the training contract')
async def mentor_accept(interaction:discord.Interaction,request_id:int):
    if not interaction.guild_id:return
    try:mentors.accept(interaction.guild_id,request_id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message(f'Accepted **Mentor Request ID `{request_id}`** (`{mentor_ref(request_id)}`). '
        'After training, use `/mentor session` to log a session for your trainee to confirm.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)


@mentor_group.command(name='decline',description='Assigned mentor: decline if you cannot train this contractor')
async def mentor_decline(interaction:discord.Interaction,request_id:int):
    try:mentors.unassign(interaction.guild_id,request_id,interaction.user.id,mentor_declining=True)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message('Declined. The request is available for reassignment.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)


@mentor_group.command(name='unassign',description='Officer: release an untrained mentor assignment for reassignment')
async def mentor_unassign(interaction:discord.Interaction,request_id:int):
    c=mentors.request_by_id(interaction.guild_id,request_id)
    if not c or not mentor_can_manage(interaction,c):
        await deny(interaction,'Only a designated officer may change this assignment.');return
    try:mentors.unassign(interaction.guild_id,request_id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message('Mentor unassigned; case is available again.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)


@mentor_group.command(name='session',description='Mentor: log one training session for trainee confirmation')
async def mentor_session(interaction:discord.Interaction,request_id:int,summary:str):
    try:sid=mentors.log_session(interaction.guild_id,request_id,interaction.user.id,summary)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    c=mentors.request_by_id(interaction.guild_id,request_id)
    await interaction.response.send_message(f'Training session **#{sid}** logged. '
        f'<@{c["trainee_id"]}> must confirm it with `/mentor confirm session_id:{sid}`.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)
    # Contact the trainee privately by DM if allowed; no details exposed publicly.
    try:
        user=await bot.fetch_user(c['trainee_id'])
        await user.send(f'LEDGER: Your mentor logged training session #{sid} for case #{request_id}. '
            'Confirm **only if you attended**. You can also use `/mentor confirm` in your server.',
            view=MentorSessionConfirmView(interaction.guild_id,sid))
    except discord.HTTPException: pass


@mentor_group.command(name='confirm',description='Trainee: confirm an actual training session')
async def mentor_confirm(interaction:discord.Interaction,session_id:int):
    try:rid=mentors.confirm_session(interaction.guild_id,session_id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message(f'Confirmed training session #{session_id}. '
        f'Check progress with `/mentor status`. Once all are done, run `/mentor finish`.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,rid)


@mentor_group.command(name='finish',description='Trainee: confirm required training and request officer sign-off')
async def mentor_finish(interaction:discord.Interaction,request_id:int):
    try:mentors.signoff(interaction.guild_id,request_id,interaction.user.id)
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message('Training finished. Your mentor contract is now waiting '
        'for independent officer approval and payout.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)


@mentor_group.command(name='approve',description='Officer: approve completed training and pay the mentor')
async def mentor_approve(interaction:discord.Interaction,request_id:int):
    await mentor_approve_case(interaction,request_id)


@mentor_group.command(name='queue',description='Unit lead or staff: view outstanding mentor cases')
async def mentor_queue(interaction:discord.Interaction):
    if not interaction.guild_id or not isinstance(interaction.user,discord.Member):return
    rows=[r for r in mentors.queue(interaction.guild_id)
          if mentor_can_manage(interaction,r)]
    if not rows:
        await interaction.response.send_message('No mentor cases available to your office.',ephemeral=True);return
    lines=[f'**Mentor ID `{r["id"]}`** (`{mentor_ref(r["id"])}`) • {r["status"]} · trainee <@{r["trainee_id"]}> · '
           f'unit {r["unit_id"] or "General"} · '
           f'{mentors.confirmed_count(interaction.guild_id,r["id"])}/{r["required_sessions"]} sessions'
           for r in rows]
    await interaction.response.send_message('\n'.join(lines)[:1900],ephemeral=True)


@mentor_group.command(name='status',description='See your ongoing mentorship and training session IDs')
async def mentor_status(interaction:discord.Interaction,request_id:int|None=None):
    if not interaction.guild_id:return
    if request_id is not None:
        c=mentors.request_by_id(interaction.guild_id,request_id)
    else:
        c=mentors.active_for_trainee(interaction.guild_id,interaction.user.id)
        if not c:
            c=mentors.conn.execute('''SELECT * FROM mentor_requests WHERE guild_id=? AND mentor_id=?
                AND status IN ('ASSIGNED','READY') ORDER BY id DESC LIMIT 1''',
                (interaction.guild_id,interaction.user.id)).fetchone()
    if not c:
        await deny(interaction,'No matching mentorship. Start with `/mentor request`.');return
    if interaction.user.id not in (c['trainee_id'],c['mentor_id']) and not mentor_can_manage(interaction,c):
        await deny(interaction,'This case is private to the participants and assigned leadership.');return
    e=mentor_case_embed(interaction.guild_id,c['id'])
    sessions=mentors.sessions(interaction.guild_id,c['id'])
    if sessions:
        e.add_field(name='Sessions',value='\n'.join(
            f'`#{s["id"]}` • {s["status"]} • {discord.utils.escape_mentions(s["note"])[:90]}'
            for s in sessions)[:1000],inline=False)
    await interaction.response.send_message(embed=e,ephemeral=True)


@mentor_group.command(name='cancel',description='Trainee or officer: close a mentorship without payout')
async def mentor_cancel(interaction:discord.Interaction,request_id:int):
    c=mentors.request_by_id(interaction.guild_id,request_id)
    if not c:
        await deny(interaction,'Mentor case not found.');return
    try:mentors.cancel(interaction.guild_id,request_id,interaction.user.id,
                       officer=mentor_can_manage(interaction,c))
    except RuleError as exc:
        await deny(interaction,str(exc));return
    await interaction.response.send_message('Mentorship request cancelled. No credits paid.',ephemeral=True)
    await mentor_refresh_case(interaction.guild_id,request_id)


@unit_group.command(name='roster',description='View automatically matched members of a unit')
async def unit_roster(interaction:discord.Interaction,unit:discord.Role):
    if not await require_moderator(interaction):return
    if not db.get_unit(interaction.guild_id,unit.id):
        await deny(interaction,'Register that unit first with `/unit setup`.');return
    # Role mapping at interaction time remains authoritative; roster is a synchronized index.
    user_ids=mentors.roster(interaction.guild_id,unit.id,limit=40)
    text='\n'.join(f'<@{uid}>' for uid in user_ids) or 'Nobody synced yet.'
    await interaction.response.send_message(f'**{unit.mention} — Contractor Roster**\n{text}\n\n'
        'Unit roles control membership. Enable `LEDGER_MEMBER_SYNC=1` with Discord '
        'Server Members Intent to sync the full roster automatically.',ephemeral=True)


@unit_group.command(name='sync_roster',description='Rebuild automatic unit rosters from Discord server roles')
async def unit_sync_roster(interaction:discord.Interaction):
    if not await require_moderator(interaction):return
    if not MEMBER_SYNC_ENABLED:
        await deny(interaction,'Enable `LEDGER_MEMBER_SYNC=1` in Railway and Server Members Intent '
            'in the Discord Developer Portal first. Other membership checks already use live roles.');return
    await interaction.response.defer(ephemeral=True)
    try:
        await sync_guild_unit_roster(interaction.guild)
    except discord.HTTPException as exc:
        await interaction.followup.send(f'Roster sync failed: {exc}',ephemeral=True);return
    await interaction.followup.send('Unit rosters reconciled from current Discord roles.',ephemeral=True)


@bot.tree.command(name='submit-mission-stats',description='Upload post-mission stats and link visible player cards to Discord members')
async def direct_submit_stats(interaction:discord.Interaction,contract_id:int,image:discord.Attachment,
                              planet:str,difficulty:int,operation_image:Optional[discord.Attachment]=None):
    offer=db.get_contract(interaction.guild_id,contract_id) if interaction.guild_id else None
    if not offer or offer['payout_mode']!='USER':
        await deny(interaction,'Choose an open individual contract. Unit contracts use `/contract submit`.');return
    if offer['proof_mode']=='OPERATION':
        await deny(interaction,'This contract requires operation proof; use `/submit-operation`.');return
    await begin_squad_link(interaction,contract_id,interaction.user.id,image,planet,difficulty,'',
                           operation_attachment=operation_image)


@bot.tree.command(name='submit-operation',description='Upload a completed operation scorecard for a Blacksite contract')
async def direct_submit_operation(interaction:discord.Interaction,contract_id:int,image:discord.Attachment,
                                  planet:str,difficulty:int):
    offer=db.get_contract(interaction.guild_id,contract_id) if interaction.guild_id else None
    if not offer or offer['payout_mode']!='USER' or offer['proof_mode']!='OPERATION':
        await deny(interaction,'This command is for individual OPERATION-type contracts only.');return
    await begin_squad_link(interaction,contract_id,interaction.user.id,image,planet,difficulty,'')


@bot.tree.command(name='intel',description='Intercept an unlisted Blacksite transmission')
async def direct_intel(interaction:discord.Interaction):
    title,body,rare=intel_drop(interaction.user.id,discord.utils.utcnow().date().isoformat())
    await interaction.response.send_message(embed=blacksite_embed(f'▰ DEAD DROP / {title}',body,color=GREEN),ephemeral=True)


for group in (ledger_group,board_group,contract_group,unit_group,credits_group,shop_group,unitshop_group,stats_group,mentor_group,dossier_group):
    bot.tree.add_command(group)


@bot.tree.error
async def on_command_error(interaction:discord.Interaction,error:app_commands.AppCommandError):
    log.error('Command failed: %r',error,exc_info=(type(error),error,error.__traceback__))
    await deny(interaction,'LEDGER could not complete that request. Check the bot logs.')


_once_ready=False
@bot.event
async def on_ready():
    global _once_ready
    log.info('LEDGER online as %s (%s)',bot.user,bot.user.id)
    if not _once_ready:
        _once_ready=True
        if not refresh_top_earners_loop.is_running():
            refresh_top_earners_loop.start()
        if not dispatch_update_loop.is_running():
            dispatch_update_loop.start()
        if MEMBER_SYNC_ENABLED:
            for guild in bot.guilds:
                asyncio.create_task(sync_guild_unit_roster(guild))
        # Upgrade button custom IDs on boards created by an older version.
        for row in db.conn.execute("SELECT * FROM contracts WHERE board_message_id IS NOT NULL"):
            await refresh_board(row['guild_id'],row['id'])


# __main__ block moved to bottom after all event handler definitions.


# Member synchronization only runs with the optional privileged Members Intent.
# Role membership remains immediately authoritative for slash commands regardless.
async def sync_guild_unit_roster(guild: discord.Guild):
    if not MEMBER_SYNC_ENABLED: return
    try:
        if not guild.chunked:
            await guild.chunk(cache=True)
        count=mentors.reconcile_roster(guild.id, ((m.id, (r.id for r in m.roles))
                 for m in guild.members if not m.bot))
        log.info('Unit role roster synced: guild %s, %s assignments',guild.id,count)
    except Exception:
        log.exception('Could not reconcile unit roster in guild %s',guild.id)

@bot.event
async def on_member_update(before:discord.Member,after:discord.Member):
    if MEMBER_SYNC_ENABLED and [r.id for r in before.roles]!=[r.id for r in after.roles]:
        mentors.sync_member(after.guild.id,after.id,[r.id for r in after.roles])

@bot.event
async def on_member_join(member:discord.Member):
    if MEMBER_SYNC_ENABLED and not member.bot:
        mentors.sync_member(member.guild.id,member.id,[r.id for r in member.roles])

@bot.event
async def on_member_remove(member:discord.Member):
    if MEMBER_SYNC_ENABLED:
        mentors.remove_member(member.guild.id,member.id)

if __name__=='__main__':
    if not TOKEN:
        raise SystemExit('Missing DISCORD_TOKEN: set Railway Variables or local .env.')
    bot.run(TOKEN,log_handler=None)
