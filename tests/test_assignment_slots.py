"""The upload screen has one optional selector for every visible squad card.

    An empty slot denotes an outside server/faction player. Never fabricate a
    Discord account, in-game name, or statistics for it.
"""
import tempfile
import unittest
from pathlib import Path

from squad_ocr import player_assignment_slots
from storage import LedgerStorage

G = 44183

class AssignmentSlotsTests(unittest.TestCase):
    def test_four_slots_when_only_three_names_readable(self):
        players=[
            {'column':1,'game_name':'Tama','stats':{'kills':212,'stims_used':8}},
            {'column':2,'game_name':'REAPER - Jinx','stats':{'kills':500,'stims_used':28}},
            {'column':4,'game_name':'azisclass','stats':{'kills':157,'stims_used':6}},
        ]
        slots=player_assignment_slots(players,4)
        self.assertEqual(len(slots),4)
        self.assertEqual([v['column'] for v in slots],[1,2,3,4])
        self.assertIsNone(slots[2]['game_name'])
        self.assertFalse(slots[2]['readable'])
        self.assertEqual(slots[2]['stats'],{})
        self.assertEqual(slots[3]['game_name'],'azisclass')

    def test_four_readable_players_do_not_need_four_discord_links(self):
        players=[
            {'column':i,'game_name':f'Squad-{i}','stats':{'kills':i*10,'stims_used':i}}
            for i in range(1,5)
        ]
        slots=player_assignment_slots(players,4)
        self.assertEqual(len(slots),4)
        self.assertTrue(all(s['readable'] for s in slots))
        with tempfile.TemporaryDirectory() as temp:
            db=LedgerStorage(Path(temp)/'ledger.sqlite3')
            cid=db.create_contract(G,'Field Job','Submit evidence',500,100,200,'USER')
            ok,msg,claim=db.submit(G,cid,'USER',41,41,'')
            self.assertTrue(ok,msg)
            # Only one of four players is in the faction and linked to Discord.
            aid,_=db.record_attempt(G,cid,claim,41,'unique-4-cards','0000000000000000',
                                    b'\0'*3072,'Planet',7,[],
                                    player_links=[(41,'Squad-2')])
            db.set_attempt_verdict(G,aid,'PASS','4 player cards',
                                   {'players':players,'player_card_count':4})
            db.save_analysis(G,claim,'PASS','4 player cards')
            success,note,paid=db.approve_group(G,claim,1000,auto=True,attempt_id=aid)
            self.assertTrue(success,note)
            self.assertEqual(paid,[41])
            self.assertEqual(db.proof_stats(G,member_id=41)['totals']['kills'],20)
            self.assertEqual(db.proof_stats(G)['totals']['kills'],20)
            self.assertEqual(db.attempt_player_links(G,aid),[(41,'Squad-2')])
            self.assertEqual(db.clean_vision_stats({'player_card_count':4})['player_card_count'],4)
            db.conn.close()

    def test_all_blank_cannot_pay_an_unlinked_personal_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            db=LedgerStorage(Path(temp)/'ledger.sqlite3')
            cid=db.create_contract(G,'Contract','Bring results',500,100,200,'USER')
            ok,msg,claim=db.submit(G,cid,'USER',41,41,'')
            self.assertTrue(ok,msg)
            aid,_=db.record_attempt(G,cid,claim,41,'blank-hash','0000000000000000',
                                    b'\0'*3072,'Planet',7,[],player_links=[])
            db.set_attempt_verdict(G,aid,'PASS','Visible squad card',{'players':[
                {'column':1,'game_name':'External-One','stats':{'kills':120}},
                {'column':2,'game_name':'External-Two','stats':{'kills':40}}]})
            db.save_analysis(G,claim,'PASS','Visible squad card')
            # A moderator must not bypass the blank-card payout rule.
            success,note,paid=db.approve_group(G,claim,1000,auto=False,attempt_id=aid)
            self.assertFalse(success)
            self.assertEqual(paid,[])
            self.assertEqual(db.balance(G,'USER',41),0)
            db.conn.close()

    def test_unit_treasury_can_receive_proof_with_all_external_players(self):
        with tempfile.TemporaryDirectory() as temp:
            db=LedgerStorage(Path(temp)/'ledger.sqlite3')
            db.register_unit(G,987,'ANGEL')
            cid=db.create_contract(G,'Shared Job','Unit evidence',400,100,200,'UNIT')
            ok,msg,claim=db.submit(G,cid,'UNIT',987,41,'')
            self.assertTrue(ok,msg)
            aid,_=db.record_attempt(G,cid,claim,41,'unit-hash','0000000000000000',
                                    b'\0'*3072,'Planet',7,[],player_links=[])
            db.set_attempt_verdict(G,aid,'PASS','Squad stats',{'players':[
                {'column':1,'game_name':'Outside','stats':{'kills':90}},
                {'column':2,'game_name':'Outside2','stats':{'kills':200}}]})
            db.save_analysis(G,claim,'PASS','Squad stats')
            success,note,paid=db.approve_group(G,claim,1000,auto=True,attempt_id=aid)
            self.assertTrue(success,note)
            self.assertEqual(paid,[987])
            self.assertEqual(db.balance(G,'UNIT',987),400)
            self.assertNotIn('kills',db.proof_stats(G,unit_id=987)['totals'])
            db.conn.close()

    def test_outsiders_stay_blank_and_contribute_no_faction_stats(self):
        players=[
            {'column':1,'game_name':'OutsideA','stats':{'kills':200}},
            {'column':2,'game_name':'Inside','stats':{'kills':10}},
            {'column':3,'game_name':'OutsideB','stats':{'kills':90}},
        ]
        slots=player_assignment_slots(players,3)
        assignments={2:12345}  # All other slots stay intentionally blank.
        linked=[(assignments[slot['column']],slot['game_name']) for slot in slots
                if slot['readable'] and slot['column'] in assignments]
        self.assertEqual(linked,[(12345,'Inside')])
        self.assertEqual(len(slots),3)

if __name__=='__main__':
    unittest.main()
