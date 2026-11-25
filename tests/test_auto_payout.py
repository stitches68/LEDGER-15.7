import sqlite3
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import LedgerStorage


class AutomatedProofTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.path=Path(self.t.name)/'ledger.sqlite3'
        self.db=LedgerStorage(self.path)
        self.c=self.db.create_contract(55,'Recover squad','Extract crew and survive',250,999,77)

    def tearDown(self):
        self.db.conn.close()
        self.t.cleanup()

    def prepare(self, user=101, contract=None, teammates=None, hash_='aaa',
                pixels=None, dhash='1122334455667788', guild=55, kind='USER'):
        cid=contract or self.c
        ok,_,claim=self.db.submit(guild,cid,kind,user,999, '')
        self.assertTrue(ok)
        attempt,duplicate=self.db.record_attempt(guild,cid,claim,999,hash_,dhash,
            pixels or bytes([77])*3072,'Genesis Prime',7,teammates or [])
        self.db.set_attempt_verdict(guild,attempt,'PASS','Verified objective')
        self.db.save_analysis(guild,claim,'PASS','Verified objective')
        return claim,attempt,duplicate

    def test_auto_pays_all_individuals_once(self):
        claim,attempt,dup=self.prepare(teammates=[102,103])
        self.assertEqual(dup,'')
        ok,note,ids=self.db.approve_group(55,claim,bot_id:=500,auto=True,attempt_id=attempt)
        self.assertTrue(ok,note)
        self.assertEqual(ids,[101,102,103])
        for person in ids:
            self.assertEqual(self.db.balance(55,'USER',person),250)
            self.assertEqual(self.db.get_claim(55,self.c,'USER',person)['status'],'PAID')
        self.assertEqual(len(self.db.history(55,'USER',102)),1)
        self.assertFalse(self.db.approve_group(55,claim,500,auto=True,attempt_id=attempt)[0])
        self.assertEqual(self.db.balance(55,'USER',101),250)

    def test_exact_duplicate_across_contracts_in_review(self):
        c1,a1,_=self.prepare(hash_='duplicate')
        c2=self.db.create_contract(55,'Second objective','Finish mission and extract',250,999,77)
        c2_claim,a2,dup=self.prepare(user=104,contract=c2,hash_='duplicate')
        self.assertIn('Exact image already submitted',dup)
        self.assertFalse(self.db.approve_group(55,c2_claim,500,auto=True,attempt_id=a2)[0])
        self.assertEqual(self.db.balance(55,'USER',104),0)
        self.assertTrue(self.db.approve_group(55,c2_claim,900,with_teammates=True)[0])
        self.assertEqual(self.db.balance(55,'USER',104),250)

    def test_near_duplicate_flag(self):
        self.prepare(hash_='a',pixels=bytes([80])*3072)
        c2=self.db.create_contract(55,'Another','Different objective test',250,999,77)
        _,_,dup=self.prepare(user=200,contract=c2,hash_='new-bytes',
                             pixels=bytes([81])*3072)
        self.assertIn('Visually similar',dup)

    def test_group_payout_atomic_if_teammate_already_paid(self):
        claim,attempt,_=self.prepare(teammates=[102,103])
        other,_,_=self.prepare(user=103,hash_='b',pixels=bytes([101])*3072,
            dhash='0000000000000000')
        self.assertTrue(self.db.approve_group(55,other,700,with_teammates=False)[0])
        ok,note,paid=self.db.approve_group(55,claim,500,auto=True,attempt_id=attempt)
        self.assertFalse(ok)
        self.assertEqual(paid,[])
        self.assertEqual(self.db.balance(55,'USER',101),0)
        self.assertEqual(self.db.balance(55,'USER',102),0)
        self.assertEqual(self.db.balance(55,'USER',103),250)
        self.assertTrue(self.db.approve_group(55,claim,900,with_teammates=False)[0])
        self.assertEqual(self.db.balance(55,'USER',101),250)

    def test_reject_and_resubmit_preserves_history(self):
        claim,attempt,_=self.prepare()
        self.assertTrue(self.db.reject(55,claim,'No proper proof')[0])
        self.assertEqual(self.db.attempt(55,attempt)['status'],'REJECTED')
        _,attempt2,duplicate=self.prepare(hash_='aaa')
        self.assertNotEqual(attempt,attempt2)
        self.assertIn('Exact image',duplicate)
        self.assertFalse(self.db.approve_group(55,claim,500,auto=True,attempt_id=attempt2)[0])

    def test_group_manual_approval_and_submission_owner(self):
        claim,attempt,_=self.prepare(teammates=[102])
        self.db.set_attempt_status(55,attempt,'REVIEW')
        # Staff review of their own screenshot is allowed and remains idempotent.
        self.assertTrue(self.db.approve_group(55,claim,999,with_teammates=True)[0])
        self.assertFalse(self.db.approve_group(55,claim,888,with_teammates=True)[0])
        self.assertEqual(self.db.balance(55,'USER',102),250)
        self.assertEqual(self.db.claim_by_id(55,claim)['approved_by'],999)

    def test_unit_auto_pays_treasury_once(self):
        self.db.register_unit(55,333,'ANGEL')
        cid=self.db.create_contract(55,'Unit objective','Rescue stranded Divers',800,999,77,
                                    payout_mode='UNIT',target_unit_id=333)
        claim,attempt,_=self.prepare(user=333,contract=cid,hash_='unit',kind='UNIT')
        ok,_,ids=self.db.approve_group(55,claim,500,auto=True,attempt_id=attempt)
        self.assertTrue(ok)
        self.assertEqual(ids,[333])
        self.assertEqual(self.db.balance(55,'UNIT',333),800)
        self.assertEqual(self.db.balance(55,'USER',333),0)

    def test_duplicate_guild_scoped(self):
        self.prepare(hash_='guild-isolated')
        cid=self.db.create_contract(56,'Other server','Different game contract',100,999,77)
        _,_,note=self.prepare(user=301,contract=cid,hash_='guild-isolated',guild=56)
        self.assertEqual(note,'')

    def test_upgrade_keeps_previous_wallet(self):
        self.db.adjust(55,'USER',101,700,999,'Existing credits')
        self.db.conn.close()
        self.db=LedgerStorage(self.path)
        self.assertEqual(self.db.balance(55,'USER',101),700)
        claim,attempt,_=self.prepare()
        self.assertTrue(self.db.approve_group(55,claim,500,auto=True,attempt_id=attempt)[0])
        self.assertEqual(self.db.balance(55,'USER',101),950)


if __name__=='__main__':
    unittest.main()

# Restart recovery: a crash mid-analysis must not permanently block payout.
class RestartRecoveryTests(unittest.TestCase):
    def test_interrupted_processing_claim_reopens_for_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.sqlite3'
            db=LedgerStorage(path)
            contract=db.create_contract(42,'Recover','Rescue and extract safely',350,99,10)
            ok,_,claim=db.submit(42,contract,'USER',27,27,'')
            self.assertTrue(ok)
            attempt,dup=db.record_attempt(42,contract,claim,27,'abcdef','ff'*8,
                bytes([2])*3072,'Genesis Prime',7,[])
            db.conn.close()
            reopened=LedgerStorage(path)
            self.assertEqual(reopened.get_claim(42,contract,'USER',27)['status'],'CLAIMED')
            self.assertEqual(reopened.attempt(42,attempt)['status'],'REJECTED')
            self.assertTrue(reopened.submit(42,contract,'USER',27,27,'new image')[0])
            reopened.conn.close()
