"""Regression tests for the original Blacksite rebuild additions.

These tests avoid Discord/network dependencies. They exercise accounting and
OCR parsing at the core and leave graphical interactions to live Discord tests.
"""
import tempfile
import unittest
from pathlib import Path

from storage import LedgerStorage, RuleError
from operation_ocr import assess_operation_text, combine_evidence
from blacksite_lore import intel_drop

G=958024


class BlacksiteOverhaulTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'ledger.sqlite3'
        self.db=LedgerStorage(self.path)
        self.cid=self.db.create_contract(G,'Blood Money','Recover the team and log a mission.',500,
                                         42,99,'USER')

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_dossier_cosmetic_data_survives_reopen_and_does_not_mutate_wallet(self):
        self.db.adjust(G,'USER',100,400,42,'Opening bonus')
        self.db.update_contractor_card(G,100,callsign='Jinx',specialty='Combat recovery',
                                       bio='First in. Last out.',motto='No diver left behind.')
        before=self.db.balance(G,'USER',100)
        self.db.conn.close()
        self.db=LedgerStorage(self.path)
        card=self.db.contractor_card(G,100)
        self.assertEqual(card['callsign'],'Jinx')
        self.assertEqual(card['specialty'],'Combat recovery')
        self.assertEqual(self.db.balance(G,'USER',100),before)

    def test_cosmetic_fields_reject_mentions_and_oversized_text(self):
        with self.assertRaises(RuleError):
            self.db.update_contractor_card(G,100,callsign='@everyone')
        with self.assertRaises(RuleError):
            self.db.update_contractor_card(G,100,bio='x'*221)
        self.assertEqual(self.db.contractor_card(G,100)['callsign'],'')

    def test_public_progress_counts_one_screenshot_for_entire_tagged_team(self):
        self.db.set_public_goal(G,self.cid,50)
        self.db.set_proof_mode(G,self.cid,'STATS')
        ok,note,claim=self.db.submit(G,self.cid,'USER',100,100,'')
        self.assertTrue(ok,note)
        aid,_=self.db.record_attempt(G,self.cid,claim,100,'unique-hash','0000000000000000',
            b'\x00'*3072,'Genesis Prime',7,[101,102],
            player_links=[(100,'Jinx'),(101,'blackbird'),(102,'Lukatsugar')])
        self.db.set_attempt_verdict(G,aid,'PASS','Three player cards recognized',{
            'players':[{'game_name':'Jinx','column':1,'stats':{'kills':711,'stims_used':11}},
                       {'game_name':'blackbird','column':2,'stats':{'kills':223,'stims_used':2}},
                       {'game_name':'Lukatsugar','column':3,'stats':{'kills':210,'stims_used':3}}]})
        self.db.save_analysis(G,claim,'PASS','Three player cards recognized')
        okay,msg,paid=self.db.approve_group(G,claim,42,auto=True,attempt_id=aid)
        self.assertTrue(okay,msg)
        self.assertEqual(len(paid),3)
        self.assertEqual(self.db.dispatch_rows(G)[0]['accepted'],1)
        self.assertEqual(self.db.dispatch_rows(G)[0]['public_goal'],50)
        self.assertEqual(self.db.proof_stats(G,member_id=100)['totals']['kills'],711)
        self.assertEqual(self.db.proof_stats(G,member_id=101)['totals']['kills'],223)
        self.assertEqual(self.db.proof_stats(G,member_id=102)['totals']['kills'],210)
        ok2,_,_=self.db.approve_group(G,claim,42,auto=True,attempt_id=aid)
        self.assertFalse(ok2)
        self.assertEqual(self.db.dispatch_rows(G)[0]['accepted'],1)

    def test_operation_screen_requires_operation_context(self):
        good='''HELLDIVERS 2
        SUPER EARTH HIGH COMMAND
        OPERATION COMPLETED
        PLANET: GENESIS PRIME
        DIFFICULTY 7
        3 / 3 MISSIONS COMPLETED
        VICTORY'''
        verdict,reason,facts=assess_operation_text(good)
        self.assertEqual(verdict,'PASS')
        self.assertEqual(facts['planet_visible'],'Genesis Prime')
        self.assertEqual(facts['difficulty_visible'],7)
        self.assertEqual(facts['missions_completed'],3)
        self.assertTrue(facts['operation_success'])
        verdict,_,_=assess_operation_text('KILLS: 711\nSTIMS USED: 11\nMISSION COMPLETE')
        self.assertNotEqual(verdict,'PASS')
        verdict,_,_=assess_operation_text('HELLO WORLD / shop order balance')
        self.assertEqual(verdict,'FAIL')
        verdict,_,failed=assess_operation_text('HELLDIVERS 2\nOPERATION FAILED\nSUPER EARTH\nDIFFICULTY 7')
        self.assertEqual(verdict,'FAIL')
        self.assertFalse(failed['operation_success'])

    def test_both_screens_count_as_one_evidence(self):
        stats=('PASS','Stats recognized',{
            'players':[{'game_name':'Jinx','column':1,'stats':{'kills':711,'stims_used':11}}]})
        op=('PASS','Operation completed',{'planet_visible':'Genesis Prime','operation_success':True})
        verdict,summary,facts=combine_evidence(stats,op,mode='BOTH')
        self.assertEqual(verdict,'PASS')
        self.assertEqual(facts['players'][0]['stats']['kills'],711)
        self.assertEqual(facts['planet_visible'],'Genesis Prime')
        self.assertTrue(facts['operation_screen_verified'])
        self.assertEqual(combine_evidence(stats,None,mode='BOTH')[0],'UNCERTAIN')
        self.assertEqual(combine_evidence(stats,('FAIL','unrelated',{}),mode='BOTH')[0],'UNCERTAIN')
        self.assertEqual(combine_evidence(stats,None,mode='STATS')[0],'PASS')

    def test_companion_image_reuse_flagged(self):
        # Attach two different proof attempts to different contracts; repeat the
        # operation image hash to ensure the second one is review-only.
        ok,_,claim=self.db.submit(G,self.cid,'USER',100,100,'')
        a1,_=self.db.record_attempt(G,self.cid,claim,100,'image1','0000000000000000',
                                    b'\0'*3072,'Prime',7,[])
        self.assertFalse(self.db.track_companion(G,a1,'shared-operation-image'))
        other=self.db.create_contract(G,'Second Mission','Another sample contract',300,42,99,'USER')
        ok,_,claim2=self.db.submit(G,other,'USER',100,100,'')
        a2,_=self.db.record_attempt(G,other,claim2,100,'image2','1111111111111111',
                                    b'\1'*3072,'Prime',7,[])
        self.assertTrue(self.db.track_companion(G,a2,'shared-operation-image'))

    def test_lore_is_deterministic_and_never_pays(self):
        x=intel_drop(100,'2026-11-11')
        self.assertEqual(x,intel_drop(100,'2026-11-11'))
        self.assertTrue(x[0] and x[1])
        self.assertEqual(self.db.balance(G,'USER',100),0)


if __name__ == '__main__':
    unittest.main()
