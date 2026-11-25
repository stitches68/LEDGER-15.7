"""Adopt a Contractor and role-driven unit roster regression tests."""
import tempfile
import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import LedgerStorage,RuleError
from mentorship import MentorshipStore

G=10001

class MentorshipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'ledger.sqlite3'
        self.db=LedgerStorage(self.path)
        self.s=MentorshipStore(self.db)
        self.db.register_unit(G,901,'ANGEL')
        self.db.register_unit(G,902,'HELLHOUND')
        self.s.configure(G,channel_id=402,reward=600,required_sessions=2,
                         officer_role_ids=[301,302])
        self.s.set_lead_roles(G,901,[201])

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def request(self,uid=1,unit=901):
        return self.s.request(G,uid,unit,'new player','Need help learning the ropes','Weekends PST')

    def train(self,case=0,mentor=50,trainee=1):
        case=case or self.request(trainee)
        self.s.assign(G,case,mentor,301)
        self.s.accept(G,case,mentor)
        sids=[]
        for j in range(2):
            sid=self.s.log_session(G,case,mentor,f'Practice mission #{j} with strategic training')
            self.s.confirm_session(G,sid,trainee)
            sids.append(sid)
        self.s.signoff(G,case,trainee)
        return case,sids

    def test_member_join_role_change_and_leave(self):
        self.db.link_unit_role(G,901,111,actor_id=301)
        self.assertEqual(self.s.sync_member(G,42,[111]),[901])
        self.assertEqual(self.s.roster(G,901),[42])
        self.assertEqual(self.s.sync_member(G,42,[902]),[902])
        self.assertEqual(self.s.roster(G,901),[])
        self.assertEqual(self.s.roster(G,902),[42])
        self.s.remove_member(G,42)
        self.assertEqual(self.s.roster(G,902),[])

    def test_bulk_roster_rebuild_reconciles_leavers(self):
        self.s.sync_member(G,5,[901])
        self.assertEqual(self.s.reconcile_roster(G,[(7,[901,902]),(8,[])]),2)
        self.assertEqual(self.s.roster(G,901),[7])
        self.assertEqual(self.s.roster(G,902),[7])

    def test_officer_and_lead_permissions(self):
        self.assertTrue(self.s.allowed_manager(G,[301],None))
        self.assertTrue(self.s.allowed_manager(G,[201],901))
        self.assertFalse(self.s.allowed_manager(G,[201],902))
        self.assertFalse(self.s.allowed_manager(G,[999],901))
        self.assertEqual(self.s.lead_roles(G,901),[201])

    def test_one_active_request_per_trainee(self):
        rid=self.request()
        with self.assertRaises(RuleError):self.request()
        self.s.cancel(G,rid,1)
        self.assertIsInstance(self.request(),int)

    def test_member_cannot_mentor_themself(self):
        rid=self.request()
        with self.assertRaises(RuleError):self.s.assign(G,rid,1,301)
        self.assertEqual(self.s.request_by_id(G,rid)['status'],'OPEN')

    def test_mentor_acceptance_required_before_sessions(self):
        rid=self.request()
        self.s.assign(G,rid,50,301)
        with self.assertRaises(RuleError):self.s.log_session(G,rid,50,'A training lesson with notes')
        with self.assertRaises(RuleError):self.s.accept(G,rid,77)
        self.s.accept(G,rid,50)
        sid=self.s.log_session(G,rid,50,'A training lesson with notes')
        with self.assertRaises(RuleError):self.s.log_session(G,rid,50,'Another training lesson')
        with self.assertRaises(RuleError):self.s.confirm_session(G,sid,99)
        self.assertEqual(self.s.confirm_session(G,sid,1),rid)
        with self.assertRaises(RuleError):self.s.confirm_session(G,sid,1)

    def test_no_early_payout_and_milestones(self):
        rid=self.request()
        self.s.assign(G,rid,50,301)
        self.s.accept(G,rid,50)
        s1=self.s.log_session(G,rid,50,'First beginner training exercise')
        self.s.confirm_session(G,s1,1)
        with self.assertRaises(RuleError):self.s.signoff(G,rid,1)
        with self.assertRaises(RuleError):self.s.payout(G,rid,301)
        self.assertEqual(self.db.balance(G,'USER',50),0)

    def test_atomic_payment_and_one_time_only(self):
        rid,sids=self.train()
        with self.assertRaises(RuleError):self.s.payout(G,rid,50)
        with self.assertRaises(RuleError):self.s.payout(G,rid,1)
        mentor,trainee,reward=self.s.payout(G,rid,301)
        self.assertEqual((mentor,trainee,reward),(50,1,600))
        self.assertEqual(self.db.balance(G,'USER',50),600)
        self.assertEqual(self.db.balance(G,'USER',1),0)
        self.assertEqual(self.db.earnings(G,'USER',50)[0],600)
        self.assertEqual(len(self.db.history(G,'USER',50)),1)
        with self.assertRaises(RuleError):self.s.payout(G,rid,301)
        self.assertEqual(self.db.balance(G,'USER',50),600)

    def test_mentor_profile_shows_verified_training(self):
        rid,_=self.train()
        self.s.payout(G,rid,301)
        instructor=self.s.profile_stats(G,50)
        self.assertEqual(instructor['trained_contractors'],1)
        self.assertEqual(instructor['mentorship_earned'],600)
        self.assertEqual(self.s.profile_stats(G,1)['graduated_training'],1)

    def test_completed_request_does_not_repeat_or_change_rewards(self):
        rid=self.request()
        self.s.configure(G,reward=9000,required_sessions=5)
        row=self.s.request_by_id(G,rid)
        self.assertEqual((row['reward'],row['required_sessions']),(600,2))
        self.train(rid)
        self.assertEqual(self.s.payout(G,rid,301)[2],600)
        self.assertEqual(self.s.request_by_id(G,rid)['status'],'PAID')

    def test_cancellation_prevents_training_payout(self):
        rid=self.request()
        self.s.assign(G,rid,50,301)
        with self.assertRaises(RuleError):self.s.cancel(G,rid,999)
        self.s.cancel(G,rid,1)
        with self.assertRaises(RuleError):self.s.accept(G,rid,50)
        self.assertEqual(self.db.balance(G,'USER',50),0)

    def test_mentor_can_decline_before_sessions(self):
        rid=self.request()
        self.s.assign(G,rid,50,301)
        with self.assertRaises(RuleError):self.s.unassign(G,rid,99,mentor_declining=True)
        self.s.unassign(G,rid,50,mentor_declining=True)
        self.assertEqual(self.s.request_by_id(G,rid)['status'],'OPEN')
        self.s.assign(G,rid,60,301)
        self.assertEqual(self.s.request_by_id(G,rid)['mentor_id'],60)

    def test_officer_can_reassign_unconfirmed_sessions(self):
        rid=self.request()
        self.s.assign(G,rid,50,301)
        self.s.accept(G,rid,50)
        self.s.log_session(G,rid,50,'Practice dive awaiting trainee approval')
        self.s.unassign(G,rid,301)
        self.assertEqual(len(self.s.sessions(G,rid)),0)
        self.s.assign(G,rid,60,301)
        self.assertEqual(self.s.request_by_id(G,rid)['mentor_id'],60)

    def test_cannot_unassign_confirmed_training(self):
        rid=self.request()
        self.s.assign(G,rid,50,301)
        self.s.accept(G,rid,50)
        sid=self.s.log_session(G,rid,50,'Practice mission with trainee participation')
        self.s.confirm_session(G,sid,1)
        with self.assertRaises(RuleError):self.s.unassign(G,rid,301)
        self.assertEqual(self.s.request_by_id(G,rid)['mentor_id'],50)

    def test_cross_guild_separation(self):
        rid=self.request()
        self.s.configure(12345,channel_id=600,reward=500)
        self.assertIsNone(self.s.request_by_id(12345,rid))
        self.assertEqual(self.s.roster(12345,901),[])
        self.assertEqual(self.s.officer_roles(12345),[])

    def test_old_balances_survive_mentorship_upgrade(self):
        self.db.adjust(G,'USER',300,1500,301,'Existing contractor earnings')
        self.db.conn.close()
        self.db=LedgerStorage(self.path)
        self.s=MentorshipStore(self.db)
        self.assertEqual(self.db.balance(G,'USER',300),1500)
        self.assertEqual(self.s.settings(G)['reward'],600)

if __name__=='__main__':unittest.main()
