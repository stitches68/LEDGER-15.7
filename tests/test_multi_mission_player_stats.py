"""Multi-mission progress, repeatable cycles and player-specific OCR regression."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from PIL import Image

from storage import LedgerStorage, RuleError
from squad_ocr import player_values, normalize_handle

G=811


class MissionProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'ledger.sqlite3'
        self.db=LedgerStorage(self.path)
        self.bonuses=[
            {'title':'Mass Elimination','stat_key':'kills','target':300,'reward':125},
            {'title':'Field Medic','stat_key':'stims_used','target':5,'reward':75},
        ]
        self.cid=self.db.create_contract(G,'Sweep the Sector','Finish multiple missions',500,88,77,
                         objectives=self.bonuses,required_submissions=3,repeatable=True)

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def proof(self,n,*,teammates=None,stats_a=None,stats_b=None,user=10,contract=None):
        cid=contract or self.cid
        ok,msg,claim=self.db.submit(G,cid,'USER',user,user,'image-url')
        self.assertTrue(ok,msg)
        players=[{'game_name':'Alpha-10','column':1,
                  'stats':stats_a or {'kills':125,'stims_used':2,'deaths':0}},
                 {'game_name':'Bravo-11','column':2,
                  'stats':stats_b or {'kills':30,'stims_used':1,'deaths':2}}]
        aid,dup=self.db.record_attempt(G,cid,claim,user,f'image-hash-{cid}-{n}',
                 f'{n:016x}',bytes([((n*27)%210)+20])*3072,'Genesis Prime',7,
                 teammates or [],player_links=[(10,'Alpha-10')]+(
                   [(11,'Bravo-11')] if 11 in (teammates or []) else []))
        self.db.set_attempt_verdict(G,aid,'PASS','Read named player stats',{'players':players})
        self.db.save_analysis(G,claim,'PASS','Read named player stats')
        return claim,aid,dup

    def test_milestone_pays_later_and_repeats_without_double_payment(self):
        for n in range(1,7):
            claim,aid,duplicate=self.proof(n,teammates=[11])
            self.assertFalse(duplicate)
            ok,msg,paid=self.db.approve_group(G,claim,999,auto=True,attempt_id=aid)
            self.assertTrue(ok,msg)
            if n%3:
                self.assertEqual(paid,[])
                self.assertEqual(self.db.balance(G,'USER',10), (n//3)*700)
                self.assertEqual(self.db.progress_for(G,self.cid,'USER',10)['count'],n%3)
            else:
                self.assertEqual(paid,[10,11])
                self.assertEqual(self.db.balance(G,'USER',10),(n//3)*700)
                self.assertEqual(self.db.balance(G,'USER',11),(n//3)*500)
            self.assertFalse(self.db.approve_group(G,claim,999,auto=True,attempt_id=aid)[0])
        self.assertEqual(self.db.progress_for(G,self.cid,'USER',10)['cycle'],2)
        self.assertEqual(self.db.earnings(G,'USER',10)[0],1400)
        self.assertEqual(len(self.db.stat_leaderboard(G,'kills')),2)
        totals={r['recipient_id']:r['total'] for r in self.db.stat_leaderboard(G,'kills')}
        self.assertEqual(totals,{10:750,11:180})
        stims={r['recipient_id']:r['total'] for r in self.db.stat_leaderboard(G,'stims_used')}
        self.assertEqual(stims,{10:12,11:6})

    def test_one_time_mode_does_not_start_second_cycle(self):
        one=self.db.create_contract(G,'One time operation','One and done mission',500,88,77,
                                    required_submissions=2,repeatable=False)
        for n in (41,42):
            claim,aid,_=self.proof(n,contract=one)
            paid=self.db.approve_group(G,claim,999,auto=True,attempt_id=aid)[2]
            self.assertEqual(paid,[] if n==41 else [10])
        self.assertFalse(self.db.submit(G,one,'USER',10,10,'again')[0])
        self.assertFalse(self.db.claim(G,one,'USER',10,10)[0])

    def test_settings_locked_after_a_proof_and_upgrade_does_not_reset_wallet(self):
        self.db.adjust(G,'USER',10,100,88,'starting balance')
        self.assertTrue(self.db.configure_progress(G,self.cid,2,True))
        self.proof(111)
        with self.assertRaises(RuleError):
            self.db.configure_progress(G,self.cid,3,False)
        self.db.conn.close()
        self.db=LedgerStorage(self.path)
        self.assertEqual(self.db.balance(G,'USER',10),100)
        self.assertEqual(self.db.get_contract(G,self.cid)['required_submissions'],2)

    def test_per_player_stats_never_assigned_without_name_match(self):
        claim,aid,_=self.proof(90,teammates=[11])
        self.db.approve_group(G,claim,999,auto=True,attempt_id=aid)
        self.assertEqual(self.db.screenshot_player_stats(G,self.db.attempt(G,aid),'USER',10)['kills'],125)
        self.assertEqual(self.db.screenshot_player_stats(G,self.db.attempt(G,aid),'USER',11)['kills'],30)
        self.db.unlink_player_name(G,aid,11)
        self.assertNotIn('kills',self.db.screenshot_player_stats(G,self.db.attempt(G,aid),'USER',11))

    def test_legacy_contracts_remain_single_mission_once_only(self):
        cid=self.db.create_contract(G,'Old operation','Legacy one screenshot',100,88,77)
        self.assertEqual(self.db.get_contract(G,cid)['required_submissions'],1)
        self.assertEqual(self.db.get_contract(G,cid)['repeatable'],0)

    def test_player_matching_does_not_guess(self):
        self.assertEqual(normalize_handle('REAPER - Jinx'),'reaperjinx')
        players=[{'game_name':'REAPER - Jinx','stats':{'kills':711}}]
        self.assertEqual(player_values(players,'reaper_jinx')['kills'],711)
        self.assertFalse(player_values(players,'Unrelated Name'))


if __name__=='__main__': unittest.main()
