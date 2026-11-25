import sqlite3
import tempfile
import unittest
from pathlib import Path
from sys import path

path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import LedgerStorage
from player_links import PlayerLinkError, validated_links


class PlayerLinksTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.file = Path(self.folder.name) / 'ledger.sqlite3'
        self.db = LedgerStorage(self.file)
        self.c = self.db.create_contract(55,'Mission','Proof',250,999,77)
        ok,_,self.claim = self.db.submit(55,self.c,'USER',101,101,'')
        self.assertTrue(ok)

    def tearDown(self):
        self.db.conn.close()
        self.folder.cleanup()

    def test_validate_linked_teammates(self):
        self.assertEqual(validated_links(101,[102,103],{101:'Jinx',102:'Medic-7',103:'Reaper 3'}),
                         [(101,'Jinx'),(102,'Medic-7'),(103,'Reaper 3')])
        for bad in ({102:'Medic-7'}, {102:'Medic-7',103:'MEDIC-7'},
                    {102:'Medic-7',103:'Medic2',999:'Extra'}):
            with self.assertRaises(PlayerLinkError):
                validated_links(101,[102,103],bad)
        self.assertEqual(validated_links(101,[],{}),[])

    def test_links_stored_with_attempt_and_paid_aliases_only(self):
        attempt, dup = self.db.record_attempt(55,self.c,self.claim,101,'digest1','ee'*8,
            bytes([1])*3072,'Genesis Prime',7,[102],player_links=[(101,'Jinx'),(102,'Medic-7')])
        self.assertFalse(dup)
        self.assertEqual(self.db.attempt_player_links(55,attempt),[(101,'Jinx'),(102,'Medic-7')])
        self.assertFalse(self.db.member_handles(55,102))
        self.db.set_attempt_verdict(55,attempt,'PASS','Valid')
        self.db.save_analysis(55,self.claim,'PASS','Valid')
        self.assertTrue(self.db.approve_group(55,self.claim,200,auto=True,attempt_id=attempt)[0])
        self.assertEqual(self.db.member_handles(55,102)[0]['game_name'],'Medic-7')
        self.assertEqual(self.db.balance(55,'USER',102),250)
        self.db.conn.close()
        self.db = LedgerStorage(self.file)
        self.assertEqual(self.db.member_handles(55,102)[0]['game_name'],'Medic-7')
        self.assertTrue(self.db.unlink_player_name(55,attempt,102))
        self.assertFalse(self.db.member_handles(55,102))
        self.assertEqual(self.db.balance(55,'USER',102),250)  # Unlink does not reverse payments.

    def test_links_cannot_be_assigned_to_unselected_member(self):
        with self.assertRaises(PlayerLinkError):
            self.db.record_attempt(55,self.c,self.claim,101,'digest1','ee'*8,
                bytes([1])*3072,'Genesis Prime',7,[102],player_links=[(999,'Fake')])
        self.assertFalse(self.db.latest_attempt(55,self.claim))

    def test_no_duplicate_names_or_ids(self):
        for pairs in ([(102,'Medic-7'),(102,'Medic-7')], [(101,'Alpha'),(102,'ALPHA')]):
            with self.assertRaises(PlayerLinkError):
                self.db.record_attempt(55,self.c,self.claim,101,'digest2','ee'*8,
                    bytes([1])*3072,'Genesis Prime',7,[102],player_links=pairs)


if __name__ == '__main__':
    unittest.main()
