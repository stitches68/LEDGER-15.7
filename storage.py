"""LEDGER: durable, guild-scoped SQLite accounting for fictional Blacksite Credits.

One process / one SQLite database connection. All balance mutations and inventory
changes are transactions. No real-money payouts or Discord billing integrations.
"""
from __future__ import annotations

import sqlite3
import json
from collections import Counter, defaultdict
from contract_objectives import MAX_OBJECTIVES, METRICS, objective_met, validate_bonus
from player_links import validated_links
from contextlib import contextmanager
from pathlib import Path


class RuleError(ValueError):
    """An expected business-rule failure suitable for display to a Discord user."""


class LedgerStorage:
    def __init__(self, path: str | Path = "ledger.sqlite3"):
        self.conn = sqlite3.connect(str(path), timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        # Keep the old tables so an existing reaper-bounty-bot .sqlite3 can be reused.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY,
                board_channel_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contracts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                reward INTEGER NOT NULL CHECK(reward > 0),
                created_by INTEGER NOT NULL,
                claimed_by INTEGER,
                evidence TEXT,
                review_note TEXT,
                approved_by INTEGER,
                status TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK(status IN ('OPEN','CLAIMED','PENDING','PAID','CANCELLED')),
                board_channel_id INTEGER,
                board_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS wallets (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                contract_id INTEGER NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL CHECK(amount > 0),
                approved_by INTEGER NOT NULL,
                paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ledger_settings (
                guild_id INTEGER PRIMARY KEY,
                board_channel_id INTEGER,
                review_channel_id INTEGER,
                shop_channel_id INTEGER,
                shop_message_id INTEGER,
                unit_shop_channel_id INTEGER,
                unit_shop_message_id INTEGER,
                moderator_role_id INTEGER,
                currency_emoji_id INTEGER,
                top_earners_channel_id INTEGER,
                top_earners_message_id INTEGER,
                hub_channel_id INTEGER,
                hub_message_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS units (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                buyer_role_id INTEGER,
                PRIMARY KEY(guild_id,role_id)
            );
            -- A unit's registered role is its permanent treasury identity. Additional
            -- existing guild roles can be linked as membership signals. One Discord
            -- role may identify only one unit in the same guild.
            CREATE TABLE IF NOT EXISTS unit_member_roles (
                guild_id INTEGER NOT NULL,
                member_role_id INTEGER NOT NULL,
                unit_id INTEGER NOT NULL,
                linked_by INTEGER NOT NULL,
                linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,member_role_id),
                FOREIGN KEY(guild_id,unit_id) REFERENCES units(guild_id,role_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_unit_member_roles_unit
                ON unit_member_roles(guild_id,unit_id);
            CREATE TABLE IF NOT EXISTS unit_wallets (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0),
                PRIMARY KEY(guild_id,role_id)
            );
            CREATE TABLE IF NOT EXISTS contract_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                contract_id INTEGER NOT NULL,
                recipient_kind TEXT NOT NULL CHECK(recipient_kind IN ('USER','UNIT')),
                recipient_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('CLAIMED','PENDING','PAID')),
                evidence TEXT,
                submitted_by INTEGER,
                approved_by INTEGER,
                review_note TEXT,
                review_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id,contract_id,recipient_kind,recipient_id),
                FOREIGN KEY(contract_id) REFERENCES contracts(id)
            );
            CREATE INDEX IF NOT EXISTS idx_claims_pending
                ON contract_claims(guild_id,status,contract_id);
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                recipient_kind TEXT NOT NULL CHECK(recipient_kind IN ('USER','UNIT')),
                recipient_id INTEGER NOT NULL,
                amount INTEGER NOT NULL CHECK(amount != 0),
                source TEXT NOT NULL,
                reference_id INTEGER,
                actor_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id,source,reference_id,recipient_kind,recipient_id)
            );
            CREATE TABLE IF NOT EXISTS shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                shop_kind TEXT NOT NULL DEFAULT 'USER' CHECK(shop_kind IN ('USER','UNIT')),
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                price INTEGER NOT NULL CHECK(price>=1),
                stock INTEGER CHECK(stock IS NULL OR stock>=0),
                active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_items ON shop_items(guild_id,active);
            CREATE TABLE IF NOT EXISTS shop_purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                recipient_kind TEXT NOT NULL DEFAULT 'USER' CHECK(recipient_kind IN ('USER','UNIT')),
                recipient_id INTEGER,
                quantity INTEGER NOT NULL CHECK(quantity>=1),
                total_price INTEGER NOT NULL CHECK(total_price>=1),
                status TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK(status IN ('PENDING','FULFILLED')),
                fulfilled_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                fulfilled_at TEXT,
                FOREIGN KEY(item_id) REFERENCES shop_items(id)
            );
        """)
        # Photo-proof upgrades are additive: preserve the original contract/claim rows.
        for table, col, declaration in (
            ('contracts', 'verification_criteria', 'TEXT'),
            ('contract_claims', 'ai_verdict', 'TEXT'),
            ('contract_claims', 'ai_summary', 'TEXT'),
            ('contract_claims', 'proof_message_id', 'INTEGER'),
        ):
            existing = {r['name'] for r in self.conn.execute(f'PRAGMA table_info({table})')}
            if col not in existing:
                self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {declaration}')
        # Older databases lack the following column; do not reset users' balances.
        cols = {r['name'] for r in self.conn.execute("PRAGMA table_info(contracts)")}
        if "payout_mode" not in cols:
            self.conn.execute("ALTER TABLE contracts ADD COLUMN payout_mode TEXT NOT NULL DEFAULT 'USER'")
        if "target_unit_id" not in cols:
            self.conn.execute("ALTER TABLE contracts ADD COLUMN target_unit_id INTEGER")
        # Safe upgrades from the original one-shop LEDGER database: old catalog
        # items and receipts remain *individual* shop records, untouched.
        upgrades = {
            'ledger_settings': {
                'currency_emoji_id': 'INTEGER',
                'unit_shop_channel_id': 'INTEGER',
                'unit_shop_message_id': 'INTEGER',
                'top_earners_channel_id': 'INTEGER',
                'top_earners_message_id': 'INTEGER',
                'hub_channel_id': 'INTEGER',
                'hub_message_id': 'INTEGER',
                'dispatch_channel_id': 'INTEGER',
                'dispatch_message_id': 'INTEGER',
            },
            'units': {'buyer_role_id': 'INTEGER'},
            'shop_items': {
                'shop_kind': "TEXT NOT NULL DEFAULT 'USER' CHECK(shop_kind IN ('USER','UNIT'))",
            },
            'shop_purchases': {
                'recipient_kind': "TEXT NOT NULL DEFAULT 'USER' CHECK(recipient_kind IN ('USER','UNIT'))",
                'recipient_id': 'INTEGER',
            },
        }
        for table, additions in upgrades.items():
            current = {r['name'] for r in self.conn.execute(f'PRAGMA table_info({table})')}
            for name, definition in additions.items():
                if name not in current:
                    self.conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        self.conn.execute("UPDATE shop_purchases SET recipient_id=user_id WHERE recipient_id IS NULL")
        # Every screenshot submission is recorded, even when rejected or paid.
        # This detects reuse across contracts, not just repeat claims.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS proof_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                contract_id INTEGER NOT NULL,
                claim_id INTEGER NOT NULL,
                submitter_id INTEGER NOT NULL,
                image_sha256 TEXT NOT NULL,
                image_dhash TEXT NOT NULL,
                thumb_rgb BLOB NOT NULL,
                planet TEXT NOT NULL,
                difficulty INTEGER NOT NULL,
                teammate_ids TEXT NOT NULL DEFAULT '[]',
                ai_verdict TEXT,
                ai_summary TEXT,
                duplicate_note TEXT,
                status TEXT NOT NULL DEFAULT 'PROCESSING'
                    CHECK(status IN ('PROCESSING','REVIEW','PAID','REJECTED')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(claim_id) REFERENCES contract_claims(id)
            );
            CREATE INDEX IF NOT EXISTS idx_proof_sha
                ON proof_attempts(guild_id, image_sha256);
            CREATE INDEX IF NOT EXISTS idx_proof_claim
                ON proof_attempts(guild_id,claim_id,id);
        """)
        # Keep per-submission screenshot facts with the existing proof audit trail.
        attempt_cols = {r['name'] for r in self.conn.execute('PRAGMA table_info(proof_attempts)')}
        if 'vision_stats' not in attempt_cols:
            self.conn.execute('ALTER TABLE proof_attempts ADD COLUMN vision_stats TEXT')
        # In-game player-name links are uploader-reported annotations on individual
        # proof attempts. They are never treated as independent identity evidence.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS proof_player_links (
                guild_id INTEGER NOT NULL,
                attempt_id INTEGER NOT NULL,
                discord_user_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                submitted_by INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,attempt_id,discord_user_id),
                FOREIGN KEY(attempt_id) REFERENCES proof_attempts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_proof_player_links_member
                ON proof_player_links(guild_id,discord_user_id);
        """)
        # A chosen Discord member belongs to a particular PHYSICAL player card.
        # This column is the authoritative stat link on new submissions: OCR
        # nameplates are unreliable against HD2's animated HUD backgrounds.
        link_cols={r['name'] for r in self.conn.execute('PRAGMA table_info(proof_player_links)')}
        if 'player_column' not in link_cols:
            self.conn.execute('ALTER TABLE proof_player_links ADD COLUMN player_column INTEGER')
        self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_proof_player_links_column
            ON proof_player_links(guild_id,attempt_id,player_column)""")
        # Additive migration: bonus objectives do not rewrite old balances.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS contract_objectives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                contract_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                stat_key TEXT NOT NULL,
                comparison TEXT NOT NULL CHECK(comparison IN ('GTE','LTE')),
                target INTEGER NOT NULL CHECK(target>=0),
                reward INTEGER NOT NULL CHECK(reward>0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_contract_objectives
                ON contract_objectives(guild_id,contract_id);
        """)
        # Opt-in repeatable and multi-mission contracts. Defaults preserve every
        # previous contract's single-screen, once-per-recipient behavior.
        contract_cols={r['name'] for r in self.conn.execute('PRAGMA table_info(contracts)')}
        if 'required_submissions' not in contract_cols:
            self.conn.execute('ALTER TABLE contracts ADD COLUMN required_submissions INTEGER NOT NULL DEFAULT 1')
        if 'repeatable' not in contract_cols:
            self.conn.execute('ALTER TABLE contracts ADD COLUMN repeatable INTEGER NOT NULL DEFAULT 0')
        if 'public_goal' not in contract_cols:
            self.conn.execute('ALTER TABLE contracts ADD COLUMN public_goal INTEGER NOT NULL DEFAULT 0')
        if 'proof_mode' not in contract_cols:
            self.conn.execute("ALTER TABLE contracts ADD COLUMN proof_mode TEXT NOT NULL DEFAULT 'STATS'")
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS contract_progress (
                guild_id INTEGER NOT NULL, contract_id INTEGER NOT NULL,
                recipient_kind TEXT NOT NULL, recipient_id INTEGER NOT NULL,
                cycle INTEGER NOT NULL, completed_count INTEGER NOT NULL DEFAULT 0,
                accumulated_stats TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','PAID')),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,contract_id,recipient_kind,recipient_id,cycle)
            );
            CREATE TABLE IF NOT EXISTS contract_progress_proofs (
                guild_id INTEGER NOT NULL, contract_id INTEGER NOT NULL,
                recipient_kind TEXT NOT NULL, recipient_id INTEGER NOT NULL,
                cycle INTEGER NOT NULL, attempt_id INTEGER NOT NULL,
                attributed_stats TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(guild_id,contract_id,recipient_kind,recipient_id,attempt_id),
                FOREIGN KEY(attempt_id) REFERENCES proof_attempts(id)
            );
            CREATE INDEX IF NOT EXISTS idx_contract_progress_audit
                ON contract_progress_proofs(guild_id,contract_id,recipient_kind,recipient_id,cycle);
        ''')
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS contractor_cards (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                callsign TEXT NOT NULL DEFAULT '',
                specialty TEXT NOT NULL DEFAULT '',
                bio TEXT NOT NULL DEFAULT '',
                motto TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,user_id)
            );
            CREATE TABLE IF NOT EXISTS proof_companion_images (
                guild_id INTEGER NOT NULL,
                attempt_id INTEGER NOT NULL,
                companion_sha256 TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('OPERATION')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id,attempt_id,kind)
            );
            CREATE INDEX IF NOT EXISTS idx_proof_companion_hash
                ON proof_companion_images(guild_id,companion_sha256);
        ''')
        self.conn.commit()
        self._migrate_legacy()
        # If LEDGER stopped mid-verification before posting proof to staff,
        # release the pending claim so the contractor can upload again.
        self._recover_interrupted_proofs()

    # Blacksite contractor dossiers are cosmetics only. They never change financial accounts.
    def contractor_card(self, guild_id: int, user_id: int) -> dict:
        row=self.conn.execute('''SELECT callsign,specialty,bio,motto,updated_at
              FROM contractor_cards WHERE guild_id=? AND user_id=?''', (guild_id,user_id)).fetchone()
        return dict(row) if row else {'callsign':'','specialty':'','bio':'','motto':'','updated_at':None}

    def update_contractor_card(self, guild_id: int, user_id: int, *, callsign: str = '',
                               specialty: str = '', bio: str = '', motto: str = '') -> None:
        caps={'callsign':36,'specialty':50,'bio':220,'motto':120}
        supplied={'callsign':callsign,'specialty':specialty,'bio':bio,'motto':motto}
        for key,val in supplied.items():
            if not isinstance(val,str) or len(val)>caps[key]:
                raise RuleError(f'Invalid {key}: maximum {caps[key]} characters.')
            if '@everyone' in val.lower() or '@here' in val.lower() or '<@' in val:
                raise RuleError('Dossier fields cannot contain mass mentions or user pings.')
        with self.atomic():
            self.conn.execute('''INSERT INTO contractor_cards(guild_id,user_id,callsign,specialty,bio,motto)
                VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET
                callsign=excluded.callsign,specialty=excluded.specialty,
                bio=excluded.bio,motto=excluded.motto,updated_at=CURRENT_TIMESTAMP''',
                (guild_id,user_id,callsign.strip(),specialty.strip(),bio.strip(),motto.strip()))

    def set_public_goal(self, guild_id:int, contract_id:int, count:int) -> None:
        if not 0<=count<=100000:
            raise RuleError('Shared target must be between 0 and 100,000 (0 disables it).')
        with self.atomic():
            row=self.conn.execute('UPDATE contracts SET public_goal=? WHERE guild_id=? AND id=?',
                                  (count,guild_id,contract_id))
            if not row.rowcount: raise RuleError('Contract not found.')

    def set_proof_mode(self, guild_id:int, contract_id:int, mode:str) -> None:
        if mode not in ('STATS','OPERATION','BOTH'):
            raise RuleError('Evidence mode must be STATS, OPERATION or BOTH.')
        with self.atomic():
            row=self.conn.execute('UPDATE contracts SET proof_mode=? WHERE guild_id=? AND id=?',
                                  (mode,guild_id,contract_id))
            if not row.rowcount: raise RuleError('Contract not found.')

    def dispatch_rows(self, guild_id:int, limit:int=8) -> list[dict]:
        """Live global totals use unique accepted screenshot attempts, never teammate count."""
        active=self.conn.execute('''SELECT id,title,status,required_submissions,repeatable,public_goal,reward,payout_mode
            FROM contracts WHERE guild_id=? AND status='OPEN' ORDER BY id DESC LIMIT ?''',
            (guild_id,limit)).fetchall()
        output=[]
        for contract in active:
            c=contract['id']
            accepted=self.conn.execute('''SELECT COUNT(DISTINCT pa.id) FROM proof_attempts pa
                WHERE pa.guild_id=? AND pa.contract_id=? AND pa.status='PAID' ''',(guild_id,c)).fetchone()[0]
            payouts=self.conn.execute('''SELECT COUNT(*) FROM transactions WHERE guild_id=? AND source='contract'
                AND (note LIKE ? OR note LIKE ?) ''',(guild_id,f'Contract #{c} •%',f'Contract #{c}:%')).fetchone()[0]
            progress=self.conn.execute('''SELECT COUNT(*) FROM contract_progress
                WHERE guild_id=? AND contract_id=? AND status='ACTIVE' AND completed_count>0''',
                (guild_id,c)).fetchone()[0]
            output.append({**dict(contract),'accepted':accepted,'payouts':payouts,'in_progress':progress})
        return output

    def track_companion(self,guild_id:int,attempt_id:int,sha256:str) -> bool:
        """True if a different proof has reused this exact operation image."""
        with self.atomic():
            duplicated=self.conn.execute('''SELECT 1 FROM proof_companion_images
                WHERE guild_id=? AND companion_sha256=? AND attempt_id!=? LIMIT 1''',
                (guild_id,sha256,attempt_id)).fetchone() is not None
            self.conn.execute('''INSERT OR REPLACE INTO proof_companion_images
                (guild_id,attempt_id,companion_sha256,kind) VALUES(?,?,?,'OPERATION')''',
                (guild_id,attempt_id,sha256))
            if duplicated:
                self.conn.execute('''UPDATE proof_attempts SET duplicate_note=?
                    WHERE guild_id=? AND id=?''',
                    ('Operation screenshot has already been submitted; requires moderator review.',
                     guild_id,attempt_id))
        return duplicated

    def _recover_interrupted_proofs(self):
        with self.atomic():
            interrupted=self.conn.execute('''
                SELECT pa.id,pa.claim_id FROM proof_attempts pa
                JOIN contract_claims cc ON cc.id=pa.claim_id AND cc.guild_id=pa.guild_id
                WHERE cc.status='PENDING' AND cc.review_message_id IS NULL
                  AND pa.id=(SELECT MAX(p2.id) FROM proof_attempts p2
                             WHERE p2.guild_id=pa.guild_id AND p2.claim_id=pa.claim_id)
                  AND pa.status IN ('PROCESSING','REVIEW')
            ''').fetchall()
            for row in interrupted:
                self.conn.execute('''UPDATE proof_attempts SET status='REJECTED'
                    WHERE id=?''',(row['id'],))
                self.conn.execute('''UPDATE contract_claims SET status='CLAIMED',
                    review_note='Verification interrupted; please resubmit the screenshot.',
                    updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='PENDING' ''',
                    (row['claim_id'],))

    @contextmanager
    def atomic(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def _migrate_legacy(self):
        """Idempotently map old one-time claims to many-recipient claims.

        Existing balances are deliberately not recredited. Previously paid contracts
        become OPEN so everyone else can still complete them, without paying the
        legacy recipient twice.
        """
        with self.atomic():
            self.conn.execute("""
                INSERT OR IGNORE INTO ledger_settings(guild_id,board_channel_id)
                SELECT guild_id,board_channel_id FROM settings
            """)
            self.conn.execute("""
                INSERT OR IGNORE INTO contract_claims
                    (guild_id,contract_id,recipient_kind,recipient_id,status,evidence,
                     submitted_by,approved_by,review_note)
                SELECT guild_id,id,'USER',claimed_by,status,evidence,claimed_by,
                       approved_by,review_note
                FROM contracts WHERE claimed_by IS NOT NULL
                  AND status IN ('CLAIMED','PENDING','PAID')
            """)
            self.conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (guild_id,recipient_kind,recipient_id,amount,source,reference_id,actor_id,note,created_at)
                SELECT guild_id,'USER',user_id,amount,'legacy',id,approved_by,
                       'Imported pre-LEDGER contract payout',paid_at
                FROM payments
            """)
            self.conn.execute("""
                UPDATE contracts SET status='OPEN'
                WHERE status IN ('CLAIMED','PENDING','PAID')
            """)

    def configure(self, guild_id: int, key: str, value: int) -> None:
        allowed = {'board_channel_id', 'review_channel_id', 'shop_channel_id',
                   'shop_message_id', 'unit_shop_channel_id', 'unit_shop_message_id',
                   'moderator_role_id', 'currency_emoji_id',
                   'top_earners_channel_id', 'top_earners_message_id',
                   'hub_channel_id', 'hub_message_id', 'dispatch_channel_id','dispatch_message_id'}
        if key not in allowed:
            raise ValueError('Invalid settings key')
        with self.atomic():
            self.conn.execute("INSERT OR IGNORE INTO ledger_settings(guild_id) VALUES(?)", (guild_id,))
            self.conn.execute(f"UPDATE ledger_settings SET {key}=? WHERE guild_id=?", (value, guild_id))

    def setting(self, guild_id: int, key: str) -> int | None:
        allowed = {'board_channel_id', 'review_channel_id', 'shop_channel_id',
                   'shop_message_id', 'unit_shop_channel_id', 'unit_shop_message_id',
                   'moderator_role_id', 'currency_emoji_id',
                   'top_earners_channel_id', 'top_earners_message_id',
                   'hub_channel_id', 'hub_message_id', 'dispatch_channel_id','dispatch_message_id'}
        if key not in allowed:
            raise ValueError('Invalid settings key')
        row = self.conn.execute(f"SELECT {key} FROM ledger_settings WHERE guild_id=?", (guild_id,)).fetchone()
        return row[key] if row else None

    def top_earners_channels(self):
        """Only guilds with a configured board are polled."""
        return self.conn.execute("""
            SELECT guild_id,top_earners_channel_id AS channel_id,
                   top_earners_message_id AS message_id
            FROM ledger_settings
            WHERE top_earners_channel_id IS NOT NULL AND top_earners_channel_id>0
        """).fetchall()

    def top_earners(self, guild_id: int, kind: str = 'USER', limit: int = 10):
        """Lifetime actual contract payouts, not current wallet balances.

        Manual grants, shop spending and unit transfers cannot inflate earnings.
        """
        if kind not in ('USER','UNIT'):
            raise RuleError('Unknown earnings category.')
        return self.conn.execute("""
            SELECT recipient_id,SUM(amount) AS earned,COUNT(*) AS payouts
            FROM transactions WHERE guild_id=? AND recipient_kind=?
                AND source IN ('contract','legacy','objective','mentorship') AND amount>0
            GROUP BY recipient_id ORDER BY earned DESC,recipient_id ASC LIMIT ?
        """,(guild_id,kind,max(1,min(limit,100)))).fetchall()

    def earnings(self, guild_id: int, kind: str, recipient_id: int) -> tuple[int,int]:
        if kind not in ('USER','UNIT'):
            raise RuleError('Unknown earnings category.')
        result=self.conn.execute("""
            SELECT COALESCE(SUM(amount),0) AS earned,COUNT(*) AS payouts
            FROM transactions WHERE guild_id=? AND recipient_kind=?
                AND recipient_id=? AND source IN ('contract','legacy','objective','mentorship') AND amount>0
        """,(guild_id,kind,recipient_id)).fetchone()
        return result['earned'],result['payouts']

    def earnings_totals(self, guild_id: int) -> dict:
        rows=self.conn.execute("""
            SELECT recipient_kind,COALESCE(SUM(amount),0) AS earned,COUNT(*) AS payouts
            FROM transactions WHERE guild_id=? AND source IN ('contract','legacy','objective','mentorship') AND amount>0
            GROUP BY recipient_kind
        """,(guild_id,)).fetchall()
        return {r['recipient_kind']: {'earned':r['earned'],'payouts':r['payouts']} for r in rows}

    @staticmethod
    def clean_vision_stats(raw: dict | None) -> dict:
        """Only preserve facts the model explicitly read from the screenshot.

        Null/unreadable fields are omitted, not estimated. This does not claim
        the picture is authentic or imply the figures belong to tagged users.
        """
        if not isinstance(raw,dict):
            return {}
        cleaned={}
        limits={
            'kills':100000,'deaths':100000,'stims_used':100000,'samples_collected':100000,
            'shots_fired':1000000,'shots_hit':1000000,'stratagems_used':100000,
            'difficulty_visible':10, 'accidentals':100000,
            'melee_kills':100000,'times_reinforcing':100000,
            'friendly_fire_damage':1000000,
            'missions_completed':25, 'missions_required_visible':25,
        }
        for key,limit in limits.items():
            value=raw.get(key)
            if type(value) is int and 0<=value<=limit and (key!='difficulty_visible' or value>=1):
                cleaned[key]=value
        for key in ('mission_success','extracted','operation_success','operation_screen_verified'):
            if type(raw.get(key)) is bool:
                cleaned[key]=raw[key]
        accuracy=raw.get('accuracy_pct')
        if type(accuracy) in (float,int) and 0 <= accuracy <= 100:
            cleaned['accuracy_pct']=round(float(accuracy),1)
        # Players appear only when OCR recognized individual result columns.
        # Never aggregate an unidentified team's numeric values onto submitter.
        card_count = raw.get('player_card_count')
        if type(card_count) is int and 1 <= card_count <= 4:
            cleaned['player_card_count'] = card_count
        players=raw.get('players')
        if isinstance(players,list) and len(players)<=4:
            sanitized=[];seen_columns=set()
            for player in players:
                if not isinstance(player,dict):
                    continue
                name=str(player.get('game_name','')).strip()[:64]
                if not isinstance(player.get('stats'),dict):
                    continue
                column=player.get('column',0)
                if type(column) is not int or not 1 <= column <= 4:
                    continue
                if column in seen_columns:
                    continue  # One physical card is one player's statistics.
                vals=LedgerStorage.clean_vision_stats({k:v for k,v in player['stats'].items() if k!='players'})
                if vals:
                    seen_columns.add(column)
                    sanitized.append({'game_name':name if len(name)>=2 else '',
                                      'column':column,
                                      'stats':vals})
            if sanitized:
                cleaned['players']=sanitized
        value=raw.get('planet_visible')
        if isinstance(value,str) and 1<=len(value.strip())<=70:
            cleaned['planet_visible']=value.strip()
        return cleaned

    def proof_stats(self, guild_id: int, *, member_id: int | None = None,
                    unit_id: int | None = None) -> dict:
        """Submission-level statistics; a teammate payout is not a second mission.

        `planet` and `difficulty` are declared in the form. The separate
        vision_stats JSON contains only image-derived facts and is counted
        exclusively for accepted submissions with PASS verdicts. On progress
        contracts, PAID proof status means 'accepted', not that money is due.
        """
        where=['pa.guild_id=?']
        args=[guild_id]
        if member_id is not None:
            where.append('''(pa.submitter_id=? OR EXISTS(
                SELECT 1 FROM proof_player_links l
                WHERE l.guild_id=pa.guild_id AND l.attempt_id=pa.id
                  AND l.discord_user_id=?))''')
            args.extend((member_id,member_id))
        if unit_id is not None:
            where += ["cc.recipient_kind='UNIT'",'cc.recipient_id=?']
            args.append(unit_id)
        rows=self.conn.execute('''
            SELECT pa.status,pa.planet,pa.difficulty,pa.ai_verdict,
                   pa.vision_stats,pa.id,pa.created_at,pa.submitter_id
            FROM proof_attempts pa
            JOIN contract_claims cc ON cc.id=pa.claim_id AND cc.guild_id=pa.guild_id
            WHERE '''+' AND '.join(where)+''' ORDER BY pa.id DESC''',args).fetchall()
        outcomes=Counter(r['status'] for r in rows)
        planets=Counter(r['planet'].strip() for r in rows if r['status']=='PAID' and r['planet'])
        difficulties=Counter(r['difficulty'] for r in rows if r['status']=='PAID')
        totals=Counter()
        available=Counter()
        booleans=Counter()
        visible_planets=Counter()
        visible_difficulties=Counter()
        ai_success=0
        for r in rows:
            if r['status']!='PAID' or r['ai_verdict']!='PASS' or not r['vision_stats']:
                continue
            ai_success+=1
            try:
                facts=self.clean_vision_stats(json.loads(r['vision_stats']))
            except (TypeError,ValueError):
                continue
            if 'planet_visible' in facts:
                visible_planets[facts['planet_visible']]+=1
            if 'difficulty_visible' in facts:
                visible_difficulties[facts['difficulty_visible']]+=1
            if 'players' in facts:
                if member_id is not None:
                    rows_for_member=[p for p in facts['players'] if self._player_matches_user(
                        guild_id,r['id'],p['game_name'],member_id,p.get('column'))]
                else:
                    # Server/unit faction totals must exclude outside squad
                    # members left blank in Assign Player Statistics. Their
                    # OCR values stay in the evidence record for transparency.
                    rows_for_member=[p for p in facts['players'] if any(
                        self._player_matches_user(guild_id,r['id'],p['game_name'],uid,p.get('column'))
                        for uid,_ in self.attempt_player_links(guild_id,r['id']))]
                # Never attribute an external player to the faction, and never
                # make one person's stats the total for tagged teammates.
                contributions=[p['stats'] for p in rows_for_member]
            else:
                contributions=[facts] if (member_id is None or r['submitter_id']==member_id) else []
            for player_values in contributions:
                for key in ('kills','deaths','stims_used','samples_collected','shots_fired','shots_hit',
                            'stratagems_used','accidentals','melee_kills','times_reinforcing',
                            'friendly_fire_damage'):
                    if key in player_values:
                        totals[key]+=player_values[key]
                        available[key]+=1
            for key in ('mission_success','extracted'):
                if facts.get(key) is True:
                    booleans[key]+=1
        return {
            'submissions':len(rows),'paid':outcomes['PAID'],'review':outcomes['REVIEW'],
            'rejected':outcomes['REJECTED'],'processing':outcomes['PROCESSING'],
            'ai_passed_paid':ai_success,'totals':dict(totals),
            'available':dict(available),'booleans':dict(booleans),
            'planets':planets.most_common(10),'difficulties':difficulties.most_common(10),
            'visible_planets':visible_planets.most_common(10),
            'visible_difficulties':visible_difficulties.most_common(10),
        }

    def _player_matches_user(self, guild_id: int, attempt_id: int,
                             ocr_name: str, user_id: int, ocr_column: int | None = None) -> bool:
        """New links use a physical card number; legacy links use exact names.

        Do not fall back to an OCR name for a new position-based link, since
        that can silently attribute the wrong player's statistics.
        """
        from squad_ocr import normalize_handle
        target=normalize_handle(ocr_name)
        for link in self.attempt_player_records(guild_id,attempt_id):
            if link['discord_user_id'] != user_id:
                continue
            if link['player_column'] is not None:
                return (type(ocr_column) is int and
                        link['player_column'] == ocr_column)
            return bool(target and normalize_handle(link['game_name']) == target)
        return False

    def recent_proof_stats(self, guild_id: int, submitter_id: int, limit: int = 5):
        """Recent attempts to make submission audits and personal stats easier."""
        return self.conn.execute("""
            SELECT id,contract_id,status,ai_verdict,planet,difficulty,
                   vision_stats,created_at
            FROM proof_attempts WHERE guild_id=? AND submitter_id=?
            ORDER BY id DESC LIMIT ?
        """,(guild_id,submitter_id,max(1,min(limit,20)))).fetchall()

    @staticmethod
    def _validated_objectives(objectives: list[dict], base_reward: int) -> list[dict]:
        if len(objectives) > MAX_OBJECTIVES:
            raise RuleError(f'A contract supports up to {MAX_OBJECTIVES} bonus objectives.')
        try:
            normalized = [validate_bonus(title=x['title'], metric=x['stat_key'],
                target=x['target'], reward=x['reward'], base_reward=base_reward)
                for x in objectives]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuleError(str(exc)) from exc
        if len({x['title'].casefold() for x in normalized}) != len(normalized):
            raise RuleError('Each bonus objective must have a unique name.')
        return normalized

    def _insert_bonus(self, guild_id: int, contract_id: int, bonus: dict) -> int:
        cur = self.conn.execute('''
            INSERT INTO contract_objectives(guild_id,contract_id,title,stat_key,
                comparison,target,reward) VALUES(?,?,?,?,?,?,?)
        ''', (guild_id,contract_id,bonus['title'],bonus['stat_key'],bonus['comparison'],
              bonus['target'],bonus['reward']))
        return cur.lastrowid

    def objectives(self, guild_id: int, contract_id: int):
        return self.conn.execute('''SELECT * FROM contract_objectives
            WHERE guild_id=? AND contract_id=? ORDER BY id''',
            (guild_id,contract_id)).fetchall()

    def add_objective(self, guild_id: int, contract_id: int, bonus: dict) -> int:
        with self.atomic():
            offer = self.get_contract(guild_id,contract_id)
            if not offer or offer['status'] != 'OPEN':
                raise RuleError('Contract is missing or closed.')
            if self.conn.execute('''SELECT 1 FROM contract_claims
                WHERE guild_id=? AND contract_id=? AND status IN ('PENDING','PAID') LIMIT 1''',
                (guild_id,contract_id)).fetchone():
                raise RuleError('Bonuses cannot be changed after proof is pending or paid.')
            if self.conn.execute('''SELECT 1 FROM proof_attempts
                WHERE guild_id=? AND contract_id=? LIMIT 1''',(guild_id,contract_id)).fetchone():
                raise RuleError('Bonuses are locked after the first proof submission.')
            current = self.objectives(guild_id,contract_id)
            if len(current) >= MAX_OBJECTIVES:
                raise RuleError(f'Maximum {MAX_OBJECTIVES} bonus objectives per contract.')
            bonus = self._validated_objectives([bonus],offer['reward'])[0]
            if any(r['title'].casefold() == bonus['title'].casefold() for r in current):
                raise RuleError('Bonus objective names must be unique.')
            return self._insert_bonus(guild_id,contract_id,bonus)

    def screenshot_player_stats(self, guild_id: int, attempt, kind: str,
                                recipient_id: int) -> dict:
        # Per-recipient, strict name match; no shared squad totals.
        if not attempt or not attempt['vision_stats']:
            return {}
        try:
            facts=self.clean_vision_stats(json.loads(attempt['vision_stats']))
        except (TypeError,ValueError):
            return {}
        players=facts.get('players',[])
        if not players:
            # Legacy one-player screenshot fields were attached to submitter.
            return {k:v for k,v in facts.items() if k!='players'} if (
                kind=='UNIT' or recipient_id==attempt['submitter_id']) else {}
        if kind=='UNIT':
            totals=defaultdict(int)
            for player in players:
                for key,value in player['stats'].items():
                    if type(value) is int:
                        totals[key]+=value
            return dict(totals)
        matches=[p['stats'] for p in players if self._player_matches_user(
            guild_id,attempt['id'],p['game_name'],recipient_id,p.get('column'))]
        return dict(matches[0]) if len(matches)==1 else {}

    def bonus_results(self, guild_id: int, contract_id: int, attempt, *,
                      recipient_kind: str | None = None, recipient_id: int | None = None) -> list:
        # Only PASS screenshots with readable exact OCR field values earn bonuses.
        if not attempt or attempt['ai_verdict'] != 'PASS' or not attempt['vision_stats']:
            return []
        try:
            stats = self.clean_vision_stats(json.loads(attempt['vision_stats']))
        except (ValueError, TypeError):
            return []
        if recipient_kind is not None and recipient_id is not None:
            stats=self.screenshot_player_stats(guild_id,attempt,recipient_kind,recipient_id)
        elif stats.get('players'):
            # Team totals must not unlock a personal bonus without attribution.
            return []
        return [r for r in self.objectives(guild_id,contract_id)
                if objective_met(r,stats)]

    def stat_leaderboard(self, guild_id: int, stat: str, limit: int = 10):
        # Each OCR player column is attributed only through an explicit exact
        # game-name -> Discord mapping. Never give all squad kills to uploader.
        if stat not in ('kills','stims_used'):
            raise RuleError('Unsupported OCR statistic ranking.')
        rows=self.conn.execute('''SELECT id,submitter_id,image_sha256,vision_stats
            FROM proof_attempts WHERE guild_id=? AND status='PAID'
              AND ai_verdict='PASS' AND vision_stats IS NOT NULL
            ORDER BY id ASC''',(guild_id,)).fetchall()
        seen=set();totals=defaultdict(int);readings=defaultdict(int)
        for row in rows:
            if row['image_sha256'] in seen:
                continue
            seen.add(row['image_sha256'])
            try:
                facts=self.clean_vision_stats(json.loads(row['vision_stats']))
            except (ValueError,TypeError):
                continue
            if facts.get('players'):
                linked=self.attempt_player_records(guild_id,row['id'])
                for link in linked:
                    uid=link['discord_user_id']
                    matches=[p['stats'] for p in facts['players']
                             if self._player_matches_user(guild_id,row['id'],
                                 p['game_name'],uid,p.get('column'))]
                    if len(matches)==1 and stat in matches[0]:
                        totals[uid]+=matches[0][stat]
                        readings[uid]+=1
            elif stat in facts:
                totals[row['submitter_id']]+=facts[stat]
                readings[row['submitter_id']]+=1
        return [{'recipient_id':rid,'total':total,'screenshots':readings[rid]}
                for rid,total in sorted(totals.items(),key=lambda item:(-item[1],item[0]))[:max(1,min(limit,50))]]

    def member_summary(self, guild_id: int, member_id: int) -> dict:
        earned,payouts=self.earnings(guild_id,'USER',member_id)
        rank=self.conn.execute('''SELECT COUNT(*)+1 FROM (
            SELECT recipient_id,SUM(amount) AS earned FROM transactions
            WHERE guild_id=? AND recipient_kind='USER' AND amount>0
                AND source IN ('contract','legacy','objective','mentorship')
            GROUP BY recipient_id HAVING earned>?
        )''',(guild_id,earned)).fetchone()[0] if earned else None
        bonuses=self.conn.execute('''SELECT COUNT(*) AS awards,COALESCE(SUM(amount),0) AS total
            FROM transactions WHERE guild_id=? AND recipient_kind='USER'
            AND recipient_id=? AND source='objective' AND amount>0''',
            (guild_id,member_id)).fetchone()
        return {'balance':self.balance(guild_id,'USER',member_id),
                'earned':earned,'payouts':payouts,'rank':rank,
                'bonuses':bonuses['awards'],'bonus_credits':bonuses['total'],
                'stats':self.proof_stats(guild_id,member_id=member_id)}

    def create_contract(self, guild_id: int, title: str, details: str, reward: int,
                        actor_id: int, board_channel_id: int, payout_mode: str = 'USER',
                        target_unit_id: int | None = None,
                        verification_criteria: str | None = None,
                        objectives: list[dict] | None = None,
                        required_submissions: int = 1, repeatable: bool = False) -> int:
        if payout_mode not in ('USER', 'UNIT') or reward < 1:
            raise RuleError('Invalid payout configuration.')
        if payout_mode == 'USER' and target_unit_id is not None:
            raise RuleError('An individual contract cannot target a unit.')
        if target_unit_id is not None and not self.get_unit(guild_id, target_unit_id):
            raise RuleError('Target unit must be registered first.')
        self._validate_progress_settings(required_submissions,repeatable)
        bonuses = self._validated_objectives(objectives or [], reward)
        with self.atomic():
            cur = self.conn.execute("""
                INSERT INTO contracts(guild_id,title,details,reward,created_by,
                    board_channel_id,payout_mode,target_unit_id,verification_criteria,
                    required_submissions,repeatable,status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'OPEN')
            """, (guild_id,title,details,reward,actor_id,board_channel_id,payout_mode,
                    target_unit_id,verification_criteria,required_submissions,int(repeatable)))
            contract_id = cur.lastrowid
            for bonus in bonuses:
                self._insert_bonus(guild_id, contract_id, bonus)
            return contract_id

    @staticmethod
    def _validate_progress_settings(required_submissions: int, repeatable: bool) -> None:
        if type(required_submissions) is not int or not 1 <= required_submissions <= 25:
            raise RuleError('Required mission submissions must be between 1 and 25.')
        if type(repeatable) is not bool:
            raise RuleError('Repeatable must be Yes or No.')

    def configure_progress(self, guild_id: int, contract_id: int,
                           required_submissions: int, repeatable: bool) -> bool:
        # Never invalidate recorded work.
        self._validate_progress_settings(required_submissions,repeatable)
        with self.atomic():
            offer=self.get_contract(guild_id,contract_id)
            if not offer or offer['status']!='OPEN':
                raise RuleError('Only open contracts can be configured.')
            if self.conn.execute('''SELECT 1 FROM proof_attempts WHERE guild_id=?
                AND contract_id=? LIMIT 1''',(guild_id,contract_id)).fetchone():
                raise RuleError('Progress settings are locked after the first screenshot submission.')
            cur=self.conn.execute('''UPDATE contracts
                SET required_submissions=?,repeatable=?,updated_at=CURRENT_TIMESTAMP
                WHERE guild_id=? AND id=?''',
                (required_submissions,int(repeatable),guild_id,contract_id))
            return bool(cur.rowcount)

    def progress_for(self, guild_id: int, contract_id: int,
                     recipient_kind: str, recipient_id: int) -> dict:
        offer=self.get_contract(guild_id,contract_id)
        if not offer:
            raise RuleError('Contract not found.')
        row=self.conn.execute('''SELECT * FROM contract_progress WHERE guild_id=?
            AND contract_id=? AND recipient_kind=? AND recipient_id=?
            ORDER BY cycle DESC LIMIT 1''',
            (guild_id,contract_id,recipient_kind,recipient_id)).fetchone()
        return {'cycle':row['cycle'] if row else 1,
                'count':row['completed_count'] if row else 0,
                'required':offer['required_submissions'],
                'status':row['status'] if row else 'NOT STARTED',
                'repeatable':bool(offer['repeatable'])}

    def set_contract_criteria(self, guild_id: int, contract_id: int, criteria: str) -> bool:
        if not 8 <= len(criteria.strip()) <= 1000:
            raise RuleError('Screenshot criteria must be 8–1000 characters.')
        with self.atomic():
            result = self.conn.execute('''
                UPDATE contracts SET verification_criteria=?, updated_at=CURRENT_TIMESTAMP
                WHERE guild_id=? AND id=?
            ''', (criteria.strip(),guild_id,contract_id))
            return result.rowcount == 1

    def save_proof_message(self, guild_id: int, claim_id: int, message_id: int) -> None:
        with self.atomic():
            result = self.conn.execute('''
                UPDATE contract_claims SET proof_message_id=?
                WHERE guild_id=? AND id=? AND status='PENDING'
            ''', (message_id, guild_id, claim_id))
            if result.rowcount != 1:
                raise RuleError('Cannot attach proof to an inactive submission.')

    def save_analysis(self, guild_id: int, claim_id: int, verdict: str, summary: str) -> None:
        if verdict not in ('PASS', 'FAIL', 'UNCERTAIN', 'UNAVAILABLE'):
            raise RuleError('Unknown AI verification verdict.')
        with self.atomic():
            result = self.conn.execute('''
                UPDATE contract_claims SET ai_verdict=?, ai_summary=?
                WHERE guild_id=? AND id=? AND status='PENDING'
            ''',(verdict,summary[:1600],guild_id,claim_id))
            if result.rowcount != 1:
                raise RuleError('Cannot attach an assessment to an inactive submission.')

    def get_contract(self, guild_id: int, contract_id: int):
        return self.conn.execute("SELECT * FROM contracts WHERE guild_id=? AND id=?",
                                 (guild_id,contract_id)).fetchone()

    def attach_board(self, guild_id: int, contract_id: int, message_id: int) -> None:
        with self.atomic():
            self.conn.execute("UPDATE contracts SET board_message_id=? WHERE guild_id=? AND id=?",
                              (message_id,guild_id,contract_id))

    def delete_unposted(self, guild_id: int, contract_id: int) -> None:
        with self.atomic():
            self.conn.execute("DELETE FROM contracts WHERE guild_id=? AND id=? AND board_message_id IS NULL",
                              (guild_id,contract_id))

    def board_contracts(self):
        return self.conn.execute("SELECT * FROM contracts WHERE board_message_id IS NOT NULL AND status='OPEN'").fetchall()

    def set_contract_open(self, guild_id: int, contract_id: int, open_: bool) -> bool:
        target, prior = ('OPEN','CANCELLED') if open_ else ('CANCELLED','OPEN')
        with self.atomic():
            result = self.conn.execute("""
                UPDATE contracts SET status=?,updated_at=CURRENT_TIMESTAMP
                WHERE guild_id=? AND id=? AND status=?
            """, (target,guild_id,contract_id,prior))
            return result.rowcount == 1

    def counts(self, guild_id: int, contract_id: int):
        r = self.conn.execute("""
            SELECT SUM(CASE WHEN status='CLAIMED' THEN 1 ELSE 0 END) as claimed,
                   SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                   SUM(CASE WHEN status='PAID' THEN 1 ELSE 0 END) as paid
            FROM contract_claims WHERE guild_id=? AND contract_id=?
        """, (guild_id,contract_id)).fetchone()
        return tuple(int(x or 0) for x in r)

    def register_unit(self, guild_id: int, role_id: int, name: str) -> None:
        with self.atomic():
            alias = self.conn.execute("""
                SELECT unit_id FROM unit_member_roles
                WHERE guild_id=? AND member_role_id=?
            """, (guild_id,role_id)).fetchone()
            if alias and alias['unit_id'] != role_id:
                raise RuleError('This role is already linked as membership for a different unit. Unlink it first.')
            self.conn.execute("""
                INSERT INTO units(guild_id,role_id,name) VALUES(?,?,?)
                ON CONFLICT(guild_id,role_id) DO UPDATE SET name=excluded.name
            """, (guild_id,role_id,name))

    def register_units_from_roles(self, guild_id: int, roles: list[tuple[int, str]]) -> tuple[list[int], list[int]]:
        """Register one independent treasury per existing Discord role atomically.

        Role membership itself remains authoritative: anybody who holds a selected
        role is automatically in that unit, without a separate member roster or
        any role edits by the bot. Existing treasuries are never reset.
        Returns (newly_created_ids, already_registered_ids).
        """
        if not 1 <= len(roles) <= 25:
            raise RuleError('Select between 1 and 25 roles at a time.')
        if len({role_id for role_id, _ in roles}) != len(roles):
            raise RuleError('A role was selected more than once.')
        if any(role_id <= 0 or not (1 <= len(name.strip()) <= 100) for role_id, name in roles):
            raise RuleError('Invalid role selection.')
        new_ids: list[int] = []
        existing_ids: list[int] = []
        with self.atomic():
            for role_id, name in roles:
                alias = self.conn.execute('''
                    SELECT unit_id FROM unit_member_roles
                    WHERE guild_id=? AND member_role_id=?
                ''', (guild_id, role_id)).fetchone()
                if alias and alias['unit_id'] != role_id:
                    raise RuleError(f'Role {role_id} is already paired to a different unit. Unlink it first.')
                if self.get_unit(guild_id, role_id):
                    existing_ids.append(role_id)
                    self.conn.execute('UPDATE units SET name=? WHERE guild_id=? AND role_id=?',
                                      (name.strip(), guild_id, role_id))
                else:
                    new_ids.append(role_id)
                    self.conn.execute('''
                        INSERT INTO units(guild_id,role_id,name) VALUES(?,?,?)
                    ''', (guild_id, role_id, name.strip()))
        return new_ids, existing_ids

    def link_unit_role(self, guild_id: int, unit_id: int, member_role_id: int,
                       actor_id: int) -> bool:
        """Link an existing Discord role to a unit without modifying guild roles.

        False means already linked to this same unit; conflicting assignments are
        rejected. The registered unit role always counts as membership itself.
        """
        with self.atomic():
            if not self.get_unit(guild_id,unit_id):
                raise RuleError('Register the destination unit first using /unit register.')
            primary_unit = self.get_unit(guild_id,member_role_id)
            if primary_unit:
                if member_role_id == unit_id:
                    raise RuleError('The registered unit role already grants membership automatically.')
                raise RuleError('That Discord role is already the primary role of another registered unit.')
            existing = self.conn.execute("""
                SELECT unit_id FROM unit_member_roles
                WHERE guild_id=? AND member_role_id=?
            """,(guild_id,member_role_id)).fetchone()
            if existing:
                if existing['unit_id'] == unit_id:
                    return False
                raise RuleError('That Discord role is already linked to another unit. Unlink it first.')
            self.conn.execute("""
                INSERT INTO unit_member_roles(guild_id,member_role_id,unit_id,linked_by)
                VALUES(?,?,?,?)
            """,(guild_id,member_role_id,unit_id,actor_id))
            return True

    def unlink_unit_role(self, guild_id: int, unit_id: int, member_role_id: int) -> bool:
        if member_role_id == unit_id:
            raise RuleError('The primary unit role cannot be unlinked. It always grants membership.')
        with self.atomic():
            cur = self.conn.execute("""
                DELETE FROM unit_member_roles
                WHERE guild_id=? AND unit_id=? AND member_role_id=?
            """,(guild_id,unit_id,member_role_id))
            return cur.rowcount == 1

    def unit_member_roles(self, guild_id: int, unit_id: int) -> list[int]:
        """All linked role IDs, excluding the automatically valid primary role."""
        return [row['member_role_id'] for row in self.conn.execute("""
            SELECT member_role_id FROM unit_member_roles
            WHERE guild_id=? AND unit_id=? ORDER BY member_role_id
        """,(guild_id,unit_id))]

    def member_in_unit(self, guild_id: int, unit_id: int, member_role_ids) -> bool:
        """Resolve unit membership from a current member's Discord role IDs."""
        if not self.get_unit(guild_id,unit_id):
            return False
        held = set(member_role_ids)
        return unit_id in held or bool(held.intersection(self.unit_member_roles(guild_id,unit_id)))

    def member_units(self, guild_id: int, member_role_ids) -> list:
        """Return registered units the member currently belongs to."""
        held = set(member_role_ids)
        return [unit for unit in self.units(guild_id)
                if self.member_in_unit(guild_id,unit['role_id'],held)]

    def set_unit_buyer_role(self, guild_id: int, unit_id: int, buyer_role_id: int | None) -> bool:
        with self.atomic():
            cur = self.conn.execute("""
                UPDATE units SET buyer_role_id=? WHERE guild_id=? AND role_id=?
            """, (buyer_role_id,guild_id,unit_id))
            return cur.rowcount == 1

    def get_unit(self, guild_id: int, role_id: int):
        return self.conn.execute("SELECT * FROM units WHERE guild_id=? AND role_id=?",
                                 (guild_id,role_id)).fetchone()

    def units(self, guild_id: int):
        return self.conn.execute("SELECT * FROM units WHERE guild_id=? ORDER BY name LIMIT 100",
                                 (guild_id,)).fetchall()

    def claim(self, guild_id: int, contract_id: int, kind: str, recipient_id: int,
              actor_id: int) -> tuple[bool, str]:
        with self.atomic():
            offer = self.get_contract(guild_id,contract_id)
            if not offer or offer['status'] != 'OPEN':
                return False, 'This contract is closed or does not exist.'
            if offer['payout_mode'] != kind:
                return False, 'This contract uses a different payout type.'
            if kind == 'UNIT':
                if not self.get_unit(guild_id,recipient_id):
                    return False, 'That unit is not registered.'
                if offer['target_unit_id'] is not None and recipient_id != offer['target_unit_id']:
                    return False, 'This contract is reserved for a different unit.'
            old = self.get_claim(guild_id,contract_id,kind,recipient_id)
            if old:
                if old['status']=='PAID' and offer['repeatable']:
                    previous=self.conn.execute('''SELECT cycle,status FROM contract_progress
                        WHERE guild_id=? AND contract_id=? AND recipient_kind=?
                        AND recipient_id=? ORDER BY cycle DESC LIMIT 1''',
                        (guild_id,contract_id,kind,recipient_id)).fetchone()
                    if previous and previous['status']=='PAID':
                        self.conn.execute('''INSERT INTO contract_progress(
                            guild_id,contract_id,recipient_kind,recipient_id,cycle)
                            VALUES(?,?,?,?,?)''',
                            (guild_id,contract_id,kind,recipient_id,previous['cycle']+1))
                    self.conn.execute('''UPDATE contract_claims SET status='CLAIMED',
                        evidence=NULL,review_note=NULL,review_message_id=NULL,
                        ai_verdict=NULL,ai_summary=NULL,updated_at=CURRENT_TIMESTAMP
                        WHERE id=?''',(old['id'],))
                    return True,'New repeatable contract cycle claimed. Submit fresh proof.'
                return False, {'CLAIMED':'Already claimed; submit your proof.',
                               'PENDING':'Submission awaiting review.',
                               'PAID':'This contractor or unit has already been paid.'}[old['status']]
            self.conn.execute("""
                INSERT INTO contract_claims(guild_id,contract_id,recipient_kind,
                    recipient_id,status,submitted_by)
                VALUES(?,?,?,?,'CLAIMED',?)
            """, (guild_id,contract_id,kind,recipient_id,actor_id))
            return True, 'Contract claimed. Submit proof when complete.'

    def submit(self, guild_id: int, contract_id: int, kind: str, recipient_id: int,
               actor_id: int, evidence: str) -> tuple[bool, str, int | None]:
        with self.atomic():
            offer = self.get_contract(guild_id,contract_id)
            if not offer or offer['status'] != 'OPEN' or offer['payout_mode'] != kind:
                return False, 'Contract is closed or has a different payout type.', None
            if kind == 'UNIT':
                if not self.get_unit(guild_id,recipient_id):
                    return False, 'That unit is not registered.', None
                if offer['target_unit_id'] is not None and recipient_id != offer['target_unit_id']:
                    return False, 'This contract is reserved for another unit.', None
            row = self.get_claim(guild_id,contract_id,kind,recipient_id)
            if row and row['status'] != 'CLAIMED':
                if row['status']=='PAID' and offer['repeatable']:
                    # Begin a fresh cycle using the existing unique claim key.
                    self.conn.execute('''UPDATE contract_claims SET status='CLAIMED'
                        WHERE id=?''',(row['id'],))
                else:
                    return False, ('Already paid.' if row['status']=='PAID' else 'Already awaiting review.'), None
            if row:
                self.conn.execute("""
                    UPDATE contract_claims SET status='PENDING',evidence=?,submitted_by=?,
                        review_note=NULL,review_message_id=NULL,ai_verdict=NULL,
                        ai_summary=NULL,proof_message_id=NULL,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (evidence,actor_id,row['id']))
                claim_id = row['id']
            else:
                cur = self.conn.execute("""
                    INSERT INTO contract_claims(guild_id,contract_id,recipient_kind,
                        recipient_id,status,evidence,submitted_by)
                    VALUES(?,?,?,?,'PENDING',?,?)
                """, (guild_id,contract_id,kind,recipient_id,evidence,actor_id))
                claim_id = cur.lastrowid
            return True, 'Proof submitted for moderation.', claim_id

    def get_claim(self, guild_id: int, contract_id: int, kind: str, recipient_id: int):
        return self.conn.execute("""
            SELECT * FROM contract_claims WHERE guild_id=? AND contract_id=?
                AND recipient_kind=? AND recipient_id=?
        """, (guild_id,contract_id,kind,recipient_id)).fetchone()

    def claim_by_id(self, guild_id: int, claim_id: int):
        return self.conn.execute("SELECT * FROM contract_claims WHERE guild_id=? AND id=?",
                                 (guild_id,claim_id)).fetchone()

    def pending_claims(self, guild_id: int, limit: int = 15):
        return self.conn.execute("""
            SELECT cc.*, c.title, c.reward FROM contract_claims cc
            JOIN contracts c ON c.id=cc.contract_id AND c.guild_id=cc.guild_id
            WHERE cc.guild_id=? AND cc.status='PENDING'
            ORDER BY cc.id ASC LIMIT ?
        """, (guild_id,limit)).fetchall()

    def outstanding_reviews(self):
        return self.conn.execute("""
            SELECT id,guild_id,review_message_id FROM contract_claims
            WHERE status='PENDING' AND review_message_id IS NOT NULL
        """).fetchall()

    def attach_review(self, guild_id: int, claim_id: int, message_id: int):
        with self.atomic():
            self.conn.execute("""
                UPDATE contract_claims SET review_message_id=?
                WHERE guild_id=? AND id=? AND status='PENDING'
            """, (message_id,guild_id,claim_id))

    def reject(self, guild_id: int, claim_id: int, reason: str) -> tuple[bool, str]:
        with self.atomic():
            updated = self.conn.execute("""
                UPDATE contract_claims SET status='CLAIMED',review_note=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE guild_id=? AND id=? AND status='PENDING'
            """, (reason,guild_id,claim_id))
            if updated.rowcount:
                self.conn.execute("""UPDATE proof_attempts SET status='REJECTED'
                    WHERE guild_id=? AND claim_id=? AND status IN ('PROCESSING','REVIEW')""",
                    (guild_id,claim_id))
            return (True,'Revision requested; claimant may resubmit.') if updated.rowcount else (False,'This claim is not pending.')

    def approve(self, guild_id: int, claim_id: int, approver_id: int) -> tuple[bool, str]:
        with self.atomic():
            row = self.claim_by_id(guild_id,claim_id)
            if row is None or row['status'] != 'PENDING':
                return False,'This submission is not pending.'
            offer = self.get_contract(guild_id,row['contract_id'])
            if not offer or offer['status'] != 'OPEN':
                return False,'The contract is closed.'
            self.conn.execute("""
                UPDATE contract_claims SET status='PAID',approved_by=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='PENDING'
            """, (approver_id,claim_id))
            self._change_balance(guild_id,row['recipient_kind'],row['recipient_id'],offer['reward'])
            self._transaction(guild_id,row['recipient_kind'],row['recipient_id'],offer['reward'],
                              'contract',claim_id,approver_id,f"Contract #{offer['id']}: {offer['title']}")
            self.conn.execute("""UPDATE proof_attempts SET status='PAID'
                WHERE guild_id=? AND claim_id=? AND status IN ('PROCESSING','REVIEW')""",
                (guild_id,claim_id))
            return True,f"Paid {offer['reward']:,} Blacksite Credits to {row['recipient_kind'].lower()} {row['recipient_id']}."

    def record_attempt(self, guild_id: int, contract_id: int, claim_id: int,
                       submitter_id: int, image_hash: str, image_dhash: str,
                       thumb_rgb: bytes, planet: str, difficulty: int,
                       teammate_ids: list[int],
                       player_links: list[tuple[int,str]] | None = None,
                       stat_link_ids: list[int] | None = None,
                       player_columns: dict[int,int] | None = None) -> tuple[int, str]:
        '''Register before AI runs. Cross-contract duplicates go to review.

        This is atomic, so two simultaneous copies can't both auto-pay.
        Checks all older attempts, including rejections; exact matches always win.
        Visual match is deliberately conservative and advisory only.
        '''
        import json
        pairs = dict(player_links or [])
        if len(pairs) != len(player_links or []):
            from player_links import PlayerLinkError
            raise PlayerLinkError('A Discord member was linked more than once.')
        # Unit screenshots can link squad members for individual career stats
        # without giving those members a personal credit payout.
        allowed_stat_members = teammate_ids if stat_link_ids is None else stat_link_ids
        cleaned_links = validated_links(submitter_id,allowed_stat_members,pairs) if player_links is not None else []
        columns=player_columns or {}
        if not isinstance(columns,dict) or any(
                type(uid) is not int or type(col) is not int or col not in (1,2,3,4)
                for uid,col in columns.items()):
            raise RuleError('Invalid player-card positions.')
        if len(set(columns.values())) != len(columns):
            raise RuleError('Two members cannot be assigned to the same player card.')
        if set(columns) - {uid for uid,_ in cleaned_links}:
            raise RuleError('A player card must be assigned to a selected Discord member.')
        with self.atomic():
            old = self.conn.execute('''
                SELECT id,contract_id,submitter_id FROM proof_attempts
                WHERE guild_id=? AND image_sha256=? ORDER BY id LIMIT 1
            ''', (guild_id,image_hash)).fetchone()
            note = (f'Exact image already submitted (attempt #{old["id"]}, '
                    f'contract #{old["contract_id"]}).') if old else ''
            if not note:
                candidates = self.conn.execute('''
                    SELECT id,contract_id,image_dhash,thumb_rgb FROM proof_attempts
                    WHERE guild_id=? ORDER BY id DESC LIMIT 1500
                ''', (guild_id,)).fetchall()
                for prev in candidates:
                    if (prev['image_dhash'] is not None and
                        (int(prev['image_dhash'],16)^int(image_dhash,16)).bit_count() <= 4 and
                        len(prev['thumb_rgb']) == len(thumb_rgb)):
                        # Normalized thumbnail pixel difference. Strongly similar
                        # images are REVIEWED, not categorically rejected.
                        delta = sum(abs(a-b) for a,b in zip(prev['thumb_rgb'],thumb_rgb))
                        if delta / len(thumb_rgb) <= 3.0:
                            note = (f'Visually similar to previous attempt #{prev["id"]} '
                                    f'(contract #{prev["contract_id"]}); manual review required.')
                            break
            cur = self.conn.execute('''
                INSERT INTO proof_attempts(guild_id,contract_id,claim_id,submitter_id,
                    image_sha256,image_dhash,thumb_rgb,planet,difficulty,teammate_ids,duplicate_note)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ''', (guild_id,contract_id,claim_id,submitter_id,image_hash,image_dhash,
                  thumb_rgb,planet,difficulty,json.dumps(teammate_ids),note))
            attempt_id=cur.lastrowid
            for linked_user, handle in cleaned_links:
                self.conn.execute('''INSERT INTO proof_player_links(
                    guild_id,attempt_id,discord_user_id,game_name,submitted_by,player_column)
                    VALUES(?,?,?,?,?,?)''',
                    (guild_id,attempt_id,linked_user,handle,submitter_id,
                     columns.get(linked_user)))
            return attempt_id,note

    def attempt_player_links(self, guild_id: int, attempt_id: int) -> list[tuple[int,str]]:
        '''An attempt's explicit uploader-reported names (not verified identities).'''
        rows=self.conn.execute('''SELECT discord_user_id,game_name FROM proof_player_links
            WHERE guild_id=? AND attempt_id=? ORDER BY discord_user_id''',
            (guild_id,attempt_id)).fetchall()
        return [(r['discord_user_id'],r['game_name']) for r in rows]

    def attempt_player_records(self, guild_id: int, attempt_id: int) -> list[dict]:
        """Discord-linked player cards with stable player-column positions."""
        rows=self.conn.execute('''SELECT discord_user_id,game_name,player_column
            FROM proof_player_links WHERE guild_id=? AND attempt_id=?
            ORDER BY player_column,discord_user_id''',
            (guild_id,attempt_id)).fetchall()
        return [dict(r) for r in rows]

    def member_handles(self, guild_id: int, user_id: int, limit: int=5) -> list[dict]:
        '''Names reported for this member on PAID screenshots, most frequently first.'''
        rows=self.conn.execute('''SELECT ppl.game_name,COUNT(*) AS sightings,
            MAX(ppl.created_at) AS last_seen
            FROM proof_player_links ppl
            JOIN proof_attempts pa ON pa.id=ppl.attempt_id AND pa.guild_id=ppl.guild_id
            WHERE ppl.guild_id=? AND ppl.discord_user_id=? AND pa.status='PAID'
              AND ppl.player_column IS NULL
              -- Position-linked OCR labels are suggestions, not confirmed handles.
            GROUP BY ppl.game_name COLLATE NOCASE
            ORDER BY sightings DESC,last_seen DESC LIMIT ?''',
            (guild_id,user_id,max(1,min(limit,20)))).fetchall()
        return [dict(row) for row in rows]

    def unlink_player_name(self, guild_id: int, attempt_id: int, member_id: int) -> bool:
        '''Remove a disputed association; intentionally never mutate prior payouts.'''
        with self.atomic():
            cur=self.conn.execute('''DELETE FROM proof_player_links
                WHERE guild_id=? AND attempt_id=? AND discord_user_id=?''',
                (guild_id,attempt_id,member_id))
            return bool(cur.rowcount)

    def attempt(self, guild_id: int, attempt_id: int):
        return self.conn.execute('''SELECT * FROM proof_attempts WHERE guild_id=? AND id=?''',
                                 (guild_id,attempt_id)).fetchone()

    def latest_attempt(self, guild_id: int, claim_id: int):
        return self.conn.execute('''SELECT * FROM proof_attempts
            WHERE guild_id=? AND claim_id=? ORDER BY id DESC LIMIT 1''',
            (guild_id,claim_id)).fetchone()

    def set_attempt_verdict(self, guild_id: int, attempt_id: int,
                            verdict: str, summary: str, stats: dict | None = None) -> None:
        if verdict not in ('PASS','FAIL','UNCERTAIN','UNAVAILABLE'):
            raise RuleError('Invalid verification result.')
        with self.atomic():
            upd = self.conn.execute('''UPDATE proof_attempts SET ai_verdict=?,ai_summary=?,vision_stats=?
                WHERE guild_id=? AND id=? AND status='PROCESSING' ''',
                (verdict,summary[:1600],json.dumps(self.clean_vision_stats(stats)),guild_id,attempt_id))
            if not upd.rowcount:
                raise RuleError('This proof attempt has already been processed.')

    def set_attempt_status(self, guild_id: int, attempt_id: int, status: str) -> None:
        if status not in ('REVIEW','REJECTED'):
            raise RuleError('Invalid review status.')
        with self.atomic():
            self.conn.execute('''UPDATE proof_attempts SET status=?
                WHERE guild_id=? AND id=? AND status='PROCESSING' ''',
                (status,guild_id,attempt_id))

    def attach_proof_review(self, guild_id: int, claim_id: int,
                            evidence_url: str, message_id: int) -> None:
        with self.atomic():
            upd = self.conn.execute('''UPDATE contract_claims
                SET evidence=?,proof_message_id=?,review_message_id=?
                WHERE guild_id=? AND id=? AND status='PENDING' ''',
                (evidence_url,message_id,message_id,guild_id,claim_id))
            if not upd.rowcount:
                raise RuleError('This submission is no longer in review.')

    def _unlinked_stats_recipients(self, guild_id: int, attempt, recipients: list[int]) -> list[int]:
        """Members without a matching visible player card cannot receive stats-card payouts.

        This applies to normal, repeatable, staff-approved, and auto-approved
        USER contracts. UNIT contracts pay the treasury independently.
        """
        if not attempt or not attempt['vision_stats']:
            return []
        try:
            players=self.clean_vision_stats(json.loads(attempt['vision_stats'])).get('players',[])
        except (ValueError,TypeError):
            return []
        if not players:
            return []
        linked={uid for uid,_ in self.attempt_player_links(guild_id,attempt['id'])
                if any(self._player_matches_user(guild_id,attempt['id'],
                       player['game_name'],uid,player.get('column')) for player in players)}
        return [uid for uid in recipients if uid not in linked]

    def _approve_progress(self, guild_id: int, claim_id: int, approver_id: int,
                          *, auto: bool, with_teammates: bool,
                          attempt_id: int | None) -> tuple[bool,str,list[int]]:
        # One atomic transaction for recipient progress, bonuses and payouts.
        with self.atomic():
            row=self.claim_by_id(guild_id,claim_id)
            if not row or row['status']!='PENDING':
                return False,'Submission is no longer awaiting approval.',[]
            offer=self.get_contract(guild_id,row['contract_id'])
            if not offer or offer['status']!='OPEN':
                return False,'Contract closed.',[]
            attempt=self.latest_attempt(guild_id,claim_id)
            if not attempt:
                return False,'This claim has no screenshot record.',[]
            if auto and (attempt['id']!=attempt_id or attempt['status']!='PROCESSING'
                         or attempt['ai_verdict']!='PASS' or attempt['duplicate_note']):
                return False,'Automatic verification did not pass.',[]
            ids=json.loads(attempt['teammate_ids']) if row['recipient_kind']=='USER' and with_teammates else []
            recipients=[row['recipient_id']]+[x for x in ids if x!=row['recipient_id']]
            if len(set(recipients))!=len(recipients):
                return False,'Duplicate participants.',[]
            if row['recipient_kind']=='USER' and self._unlinked_stats_recipients(
                    guild_id,attempt,recipients):
                return False, ('A payout recipient is not linked to a readable player card. '
                               'Link only players from the server or resubmit a clearer image.'),[]
            # Validate the *entire* group before applying any progress.
            for rid in recipients:
                existing=self.get_claim(guild_id,row['contract_id'],row['recipient_kind'],rid)
                if existing and existing['status']=='PENDING' and existing['id']!=claim_id:
                    return False,f'<@{rid}> has another proof pending review.',[]
                if existing and existing['status']=='PAID' and not offer['repeatable']:
                    return False,f'<@{rid}> has already completed this one-time contract.',[]
            for rid in recipients:
                if self.conn.execute('''SELECT 1 FROM contract_progress_proofs
                    WHERE guild_id=? AND contract_id=? AND recipient_kind=?
                      AND recipient_id=? AND attempt_id=?''',
                    (guild_id,offer['id'],row['recipient_kind'],rid,attempt['id'])).fetchone():
                    return False,'This mission already counts toward a contract cycle.',[]
            payouts=[];log=[];required=offer['required_submissions']
            for rid in recipients:
                cycle_row=self.conn.execute('''SELECT * FROM contract_progress
                    WHERE guild_id=? AND contract_id=? AND recipient_kind=?
                    AND recipient_id=? ORDER BY cycle DESC LIMIT 1''',
                    (guild_id,offer['id'],row['recipient_kind'],rid)).fetchone()
                if cycle_row and cycle_row['status']=='PAID':
                    if not offer['repeatable']:
                        return False,'One-time contract already paid.',[]
                    cycle=cycle_row['cycle']+1;count=0;totals={}
                elif cycle_row:
                    cycle=cycle_row['cycle'];count=cycle_row['completed_count']
                    totals=json.loads(cycle_row['accumulated_stats'])
                else:
                    cycle=1;count=0;totals={}
                if not cycle_row or cycle_row['status']=='PAID':
                    self.conn.execute('''INSERT INTO contract_progress(guild_id,contract_id,
                        recipient_kind,recipient_id,cycle) VALUES(?,?,?,?,?)''',
                        (guild_id,offer['id'],row['recipient_kind'],rid,cycle))
                facts=self.screenshot_player_stats(guild_id,attempt,row['recipient_kind'],rid)
                try:
                    self.conn.execute('''INSERT INTO contract_progress_proofs
                        (guild_id,contract_id,recipient_kind,recipient_id,cycle,attempt_id,attributed_stats)
                        VALUES(?,?,?,?,?,?,?)''',
                        (guild_id,offer['id'],row['recipient_kind'],rid,cycle,attempt['id'],json.dumps(facts)))
                except sqlite3.IntegrityError:
                    return False,'This mission has already counted for this contractor.',[]
                coverage=totals.get('_coverage',{})
                if not isinstance(coverage,dict): coverage={}
                for key,value in facts.items():
                    if type(value) is int and key not in ('difficulty_visible',):
                        totals[key]=totals.get(key,0)+value
                        coverage[key]=coverage.get(key,0)+1
                totals['_coverage']=coverage
                if totals.get('shots_fired',0)>0 and 'shots_hit' in totals:
                    totals['accuracy_pct']=round(totals['shots_hit']/totals['shots_fired']*100,1)
                count+=1
                complete=count>=required
                self.conn.execute('''UPDATE contract_progress
                    SET completed_count=?,accumulated_stats=?,status=?,updated_at=CURRENT_TIMESTAMP
                    WHERE guild_id=? AND contract_id=? AND recipient_kind=?
                    AND recipient_id=? AND cycle=?''',
                    (count,json.dumps(totals),'PAID' if complete else 'ACTIVE',
                     guild_id,offer['id'],row['recipient_kind'],rid,cycle))
                target=self.get_claim(guild_id,offer['id'],row['recipient_kind'],rid)
                if target:
                    self.conn.execute('''UPDATE contract_claims SET status=?,
                        submitted_by=?,approved_by=?,review_message_id=NULL,
                        updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                        ('PAID' if complete else 'CLAIMED',row['submitted_by'],approver_id,target['id']))
                    target_id=target['id']
                else:
                    target_id=self.conn.execute('''INSERT INTO contract_claims
                        (guild_id,contract_id,recipient_kind,recipient_id,status,
                         submitted_by,approved_by) VALUES(?,?,?,?,?,?,?)''',
                        (guild_id,offer['id'],row['recipient_kind'],rid,
                         'PAID' if complete else 'CLAIMED',row['submitted_by'],approver_id)).lastrowid
                if complete:
                    bonuses=[]
                    for b in self.objectives(guild_id,offer['id']):
                        if not objective_met(b,totals):
                            continue
                        # For maxima (e.g. 0 deaths), unreadable missions must
                        # NOT be silently treated as zero deaths.
                        if b['comparison']=='LTE' and coverage.get(b['stat_key'],0)<required:
                            continue
                        if b['stat_key']=='accuracy_pct' and (
                            coverage.get('shots_fired',0)<required or
                            coverage.get('shots_hit',0)<required):
                            continue
                        bonuses.append(b)
                    self._change_balance(guild_id,row['recipient_kind'],rid,offer['reward'])
                    self._transaction(guild_id,row['recipient_kind'],rid,offer['reward'],
                        'contract',attempt['id'],approver_id,
                        f'Contract #{offer["id"]} • completion #{cycle} ({required} missions)')
                    for bonus in bonuses:
                        self._change_balance(guild_id,row['recipient_kind'],rid,bonus['reward'])
                        self._transaction(guild_id,row['recipient_kind'],rid,bonus['reward'],
                            'objective',attempt['id']*1000000+bonus['id'],approver_id,
                            f'Contract #{offer["id"]} cycle {cycle}: {bonus["title"]}')
                    payouts.append(rid)
                    log.append(f'{rid}: {required}/{required} • PAID {offer["reward"]+sum(b["reward"] for b in bonuses):,} BC')
                else:
                    log.append(f'{rid}: {count}/{required} missions • awaiting more proof')
            # Existing database status 'PAID' also denotes verified/accepted
            # evidence. Detailed progress vs. money is stored separately.
            self.conn.execute('''UPDATE proof_attempts SET status='PAID'
                WHERE guild_id=? AND id=? AND status IN ('PROCESSING','REVIEW')''',
                (guild_id,attempt['id']))
            return True,'Mission accepted. '+ '; '.join(log)[:1000],payouts

    def approve_group(self, guild_id: int, claim_id: int, approver_id: int,
                      *, auto: bool = False, with_teammates: bool = True,
                      attempt_id: int | None = None) -> tuple[bool, str, list[int]]:
        '''Atomic, idempotent multi-wallet payment. Never part-pay a group.

        Automatic approval requires the checked attempt and an explicit PASS,
        with no duplicate; teammate tags are validated as members by the caller.
        '''
        import json
        preclaim=self.claim_by_id(guild_id,claim_id)
        offer_for_mode=self.get_contract(guild_id,preclaim['contract_id']) if preclaim else None
        if offer_for_mode and (offer_for_mode['required_submissions']>1 or offer_for_mode['repeatable']):
            return self._approve_progress(guild_id,claim_id,approver_id,
                auto=auto,with_teammates=with_teammates,attempt_id=attempt_id)
        with self.atomic():
            row = self.claim_by_id(guild_id,claim_id)
            if row is None or row['status'] != 'PENDING':
                return False,'Submission no longer pending.',[]
            offer = self.get_contract(guild_id,row['contract_id'])
            if not offer or offer['status']!='OPEN':
                return False,'Contract is closed.',[]
            attempt = self.latest_attempt(guild_id,claim_id)
            if auto:
                if (not attempt or attempt['id']!=attempt_id or
                    attempt['status']!='PROCESSING' or
                    attempt['ai_verdict']!='PASS' or attempt['duplicate_note']):
                    return False,'Automatic review requirements not met.',[]
            others = json.loads(attempt['teammate_ids']) if (attempt and with_teammates and
                row['recipient_kind']=='USER') else []
            recipients = [row['recipient_id']] + [x for x in others if x!=row['recipient_id']]
            if len(recipients)!=len(set(recipients)):
                return False,'Duplicate teammates selected.',[]
            if row['recipient_kind']=='USER' and self._unlinked_stats_recipients(
                    guild_id,attempt,recipients):
                return False, ('A payout recipient is not linked to a readable player card. '
                               'Link only players from the server or resubmit a clearer image.'),[]
            for rid in recipients[1:]:
                previous = self.get_claim(guild_id,row['contract_id'],'USER',rid)
                if previous and previous['status'] in ('PENDING','PAID'):
                    return False,f'<@{rid}> already has a pending or paid claim; staff can approve submitter only.',[]
            met_bonuses = self.bonus_results(guild_id, row['contract_id'], attempt)
            bonus_total = sum(x['reward'] for x in met_bonuses)
            for rid in recipients:
                if rid == row['recipient_id']:
                    target_claim_id=claim_id
                    self.conn.execute('''UPDATE contract_claims
                        SET status='PAID',approved_by=?,updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND status='PENDING' ''',(approver_id,target_claim_id))
                else:
                    prev=self.get_claim(guild_id,row['contract_id'],'USER',rid)
                    if prev:
                        target_claim_id=prev['id']
                        self.conn.execute('''UPDATE contract_claims SET status='PAID',
                            evidence=?,submitted_by=?,approved_by=?,review_note=?,
                            updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='CLAIMED' ''',
                            (row['evidence'],row['submitted_by'],approver_id,
                             f'Group payout from review #{claim_id}',target_claim_id))
                    else:
                        cur=self.conn.execute('''INSERT INTO contract_claims(
                            guild_id,contract_id,recipient_kind,recipient_id,status,evidence,
                            submitted_by,approved_by,review_note)
                            VALUES(?,?, 'USER',?,'PAID',?,?,?,?)''',
                            (guild_id,row['contract_id'],rid,row['evidence'],
                             row['submitted_by'],approver_id,f'Group payout from review #{claim_id}'))
                        target_claim_id=cur.lastrowid
                self._change_balance(guild_id,row['recipient_kind'],rid,offer['reward'])
                self._transaction(guild_id,row['recipient_kind'],rid,offer['reward'],
                                  'contract',target_claim_id,approver_id,
                                  f"Contract #{offer['id']}: {offer['title']}")
                player_bonuses = (self.bonus_results(guild_id,row['contract_id'],attempt,
                    recipient_kind=row['recipient_kind'],recipient_id=rid)
                    if attempt and attempt['vision_stats'] and 'players' in json.loads(attempt['vision_stats'])
                    else met_bonuses)
                for bonus in player_bonuses:
                    self._change_balance(guild_id,row['recipient_kind'],rid,bonus['reward'])
                    self._transaction(guild_id,row['recipient_kind'],rid,bonus['reward'],
                        'objective',bonus['id'],approver_id,
                        f"Black Market bonus • Contract #{offer['id']}: {bonus['title']}")
            if attempt:
                self.conn.execute('''UPDATE proof_attempts SET status='PAID'
                    WHERE guild_id=? AND id=? ''',(guild_id,attempt['id']))
            text = (f'Paid {len(recipients)} recipient(s) '
                    f'{offer["reward"]:,} Blacksite Credits base each.')
            if attempt and attempt['vision_stats'] and 'players' in json.loads(attempt['vision_stats']):
                text += ' Per-player bonuses awarded only for matched in-game names.'
            elif met_bonuses:
                text += f' + {bonus_total:,} bonus credits each ({len(met_bonuses)} objective(s)).'
            return True,text,recipients

    def _change_balance(self, guild_id: int, kind: str, recipient_id: int, delta: int) -> None:
        table, idcol = ('wallets','user_id') if kind == 'USER' else ('unit_wallets','role_id')
        self.conn.execute(f"INSERT OR IGNORE INTO {table}(guild_id,{idcol},balance) VALUES(?,?,0)",
                          (guild_id,recipient_id))
        upd = self.conn.execute(f"""
            UPDATE {table} SET balance=balance+? WHERE guild_id=? AND {idcol}=?
              AND balance+?>=0
        """,(delta,guild_id,recipient_id,delta))
        if upd.rowcount != 1:
            raise RuleError('Insufficient Blacksite Credits.')

    def _transaction(self, guild_id: int, kind: str, recipient_id: int, amount: int,
                     source: str, reference_id: int | None, actor_id: int, note: str) -> None:
        self.conn.execute("""
            INSERT INTO transactions(guild_id,recipient_kind,recipient_id,amount,
                source,reference_id,actor_id,note)
            VALUES(?,?,?,?,?,?,?,?)
        """, (guild_id,kind,recipient_id,amount,source,reference_id,actor_id,note))

    def adjust(self, guild_id: int, kind: str, recipient_id: int, amount: int,
               actor_id: int, reason: str) -> int:
        if kind not in ('USER','UNIT') or amount == 0:
            raise RuleError('Invalid recipient or adjustment.')
        if kind == 'UNIT' and not self.get_unit(guild_id,recipient_id):
            raise RuleError('Register the unit first.')
        with self.atomic():
            self._change_balance(guild_id,kind,recipient_id,amount)
            self._transaction(guild_id,kind,recipient_id,amount,'manual',None,
                              actor_id,reason)
        return self.balance(guild_id,kind,recipient_id)

    def balance(self, guild_id: int, kind: str, recipient_id: int) -> int:
        if kind not in ('USER','UNIT'):
            raise ValueError('Invalid wallet kind')
        table, idcol = ('wallets','user_id') if kind=='USER' else ('unit_wallets','role_id')
        r = self.conn.execute(f"SELECT balance FROM {table} WHERE guild_id=? AND {idcol}=?",
                              (guild_id,recipient_id)).fetchone()
        return r['balance'] if r else 0

    def history(self, guild_id: int, kind: str, recipient_id: int, limit: int = 10):
        return self.conn.execute("""
            SELECT * FROM transactions WHERE guild_id=? AND recipient_kind=?
              AND recipient_id=? ORDER BY id DESC LIMIT ?
        """, (guild_id,kind,recipient_id,limit)).fetchall()

    def leaderboard(self, guild_id: int, kind: str = 'USER'):
        table, idcol = ('wallets','user_id') if kind=='USER' else ('unit_wallets','role_id')
        return self.conn.execute(f"""
            SELECT {idcol} AS recipient_id,balance FROM {table} WHERE guild_id=?
            ORDER BY balance DESC,{idcol} ASC LIMIT 10
        """, (guild_id,)).fetchall()

    @staticmethod
    def _check_shop_kind(shop_kind: str) -> None:
        if shop_kind not in ('USER', 'UNIT'):
            raise RuleError('Unknown shop type.')

    def add_item(self, guild_id: int, name: str, description: str,
                 price: int, stock: int | None, actor_id: int,
                 shop_kind: str = 'USER') -> int:
        self._check_shop_kind(shop_kind)
        if price < 1 or (stock is not None and stock < 0):
            raise RuleError('Price must be positive, stock nonnegative or unlimited.')
        with self.atomic():
            cur = self.conn.execute("""
                INSERT INTO shop_items(guild_id,shop_kind,name,description,price,stock,created_by)
                VALUES(?,?,?,?,?,?,?)
            """,(guild_id,shop_kind,name,description,price,stock,actor_id))
            return cur.lastrowid

    def item(self, guild_id: int, item_id: int, shop_kind: str = 'USER'):
        self._check_shop_kind(shop_kind)
        return self.conn.execute("""
            SELECT * FROM shop_items WHERE guild_id=? AND id=? AND shop_kind=?
        """, (guild_id,item_id,shop_kind)).fetchone()

    def items(self, guild_id: int, limit: int = 25, shop_kind: str = 'USER'):
        self._check_shop_kind(shop_kind)
        return self.conn.execute("""
            SELECT * FROM shop_items WHERE guild_id=? AND shop_kind=? AND active=1
            ORDER BY id ASC LIMIT ?
        """, (guild_id,shop_kind,limit)).fetchall()

    def update_item(self, guild_id: int, item_id: int, *, name: str | None = None,
                    description: str | None = None, price: int | None = None,
                    shop_kind: str = 'USER') -> bool:
        with self.atomic():
            old=self.item(guild_id,item_id,shop_kind)
            if not old or not old['active']:
                return False
            self.conn.execute("""
                UPDATE shop_items SET name=?,description=?,price=?
                WHERE guild_id=? AND id=? AND shop_kind=?
            """, (name if name is not None else old['name'],
                  description if description is not None else old['description'],
                  price if price is not None else old['price'],guild_id,item_id,shop_kind))
            return True

    def remove_item(self, guild_id: int, item_id: int, shop_kind: str = 'USER') -> bool:
        with self.atomic():
            cur=self.conn.execute("""
                UPDATE shop_items SET active=0
                WHERE guild_id=? AND id=? AND shop_kind=? AND active=1
            """,(guild_id,item_id,shop_kind))
            return cur.rowcount==1

    def set_stock(self, guild_id: int, item_id: int, stock: int | None,
                  shop_kind: str = 'USER') -> bool:
        self._check_shop_kind(shop_kind)
        if stock is not None and stock<0:
            raise RuleError('Stock cannot be negative.')
        with self.atomic():
            cur=self.conn.execute("""
                UPDATE shop_items SET stock=? WHERE guild_id=? AND id=?
                  AND shop_kind=? AND active=1
            """,(stock,guild_id,item_id,shop_kind))
            return cur.rowcount==1

    def change_stock(self, guild_id: int, item_id: int, delta: int,
                     shop_kind: str = 'USER') -> int:
        if delta==0:
            raise RuleError('Stock change cannot be zero.')
        with self.atomic():
            r=self.item(guild_id,item_id,shop_kind)
            if not r or not r['active']:
                raise RuleError('Shop item not found.')
            if r['stock'] is None:
                raise RuleError('Unlimited stock: set a finite number first.')
            new=r['stock']+delta
            if new<0:
                raise RuleError('Not enough inventory to subtract that amount.')
            self.conn.execute("""
                UPDATE shop_items SET stock=? WHERE id=? AND guild_id=? AND shop_kind=?
            """,(new,item_id,guild_id,shop_kind))
            return new

    def purchase(self, guild_id: int, item_id: int, user_id: int,
                 quantity: int, *, shop_kind: str = 'USER',
                 recipient_id: int | None = None) -> tuple[int,int]:
        self._check_shop_kind(shop_kind)
        if not 1<=quantity<=10:
            raise RuleError('Quantity must be from 1 to 10.')
        if shop_kind == 'UNIT':
            if recipient_id is None or not self.get_unit(guild_id,recipient_id):
                raise RuleError('A registered unit treasury is required for unit purchases.')
        elif recipient_id is not None and recipient_id != user_id:
            raise RuleError('Personal purchases cannot charge another account.')
        billed_id = user_id if shop_kind=='USER' else recipient_id
        with self.atomic():
            itm=self.item(guild_id,item_id,shop_kind)
            if not itm or not itm['active']:
                raise RuleError('Item not available.')
            if itm['stock'] is not None and itm['stock']<quantity:
                raise RuleError('Not enough stock left.')
            cost=itm['price']*quantity
            if cost>10_000_000_000:
                raise RuleError('Purchase exceeds the transaction limit.')
            self._change_balance(guild_id,shop_kind,billed_id,-cost)
            if itm['stock'] is not None:
                self.conn.execute("""
                    UPDATE shop_items SET stock=stock-?
                    WHERE guild_id=? AND id=? AND shop_kind=?
                """,(quantity,guild_id,item_id,shop_kind))
            cur=self.conn.execute("""
                INSERT INTO shop_purchases(guild_id,item_id,user_id,recipient_kind,
                    recipient_id,quantity,total_price)
                VALUES(?,?,?,?,?,?,?)
            """,(guild_id,item_id,user_id,shop_kind,billed_id,quantity,cost))
            self._transaction(guild_id,shop_kind,billed_id,-cost,'shop',cur.lastrowid,
                              user_id,f"Purchase {quantity}x {itm['name']}")
            return cur.lastrowid,cost

    def purchase_by_id(self, guild_id: int, purchase_id: int):
        return self.conn.execute("""
            SELECT p.*,i.name AS item_name,i.shop_kind FROM shop_purchases p
            JOIN shop_items i ON i.id=p.item_id
            WHERE p.guild_id=? AND p.id=? AND i.guild_id=p.guild_id
        """,(guild_id,purchase_id)).fetchone()

    def pending_purchases(self,guild_id: int, limit:int=20,
                          shop_kind: str = 'USER'):
        self._check_shop_kind(shop_kind)
        return self.conn.execute("""
            SELECT p.*,i.name AS item_name,i.shop_kind FROM shop_purchases p
            JOIN shop_items i ON i.id=p.item_id
            WHERE p.guild_id=? AND i.guild_id=p.guild_id
                AND i.shop_kind=? AND p.status='PENDING'
            ORDER BY p.id ASC LIMIT ?
        """,(guild_id,shop_kind,limit)).fetchall()

    def fulfill(self,guild_id:int,purchase_id:int,actor_id:int,
                shop_kind: str = 'USER') -> bool:
        self._check_shop_kind(shop_kind)
        with self.atomic():
            cur=self.conn.execute("""
                UPDATE shop_purchases SET status='FULFILLED',fulfilled_by=?,
                    fulfilled_at=CURRENT_TIMESTAMP
                WHERE guild_id=? AND id=? AND status='PENDING' AND item_id IN (
                    SELECT id FROM shop_items WHERE guild_id=? AND shop_kind=?
                )
            """,(actor_id,guild_id,purchase_id,guild_id,shop_kind))
            return cur.rowcount==1


# Compatibility with the older import name is intentional.
BountyStorage = LedgerStorage
