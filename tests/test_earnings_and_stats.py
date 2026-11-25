import json
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import LedgerStorage


class EarningsAndStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.file=Path(self.temp.name)/'data.sqlite3'
        self.db=LedgerStorage(self.file)
        self.gid=42
        self.db.configure(self.gid,'board_channel_id',123)
        self.contract=self.db.create_contract(self.gid,'Rescue','Complete mission',1000,99,123)

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def pay_photo(self,submitter, teammates=None, *, kind='USER', recipient=None,
                  planet='Genesis Prime', difficulty=7, facts=None, verdict='PASS'):
        recipient=submitter if recipient is None else recipient
        ok,_,claim=self.db.submit(self.gid,self.contract,kind,recipient,submitter,'')
        self.assertTrue(ok)
        imagehash=f'sha-{submitter}-{claim}'
        attempt,duplicate=self.db.record_attempt(self.gid,self.contract,claim,submitter,
            imagehash,f'{submitter+claim:016x}',b'not-real-image',planet,difficulty,teammates or [])
        self.assertFalse(duplicate)
        self.db.set_attempt_verdict(self.gid,attempt,verdict,'test verdict',facts or {})
        self.db.save_analysis(self.gid,claim,verdict,'test verdict')
        self.assertTrue(self.db.approve_group(self.gid,claim,99,auto=True,attempt_id=attempt)[0])
        return attempt

    def test_earnings_rank_by_contract_payouts_not_wallet(self):
        self.pay_photo(101,teammates=[102],facts={'kills':200})
        self.db.adjust(self.gid,'USER',101,25000,99,'Staff grant (not contract earned)')
        self.db.adjust(self.gid,'USER',102,-800,99,'Buy from shop (wallet reduced)')
        self.assertEqual(self.db.earnings(self.gid,'USER',101),(1000,1))
        self.assertEqual(self.db.earnings(self.gid,'USER',102),(1000,1))
        self.assertEqual(self.db.balance(self.gid,'USER',101),26000)
        self.assertEqual(self.db.balance(self.gid,'USER',102),200)
        self.assertEqual([(r['recipient_id'],r['earned']) for r in self.db.top_earners(self.gid)],
                         [(101,1000),(102,1000)])
        self.assertEqual(self.db.top_earners(100,'USER'),[])
        self.assertEqual(self.db.earnings_totals(self.gid)['USER']['earned'],2000)

    def test_unit_earnings_separate_from_individual(self):
        self.db.register_unit(self.gid,900,'ANGEL')
        cid=self.db.create_contract(self.gid,'Medical','Save lives',750,99,123,'UNIT')
        ok,_,claim=self.db.submit(self.gid,cid,'UNIT',900,101,'')
        self.assertTrue(ok)
        attempt,_=self.db.record_attempt(self.gid,cid,claim,101,'some-unique-image',
            '0000000000000001',b'not-real-image','Malevelon Creek',9,[])
        self.db.set_attempt_verdict(self.gid,attempt,'PASS','success',{'kills':55,'extracted':True})
        self.db.save_analysis(self.gid,claim,'PASS','success')
        self.assertTrue(self.db.approve_group(self.gid,claim,99,auto=True,attempt_id=attempt)[0])
        self.assertEqual(self.db.earnings(self.gid,'UNIT',900),(750,1))
        self.assertEqual(self.db.earnings(self.gid,'USER',101),(0,0))
        self.assertEqual(self.db.proof_stats(self.gid,unit_id=900)['totals']['kills'],55)
        self.assertEqual(self.db.proof_stats(self.gid,member_id=101)['totals']['kills'],55)
        self.assertEqual(self.db.proof_stats(self.gid,unit_id=999)['submissions'],0)

    def test_verification_stats_and_tags_not_double_counted(self):
        self.pay_photo(101,teammates=[102,103],facts={
            'kills':320,'deaths':0,'samples_collected':12,'shots_fired':555,
            'mission_success':True,'extracted':True,'planet_visible':'Genesis Prime',
            'difficulty_visible':7})
        stats=self.db.proof_stats(self.gid)
        self.assertEqual(stats['paid'],1)
        self.assertEqual(stats['submissions'],1)
        self.assertEqual(stats['totals']['kills'],320)
        self.assertEqual(stats['totals']['deaths'],0)
        self.assertEqual(stats['available']['deaths'],1)
        self.assertEqual(stats['booleans']['extracted'],1)
        self.assertEqual(stats['planets'],[('Genesis Prime',1)])
        self.assertEqual(stats['difficulties'],[(7,1)])
        self.assertEqual(stats['visible_planets'],[('Genesis Prime',1)])
        self.assertEqual(stats['visible_difficulties'],[(7,1)])
        self.assertEqual(self.db.proof_stats(self.gid,member_id=102)['submissions'],0)
        self.assertEqual(len(self.db.top_earners(self.gid)),3)

    def test_unreadable_data_does_not_become_fabricated_zeroes(self):
        self.pay_photo(101,facts={'kills':None,'deaths':None,'mission_success':None,
                                  'samples_collected':'9999','difficulty_visible':0,
                                  'shots_hit':True})
        stats=self.db.proof_stats(self.gid)
        self.assertEqual(stats['totals'],{})
        self.assertEqual(stats['available'],{})
        self.assertEqual(stats['ai_passed_paid'],1)

    def test_rejected_screenshots_do_not_inflate_verified_totals(self):
        self.pay_photo(101,facts={'kills':50},planet='Mars')
        ok,_,claim=self.db.submit(self.gid,self.contract,'USER',102,102,'')
        self.assertTrue(ok)
        aid,_=self.db.record_attempt(self.gid,self.contract,claim,102,'bad-image',
                    '00000000000f0000',b'not-real-image','Genesis Prime',3,[])
        self.db.set_attempt_verdict(self.gid,aid,'FAIL','bad stats',{'kills':99999})
        self.db.save_analysis(self.gid,claim,'FAIL','bad stats')
        self.assertTrue(self.db.reject(self.gid,claim,'failed')[0])
        stats=self.db.proof_stats(self.gid)
        self.assertEqual(stats['paid'],1)
        self.assertEqual(stats['rejected'],1)
        self.assertEqual(stats['totals']['kills'],50)
        self.assertEqual(stats['planets'],[('Mars',1)])
        self.assertEqual(self.db.recent_proof_stats(self.gid,102)[0]['ai_verdict'],'FAIL')

    def test_channel_persistence_and_upgrade_vision_stats(self):
        self.db.configure(self.gid,'top_earners_channel_id',321)
        self.db.configure(self.gid,'top_earners_message_id',789)
        self.pay_photo(101,facts={'kills':100,'shots_hit':50})
        self.db.conn.close()
        self.db=LedgerStorage(self.file)
        self.assertEqual(self.db.setting(self.gid,'top_earners_channel_id'),321)
        self.assertEqual(self.db.setting(self.gid,'top_earners_message_id'),789)
        self.assertEqual(self.db.top_earners_channels()[0]['channel_id'],321)
        self.assertEqual(self.db.proof_stats(self.gid)['totals']['kills'],100)
        self.assertEqual(json.loads(self.db.recent_proof_stats(self.gid,101)[0]['vision_stats'])['kills'],100)

    def test_malformed_stats_cannot_break_aggregates(self):
        self.pay_photo(101,facts={'kills':90})
        with self.db.atomic():
            self.db.conn.execute("UPDATE proof_attempts SET vision_stats='invalid JSON' WHERE guild_id=?",(self.gid,))
        stats=self.db.proof_stats(self.gid)
        self.assertEqual(stats['paid'],1)
        self.assertEqual(stats['totals'],{})


if __name__=='__main__':
    unittest.main()
