"""Adopt a Contractor: role-based rosters and mentor contracts.

Business rules and payments are pure SQLite and unit-testable without Discord.
All balances use LEDGER's existing wallet/transaction ledger.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from storage import RuleError


def _role_ids(value):
    return sorted({int(v) for v in value if int(v) > 0})


class MentorshipStore:
    def __init__(self, ledger):
        self.db = ledger
        self.conn = ledger.conn
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS unit_roster (
            guild_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(guild_id, unit_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS ix_roster_user ON unit_roster(guild_id,user_id);
        CREATE TABLE IF NOT EXISTS mentor_settings (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            reward INTEGER NOT NULL DEFAULT 750 CHECK(reward BETWEEN 1 AND 10000000),
            required_sessions INTEGER NOT NULL DEFAULT 2 CHECK(required_sessions BETWEEN 1 AND 10),
            officer_role_ids TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS mentor_unit_leads (
            guild_id INTEGER NOT NULL,
            unit_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY(guild_id,unit_id,role_id)
        );
        CREATE TABLE IF NOT EXISTS mentor_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            trainee_id INTEGER NOT NULL,
            unit_id INTEGER,
            experience TEXT NOT NULL,
            goal TEXT NOT NULL,
            availability TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN'
                CHECK(status IN ('OPEN','ASSIGNED','READY','PAID','CANCELLED')),
            mentor_id INTEGER,
            mentor_accepted INTEGER NOT NULL DEFAULT 0,
            trainee_signed INTEGER NOT NULL DEFAULT 0,
            reward INTEGER NOT NULL CHECK(reward>0),
            required_sessions INTEGER NOT NULL CHECK(required_sessions>0),
            approved_by INTEGER,
            channel_id INTEGER,
            message_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_mentee
            ON mentor_requests(guild_id,trainee_id)
            WHERE status IN ('OPEN','ASSIGNED','READY');
        CREATE INDEX IF NOT EXISTS ix_mentor_queue ON mentor_requests(guild_id,status,created_at);
        CREATE TABLE IF NOT EXISTS mentor_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            request_id INTEGER NOT NULL,
            mentor_id INTEGER NOT NULL,
            note TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING','CONFIRMED')),
            confirmed_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            confirmed_at TEXT,
            FOREIGN KEY(request_id) REFERENCES mentor_requests(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_mentor_sessions_request ON mentor_sessions(guild_id,request_id);
        ''')
        self.conn.commit()

    def settings(self, guild_id):
        row = self.conn.execute('SELECT * FROM mentor_settings WHERE guild_id=?',(guild_id,)).fetchone()
        return dict(row) if row else dict(guild_id=guild_id,channel_id=None,reward=750,required_sessions=2,officer_role_ids='[]')

    def configure(self, guild_id, *, channel_id=None, reward=None, required_sessions=None, officer_role_ids=None):
        if reward is not None and not 1 <= reward <= 10_000_000:
            raise RuleError('The mentor contract reward must be 1 to 10,000,000 Blacksite Credits.')
        if required_sessions is not None and not 1 <= required_sessions <= 10:
            raise RuleError('Required training sessions must be between 1 and 10.')
        if officer_role_ids is not None:
            officer_role_ids = _role_ids(officer_role_ids)
            if len(officer_role_ids) > 25:
                raise RuleError('Select at most 25 officer roles.')
        with self.db.atomic():
            self.conn.execute('INSERT OR IGNORE INTO mentor_settings(guild_id) VALUES(?)',(guild_id,))
            updates=[]; args=[]
            for col,val in [('channel_id',channel_id),('reward',reward),('required_sessions',required_sessions),
                             ('officer_role_ids',json.dumps(officer_role_ids) if officer_role_ids is not None else None)]:
                if val is not None:
                    updates.append(f'{col}=?'); args.append(val)
            if updates:
                self.conn.execute(f'UPDATE mentor_settings SET {", ".join(updates)} WHERE guild_id=?',(*args,guild_id))
        return self.settings(guild_id)

    def set_lead_roles(self, guild_id, unit_id, role_ids):
        if not self.db.get_unit(guild_id,unit_id):
            raise RuleError('Register the unit first using /unit setup.')
        role_ids=_role_ids(role_ids)
        if len(role_ids)>25: raise RuleError('Select at most 25 lead roles.')
        with self.db.atomic():
            self.conn.execute('DELETE FROM mentor_unit_leads WHERE guild_id=? AND unit_id=?',(guild_id,unit_id))
            self.conn.executemany('INSERT INTO mentor_unit_leads(guild_id,unit_id,role_id) VALUES(?,?,?)',
                                  ((guild_id,unit_id,rid) for rid in role_ids))

    def lead_roles(self, guild_id, unit_id):
        return [r['role_id'] for r in self.conn.execute(
            'SELECT role_id FROM mentor_unit_leads WHERE guild_id=? AND unit_id=?',(guild_id,unit_id))]

    def officer_roles(self,guild_id):
        return json.loads(self.settings(guild_id)['officer_role_ids'])

    def allowed_manager(self, guild_id, role_ids, unit_id):
        roles=set(role_ids)
        return bool(roles.intersection(self.officer_roles(guild_id)) or
                    (unit_id is not None and roles.intersection(self.lead_roles(guild_id,unit_id))))

    def sync_member(self, guild_id, member_id, member_role_ids):
        """Mirror authoritative Discord roles without granting/removing any Discord roles."""
        units={r['role_id'] for r in self.db.member_units(guild_id,member_role_ids)}
        with self.db.atomic():
            prior={r['unit_id'] for r in self.conn.execute(
                'SELECT unit_id FROM unit_roster WHERE guild_id=? AND user_id=?',(guild_id,member_id))}
            for uid in prior-units:
                self.conn.execute('DELETE FROM unit_roster WHERE guild_id=? AND user_id=? AND unit_id=?',
                                  (guild_id,member_id,uid))
            for uid in units-prior:
                self.conn.execute('INSERT OR IGNORE INTO unit_roster(guild_id,user_id,unit_id) VALUES(?,?,?)',
                                  (guild_id,member_id,uid))
        return sorted(units)

    def remove_member(self,guild_id,member_id):
        with self.db.atomic():
            self.conn.execute('DELETE FROM unit_roster WHERE guild_id=? AND user_id=?',(guild_id,member_id))

    def reconcile_roster(self,guild_id,members):
        """Full sync only with a complete Discord member enumeration."""
        members=list(members)
        expected=set()
        for member_id, role_ids in members:
            expected.update((uid,member_id) for uid in
                            (r['role_id'] for r in self.db.member_units(guild_id,role_ids)))
        with self.db.atomic():
            self.conn.execute('DELETE FROM unit_roster WHERE guild_id=?',(guild_id,))
            self.conn.executemany('INSERT INTO unit_roster(guild_id,unit_id,user_id) VALUES(?,?,?)',
                                  ((guild_id,uid,user_id) for uid,user_id in expected))
        return len(expected)

    def roster(self,guild_id,unit_id,limit=50):
        return [r['user_id'] for r in self.conn.execute('''
            SELECT user_id FROM unit_roster WHERE guild_id=? AND unit_id=?
            ORDER BY user_id LIMIT ?''',(guild_id,unit_id,limit))]

    def request(self, guild_id, trainee_id, unit_id, experience, goal, availability):
        if unit_id is not None and not self.db.get_unit(guild_id,unit_id):
            raise RuleError('That unit is no longer registered.')
        experience=experience.strip(); goal=goal.strip(); availability=availability.strip()
        if not 2 <= len(experience) <= 120 or not 5 <= len(goal) <= 600 or not 2 <= len(availability) <= 250:
            raise RuleError('Please describe your experience, what you want to learn, and availability.')
        cfg=self.settings(guild_id)
        if not cfg['channel_id']:
            raise RuleError('Mentor requests are not configured yet. Ask staff to run /mentor setup.')
        with self.db.atomic():
            try:
                cur=self.conn.execute('''INSERT INTO mentor_requests(
                    guild_id,trainee_id,unit_id,experience,goal,availability,reward,required_sessions)
                    VALUES(?,?,?,?,?,?,?,?)''',(guild_id,trainee_id,unit_id,experience,goal,availability,
                                              cfg['reward'],cfg['required_sessions']))
            except Exception as exc:
                if 'UNIQUE constraint failed' in str(exc):
                    raise RuleError('You already have an active mentor request. Use /mentor status.') from exc
                raise
        return cur.lastrowid

    def request_by_id(self,guild_id,request_id):
        return self.conn.execute('SELECT * FROM mentor_requests WHERE guild_id=? AND id=?',
                                 (guild_id,request_id)).fetchone()

    def active_for_trainee(self,guild_id,trainee_id):
        return self.conn.execute('''SELECT * FROM mentor_requests WHERE guild_id=? AND trainee_id=?
            AND status IN ('OPEN','ASSIGNED','READY') ORDER BY id DESC LIMIT 1''',(guild_id,trainee_id)).fetchone()

    def queue(self,guild_id,limit=15):
        return self.conn.execute('''SELECT * FROM mentor_requests WHERE guild_id=?
            AND status IN ('OPEN','ASSIGNED','READY') ORDER BY id DESC LIMIT ?''',(guild_id,limit)).fetchall()

    def attach_message(self,guild_id,request_id,channel_id,message_id):
        with self.db.atomic():
            self.conn.execute('''UPDATE mentor_requests SET channel_id=?,message_id=?
                WHERE guild_id=? AND id=?''',(channel_id,message_id,guild_id,request_id))

    def assign(self,guild_id,request_id,mentor_id,actor_id):
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['status']!='OPEN':
                raise RuleError('This request is not open for assignment.')
            if mentor_id==row['trainee_id']:
                raise RuleError('A contractor cannot mentor themselves.')
            self.conn.execute('''UPDATE mentor_requests SET mentor_id=?,status='ASSIGNED',
                mentor_accepted=0,updated_at=CURRENT_TIMESTAMP WHERE guild_id=? AND id=?''',
                (mentor_id,guild_id,request_id))

    def unassign(self,guild_id,request_id,actor_id,*,mentor_declining=False):
        """Unassign before confirmed sessions; avoid mixing mentors' work."""
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['status']!='ASSIGNED':
                raise RuleError('This case is not currently assigned.')
            if mentor_declining and actor_id!=row['mentor_id']:
                raise RuleError('Only the currently assigned mentor may decline this role.')
            if self.confirmed_count(guild_id,request_id):
                raise RuleError('This mentorship has confirmed training. Ask an officer to cancel the case instead.')
            self.conn.execute('DELETE FROM mentor_sessions WHERE guild_id=? AND request_id=?',
                              (guild_id,request_id))
            self.conn.execute("""UPDATE mentor_requests SET status='OPEN',mentor_id=NULL,
                mentor_accepted=0,trainee_signed=0,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (request_id,))

    def accept(self,guild_id,request_id,mentor_id):
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['status']!='ASSIGNED' or row['mentor_id']!=mentor_id:
                raise RuleError('You are not the assigned mentor for this request.')
            self.conn.execute('UPDATE mentor_requests SET mentor_accepted=1 WHERE id=?',(request_id,))

    def log_session(self,guild_id,request_id,mentor_id,note):
        note=note.strip()
        if not 10 <= len(note) <= 700:
            raise RuleError('Describe the training session (10–700 characters).')
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['status']!='ASSIGNED' or row['mentor_id']!=mentor_id or not row['mentor_accepted']:
                raise RuleError('Only the mentor who accepted this assignment may log sessions.')
            pending=self.conn.execute('''SELECT 1 FROM mentor_sessions
                WHERE guild_id=? AND request_id=? AND status='PENDING' LIMIT 1''',(guild_id,request_id)).fetchone()
            if pending:
                raise RuleError('Your trainee must confirm the previous session before you log another.')
            confirmed=self.confirmed_count(guild_id,request_id)
            if confirmed >= row['required_sessions']:
                raise RuleError('All required sessions are already completed.')
            cur=self.conn.execute('''INSERT INTO mentor_sessions(guild_id,request_id,mentor_id,note)
                VALUES(?,?,?,?)''',(guild_id,request_id,mentor_id,note))
        return cur.lastrowid

    def sessions(self,guild_id,request_id):
        return self.conn.execute('''SELECT * FROM mentor_sessions WHERE guild_id=? AND request_id=?
            ORDER BY id''',(guild_id,request_id)).fetchall()

    def confirmed_count(self,guild_id,request_id):
        row=self.conn.execute('''SELECT COUNT(*) AS n FROM mentor_sessions WHERE guild_id=?
            AND request_id=? AND status='CONFIRMED' ''',(guild_id,request_id)).fetchone()
        return row['n']

    def confirm_session(self,guild_id,session_id,trainee_id):
        with self.db.atomic():
            session=self.conn.execute('''SELECT s.*, r.trainee_id, r.status AS request_status
                FROM mentor_sessions s JOIN mentor_requests r ON s.request_id=r.id
                WHERE s.guild_id=? AND s.id=?''',(guild_id,session_id)).fetchone()
            if not session or session['trainee_id']!=trainee_id or session['request_status']!='ASSIGNED':
                raise RuleError('Only the assigned trainee can confirm this training session.')
            if session['status']!='PENDING':
                raise RuleError('This session has already been confirmed.')
            self.conn.execute('''UPDATE mentor_sessions SET status='CONFIRMED',confirmed_by=?,
                confirmed_at=CURRENT_TIMESTAMP WHERE id=?''',(trainee_id,session_id))
        return session['request_id']

    def signoff(self,guild_id,request_id,trainee_id):
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['trainee_id']!=trainee_id or row['status']!='ASSIGNED':
                raise RuleError('Only the trainee may sign off on their active mentorship.')
            if not row['mentor_accepted'] or self.confirmed_count(guild_id,request_id)<row['required_sessions']:
                raise RuleError(f'Confirm {row["required_sessions"]} training sessions before signing off.')
            self.conn.execute('''UPDATE mentor_requests SET trainee_signed=1,status='READY',
                updated_at=CURRENT_TIMESTAMP WHERE id=?''',(request_id,))

    def payout(self,guild_id,request_id,approver_id):
        """One atomic final payout; approval is a second person's sign-off."""
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['status']!='READY' or not row['mentor_accepted'] or not row['trainee_signed']:
                raise RuleError('Training milestones and trainee sign-off must be completed first.')
            if row['mentor_id']==approver_id or row['trainee_id']==approver_id:
                raise RuleError('A separate authorized officer must approve this mentorship payout.')
            if self.confirmed_count(guild_id,request_id)<row['required_sessions']:
                raise RuleError('Not enough trainee-confirmed sessions.')
            updated=self.conn.execute('''UPDATE mentor_requests SET status='PAID',approved_by=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=? AND status='READY' ''',
                (approver_id,request_id,guild_id))
            if updated.rowcount!=1:
                raise RuleError('This mentor contract has already been completed.')
            self.db._change_balance(guild_id,'USER',row['mentor_id'],row['reward'])
            self.db._transaction(guild_id,'USER',row['mentor_id'],row['reward'],
                                 'mentorship',request_id,approver_id,
                                 f'Adopt a Contractor — training case #{request_id}')
        return row['mentor_id'],row['trainee_id'],row['reward']

    def profile_stats(self,guild_id,member_id):
        taught=self.conn.execute("""SELECT COUNT(*) AS n,COALESCE(SUM(reward),0) AS paid
            FROM mentor_requests WHERE guild_id=? AND mentor_id=? AND status='PAID'""",
            (guild_id,member_id)).fetchone()
        graduated=self.conn.execute("""SELECT COUNT(*) AS n FROM mentor_requests
            WHERE guild_id=? AND trainee_id=? AND status='PAID'""",
            (guild_id,member_id)).fetchone()
        return {'trained_contractors':taught['n'],'mentorship_earned':taught['paid'],
                'graduated_training':graduated['n']}

    def cancel(self,guild_id,request_id,actor_id,officer=False):
        with self.db.atomic():
            row=self.request_by_id(guild_id,request_id)
            if not row or row['status'] not in ('OPEN','ASSIGNED','READY'):
                raise RuleError('This request is no longer open.')
            if not officer and actor_id!=row['trainee_id']:
                raise RuleError('Only the trainee or an authorized officer can close this request.')
            self.conn.execute('''UPDATE mentor_requests SET status='CANCELLED',
                updated_at=CURRENT_TIMESTAMP WHERE id=?''',(request_id,))
