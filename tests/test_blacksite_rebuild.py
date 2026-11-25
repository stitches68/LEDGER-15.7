"""Regression coverage for Blacksite bonuses, dossiers and screenshot rankings."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from sys import path
path.insert(0,str(Path(__file__).resolve().parents[1]))

from contract_objectives import objective_met, validate_bonus
from storage import LedgerStorage, RuleError
from ocr_verifier import assess_ocr_text

G=9001


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.dbpath=Path(self.temp.name)/'older_LEDGER.sqlite3'
        self.db=LedgerStorage(self.dbpath)
        self.bonuses=[
            {'title':'Eliminate Hostiles','stat_key':'kills','target':200,'reward':125},
            {'title':'Combat Medic','stat_key':'stims_used','target':12,'reward':75},
            {'title':'Clean Extraction','stat_key':'deaths','target':0,'reward':90},
        ]
        self.cid=self.db.create_contract(G,'Broker contract','Complete a mission screenshot',500,
            800,99,objectives=self.bonuses)

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def proof(self,*,contract=None,member=1,teammates=None,hash_='unique',stats=None):
        cid=contract or self.cid
        ok,_,claim=self.db.submit(G,cid,'USER',member,member,'')
        self.assertTrue(ok)
        aid,_=self.db.record_attempt(G,cid,claim,member,hash_,'0123456789abcdef',
                    bytes([member%240])*3072,'Genesis Prime',8,teammates or [])
        self.db.set_attempt_verdict(G,aid,'PASS','Recognized',stats or {
            'kills':250,'stims_used':15,'deaths':1})
        self.db.save_analysis(G,claim,'PASS','Recognized')
        return claim,aid

    def test_bonus_validation_rejects_bonuses_at_or_above_base(self):
        with self.assertRaises(ValueError):
            validate_bonus(title='Bonus',metric='kills',target=5,reward=500,base_reward=500)
        with self.assertRaises(ValueError):
            validate_bonus(title='Bonus',metric='made_up_stat',target=5,reward=50,base_reward=500)
        with self.assertRaises(RuleError):
            self.db.create_contract(G,'Wrong','A valid details field',400,800,99,
               objectives=[dict(self.bonuses[0],reward=400)])
        self.assertEqual(len(self.db.objectives(G,self.cid)),3)

    def test_base_and_met_bonus_paid_to_all_tagged_atomically(self):
        claim,aid=self.proof(teammates=[2,3])
        ok,note,ids=self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)
        self.assertTrue(ok,note)
        self.assertEqual(ids,[1,2,3])
        self.assertIn('200 bonus credits',note)
        for rid in ids:
            self.assertEqual(self.db.balance(G,'USER',rid),700)
            self.assertEqual(self.db.earnings(G,'USER',rid)[0],700)
            tx=self.db.history(G,'USER',rid)
            self.assertEqual(len(tx),3) # base 500 + kills 125 + stims 75
        self.assertFalse(self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)[0])
        self.assertEqual(self.db.balance(G,'USER',1),700)

    def test_unknown_bonus_field_not_paid(self):
        claim,aid=self.proof(stats={'kills':201})
        self.assertTrue(self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)[0])
        self.assertEqual(self.db.balance(G,'USER',1),625)

    def test_unconfirmed_screenshot_manual_review_pays_base_only(self):
        claim,aid=self.proof(stats={'kills':250,'stims_used':20})
        self.db.set_attempt_verdict(G,aid,'UNCERTAIN','Needs staff',{'kills':250,'stims_used':20})
        self.assertTrue(self.db.approve_group(G,claim,800,auto=False)[0])
        self.assertEqual(self.db.balance(G,'USER',1),500)

    def test_group_conflict_rolls_back_bonuses_and_base(self):
        # Contractor 3 is paid first, so group containing 3 can't get partial payout.
        self.proof(member=3,hash_='first')
        claim3=self.db.get_claim(G,self.cid,'USER',3)
        self.assertTrue(self.db.approve_group(G,claim3['id'],800,auto=False)[0])
        claim,aid=self.proof(member=1,teammates=[2,3],hash_='second')
        ok,_,_=self.db.approve_group(G,claim,99,auto=True,attempt_id=aid)
        self.assertFalse(ok)
        self.assertEqual(self.db.balance(G,'USER',1),0)
        self.assertEqual(self.db.balance(G,'USER',2),0)

    def test_unit_bonus_goes_to_treasury(self):
        self.db.register_unit(G,600,'ANGEL')
        unit_c=self.db.create_contract(G,'Medical mission','Finish medic contract results',500,800,99,
            payout_mode='UNIT',target_unit_id=600,objectives=[self.bonuses[1]])
        ok,_,claim=self.db.submit(G,unit_c,'UNIT',600,2,'')
        self.assertTrue(ok)
        aid,_=self.db.record_attempt(G,unit_c,claim,2,'medic_hash','fedcba0123456789',
                                     bytes([43])*3072,'Genesis Prime',7,[])
        self.db.set_attempt_verdict(G,aid,'PASS','Recognized',{'stims_used':14})
        self.db.save_analysis(G,claim,'PASS','Recognized')
        self.assertTrue(self.db.approve_group(G,claim,100,auto=True,attempt_id=aid)[0])
        self.assertEqual(self.db.balance(G,'UNIT',600),575)
        self.assertEqual(self.db.balance(G,'USER',2),0)

    def test_paid_screenshot_rankings_count_submitter_not_tags(self):
        claim,aid=self.proof(teammates=[2,3])
        self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)
        self.assertEqual(self.db.stat_leaderboard(G,'kills')[0]['recipient_id'],1)
        self.assertEqual(self.db.stat_leaderboard(G,'kills')[0]['total'],250)
        self.assertEqual(self.db.stat_leaderboard(G,'stims_used')[0]['total'],15)
        self.assertEqual(self.db.member_summary(G,2)['stats']['submissions'],0)
        self.assertEqual(self.db.member_summary(G,2)['balance'],700)

    def test_manual_paid_exact_duplicate_counts_once_on_stats_board(self):
        claim,aid=self.proof()
        self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)
        other=self.db.create_contract(G,'Another','Another proof for checks',500,800,99)
        claim2,aid2=self.proof(contract=other,member=4,hash_='unique')
        self.db.approve_group(G,claim2,800,auto=False)
        self.assertEqual(self.db.stat_leaderboard(G,'kills')[0]['total'],250)
        self.assertEqual(len(self.db.stat_leaderboard(G,'kills')),1)

    def test_objective_edit_freezes_after_payment(self):
        bonus={'title':'Optional Shots','stat_key':'shots_fired','target':100,'reward':10}
        self.db.add_objective(G,self.cid,bonus)
        claim,aid=self.proof()
        self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)
        with self.assertRaises(RuleError):
            self.db.add_objective(G,self.cid,bonus)

    def test_upgrade_retains_existing_balances_and_payouts(self):
        self.db.adjust(G,'USER',50,1000,100,'Old wallet')
        claim,aid=self.proof()
        self.db.approve_group(G,claim,5,auto=True,attempt_id=aid)
        self.db.conn.close()
        self.db=LedgerStorage(self.dbpath)
        self.assertEqual(self.db.balance(G,'USER',50),1000)
        self.assertEqual(self.db.balance(G,'USER',1),700)
        self.assertEqual(len(self.db.objectives(G,self.cid)),3)

    def test_stim_ocr_is_read_only_from_unambiguous_lines(self):
        text='''MISSION STATS\nHELLDIVERS 2\nSTIMS USED: 14\nKILLS: 321\nDEATHS: 2\nSHOTS FIRED: 1001\nSAMPLES EXTRACTED: 8\nSTRATAGEMS USED: 4'''
        verdict,_,facts=assess_ocr_text(text)
        self.assertEqual(verdict,'PASS')
        self.assertEqual(facts['stims_used'],14)
        text=text.replace('STIMS USED: 14','STIMS USED: 14\nSTIMS USED: 20')
        _,_,facts=assess_ocr_text(text)
        self.assertNotIn('stims_used',facts)


if __name__=='__main__':
    unittest.main()
