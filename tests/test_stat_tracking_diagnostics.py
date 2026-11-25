"""Regression tests: accepted unit missions must attribute player stats, not personal pay."""
import tempfile
import unittest
from pathlib import Path

from storage import LedgerStorage
from player_links import PlayerLinkError

G = 4010


class StatTrackingAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LedgerStorage(Path(self.tmp.name) / 'ledger.sqlite3')
        self.db.register_unit(G, 99, 'ANGEL')
        self.contract = self.db.create_contract(G, 'Unit mission', 'Complete a mission', 500,
                                               20, 50, 'UNIT')

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def make_proof(self, *, links):
        ok, note, claim = self.db.submit(G, self.contract, 'UNIT', 99, 10, '')
        self.assertTrue(ok, note)
        aid, duplicate = self.db.record_attempt(G, self.contract, claim, 10,
            'unique-unit-image', '0123456789abcdef', b'\x11' * 3072,
            'Genesis Prime', 7, [], player_links=links, stat_link_ids=[11])
        self.assertFalse(duplicate)
        self.db.set_attempt_verdict(G, aid, 'PASS', 'Read three players', {
            'players': [
                {'game_name': 'REAPER - Jinx', 'column': 1,
                 'stats': {'kills': 711, 'stims_used': 11}},
                {'game_name': 'blackbird', 'column': 2,
                 'stats': {'kills': 223, 'stims_used': 2}},
            ]})
        self.db.save_analysis(G, claim, 'PASS', 'Read three players')
        return claim, aid

    def test_unit_pays_treasury_and_records_individual_stats(self):
        claim, aid = self.make_proof(links=[(10, 'REAPER - Jinx'), (11, 'blackbird')])
        ok, msg, recipients = self.db.approve_group(G, claim, 20, auto=True,
                                                     attempt_id=aid)
        self.assertTrue(ok, msg)
        self.assertEqual(recipients, [99])
        self.assertEqual(self.db.balance(G, 'UNIT', 99), 500)
        self.assertEqual(self.db.balance(G, 'USER', 10), 0)
        self.assertEqual(self.db.balance(G, 'USER', 11), 0)
        self.assertEqual(self.db.proof_stats(G, member_id=10)['totals']['kills'], 711)
        self.assertEqual(self.db.proof_stats(G, member_id=11)['totals']['stims_used'], 2)
        self.assertEqual(self.db.proof_stats(G, unit_id=99)['totals']['kills'], 934)
        self.assertEqual({x['recipient_id']: x['total'] for x in
                          self.db.stat_leaderboard(G, 'kills')}, {10: 711, 11: 223})

    def test_unit_stat_link_rejects_untagged_member(self):
        ok, _, claim = self.db.submit(G, self.contract, 'UNIT', 99, 10, '')
        self.assertTrue(ok)
        with self.assertRaises(PlayerLinkError):
            self.db.record_attempt(G, self.contract, claim, 10, 'another-image',
                '0123456789abcdef', b'\x11' * 3072, 'Genesis Prime', 7, [],
                player_links=[(10, 'REAPER - Jinx'), (12, 'stranger')], stat_link_ids=[11])
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM proof_attempts').fetchone()[0], 0)

    def test_name_mismatch_is_not_silently_attributed(self):
        claim, aid = self.make_proof(links=[(10, 'Jinx'), (11, 'blackbird')])
        self.db.approve_group(G, claim, 20, auto=True, attempt_id=aid)
        self.assertNotIn('kills', self.db.proof_stats(G, member_id=10)['totals'])
        self.assertEqual(self.db.proof_stats(G, member_id=11)['totals']['kills'], 223)


if __name__ == '__main__':
    unittest.main()
