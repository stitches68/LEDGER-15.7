"""Regression tests: OCR gibberish must not derail saved contractor statistics."""
import tempfile
import unittest
from pathlib import Path

from storage import LedgerStorage, RuleError
from squad_ocr import player_assignment_slots

G = 8821

class PositionStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=LedgerStorage(Path(self.tmp.name)/'ledger.sqlite3')
        self.cid=self.db.create_contract(G,'Test Card','Verified screenshot',500,999,111,'USER')

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_wrong_ocr_name_still_links_via_selected_card(self):
        ok,msg,claim=self.db.submit(G,self.cid,'USER',10,10,'')
        self.assertTrue(ok,msg)
        aid,duplicate=self.db.record_attempt(G,self.cid,claim,10,'unique-a',
            '0000000000000000',b'\0'*3072,'Genesis Prime',6,[],
            player_links=[(10,'NOCANCHD Ainv')],player_columns={10:2})
        self.assertFalse(duplicate)
        players=[
            {'column':1,'game_name':'Taam','stats':{'kills':112,'stims_used':8,'deaths':2}},
            {'column':2,'game_name':'REAPER - Jinx','stats':{'kills':500,'stims_used':28,'deaths':5}},
        ]
        self.db.set_attempt_verdict(G,aid,'PASS','Read results',{'players':players,'player_card_count':2})
        self.db.save_analysis(G,claim,'PASS','Read results')
        paid,note,recipients=self.db.approve_group(G,claim,999,auto=True,attempt_id=aid)
        self.assertTrue(paid,note)
        self.assertEqual(recipients,[10])
        self.assertEqual(self.db.balance(G,'USER',10),500)
        self.assertEqual(self.db.proof_stats(G,member_id=10)['totals']['kills'],500)
        self.assertEqual(self.db.proof_stats(G)['totals']['kills'],500)
        self.assertEqual(self.db.stat_leaderboard(G,'stims_used')[0]['total'],28)
        self.assertEqual(self.db.screenshot_player_stats(G,self.db.attempt(G,aid),'USER',10)['kills'],500)
        self.assertEqual(self.db.attempt_player_records(G,aid)[0]['player_column'],2)
        # An unconfirmed OCR string must not pollute the dossier's saved names.
        self.assertEqual(self.db.member_handles(G,10),[])

    def test_two_linked_players_outsider_blank(self):
        ok,msg,claim=self.db.submit(G,self.cid,'USER',10,10,'')
        self.assertTrue(ok,msg)
        aid,_=self.db.record_attempt(G,self.cid,claim,10,'unique-b',
            '0000000000000000',b'\0'*3072,'Genesis Prime',6,[11],
            player_links=[(10,'wrong spelled name'),(11,'also wrong')],
            player_columns={10:2,11:4})
        players=[{'column':i,'game_name':label,'stats':{'kills':kills,'stims_used':i}}
                 for i,(label,kills) in enumerate([
                     ('outsider',999),('REAPER - Jinx',500),('outsider two',900),('blackbird',223)],1)]
        self.db.set_attempt_verdict(G,aid,'PASS','Stats read',{'players':players,'player_card_count':4})
        self.db.save_analysis(G,claim,'PASS','Stats read')
        paid,note,recipients=self.db.approve_group(G,claim,999,auto=True,with_teammates=True,attempt_id=aid)
        self.assertTrue(paid,note)
        self.assertEqual(set(recipients),{10,11})
        self.assertEqual(self.db.proof_stats(G,member_id=10)['totals']['kills'],500)
        self.assertEqual(self.db.proof_stats(G,member_id=11)['totals']['kills'],223)
        self.assertEqual(self.db.proof_stats(G)['totals']['kills'],723)

    def test_name_unreadable_but_stats_readable_is_assignable(self):
        slots=player_assignment_slots([
           {'column':1,'game_name':'', 'stats':{'kills':53,'stims_used':4}},
           {'column':2,'game_name':'clear','stats':{'kills':19}},
        ],2)
        self.assertTrue(slots[0]['readable'])
        self.assertFalse(slots[0]['name_readable'])
        self.assertIsNone(slots[0]['game_name'])
        self.assertTrue(slots[1]['name_readable'])

    def test_legacy_exact_name_links_still_work(self):
        ok,msg,claim=self.db.submit(G,self.cid,'USER',10,10,'')
        self.assertTrue(ok,msg)
        aid,_=self.db.record_attempt(G,self.cid,claim,10,'unique-c',
            '0000000000000000',b'\0'*3072,'Genesis Prime',6,[],
            player_links=[(10,'real name')])
        self.db.set_attempt_verdict(G,aid,'PASS','Read results',{'players':[
            {'column':1,'game_name':'real name','stats':{'kills':76}},
            {'column':2,'game_name':'other','stats':{'kills':100}}]})
        self.db.save_analysis(G,claim,'PASS','Read results')
        paid,note,recipients=self.db.approve_group(G,claim,999,auto=True,attempt_id=aid)
        self.assertTrue(paid,note)
        self.assertEqual(self.db.proof_stats(G,member_id=10)['totals']['kills'],76)
        self.assertEqual(self.db.member_handles(G,10)[0]['game_name'],'real name')

    def test_reject_duplicate_card_assignment(self):
        ok,_,claim=self.db.submit(G,self.cid,'USER',10,10,'')
        self.assertTrue(ok)
        with self.assertRaises(RuleError):
            self.db.record_attempt(G,self.cid,claim,10,'unique-d',
                '0000000000000000',b'\0'*3072,'Genesis Prime',6,[11],
                player_links=[(10,'one'),(11,'two')],player_columns={10:2,11:2})

if __name__=='__main__':
    unittest.main()

# Add a separate class below the script's optional __main__ invocation so
# unittest discovery also catches the layout / sanitation regression.
class NameGuessRegressions(unittest.TestCase):
    def test_duplicate_guessed_names_do_not_erase_distinct_columns(self):
        facts=LedgerStorage.clean_vision_stats({'players':[
            {'column':1,'game_name':'Unreadable','stats':{'kills':25}},
            {'column':2,'game_name':'Unreadable','stats':{'kills':47}},
        ],'player_card_count':2})
        self.assertEqual(len(facts['players']),2)
        self.assertEqual([p['stats']['kills'] for p in facts['players']],[25,47])

    def test_duplicate_numbers_for_same_column_are_not_counted_twice(self):
        facts=LedgerStorage.clean_vision_stats({'players':[
            {'column':1,'game_name':'Name A','stats':{'kills':25}},
            {'column':1,'game_name':'Name B','stats':{'kills':100}},
        ]})
        self.assertEqual(len(facts['players']),1)
