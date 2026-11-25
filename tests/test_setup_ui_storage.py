"""Regression tests for the interactive channel/role setup release.

Discord menus are not exercised without a gateway connection. These tests cover
business-rule writes triggered by the role multi-select and their live membership
semantics using a real SQLite database.
"""
import tempfile
import unittest
from pathlib import Path
from sys import path
path.insert(0, str(Path(__file__).resolve().parents[1]))
from storage import LedgerStorage, RuleError


class RoleMenuStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = LedgerStorage(Path(self.temp.name) / 'ledger.sqlite3')

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_select_roles_creates_separate_units_and_live_membership(self):
        new, old = self.db.register_units_from_roles(10, [(111, 'ANGEL'), (222, 'SPECTER'), (333, 'WARDEN')])
        self.assertEqual(new, [111, 222, 333])
        self.assertEqual(old, [])
        self.assertEqual({r['role_id'] for r in self.db.units(10)}, {111, 222, 333})
        self.assertEqual({r['role_id'] for r in self.db.member_units(10, [111])}, {111})
        self.assertEqual({r['role_id'] for r in self.db.member_units(10, [111, 333])}, {111, 333})
        self.assertEqual({r['role_id'] for r in self.db.member_units(10, [222])}, {222})
        self.assertEqual(self.db.member_units(10, []), [])  # removing role instantly removes membership
        self.assertEqual(self.db.balance(10, 'UNIT', 222), 0)

    def test_repeat_registration_preserves_treasuries_and_buyer_permissions(self):
        self.db.register_units_from_roles(10, [(111, 'ANGEL')])
        self.db.adjust(10, 'UNIT', 111, 3750, 99, 'Reward for contract')
        self.db.set_unit_buyer_role(10, 111, 555)
        new, old = self.db.register_units_from_roles(10, [(111, 'ANGEL UNIT'), (222, 'SPECTER')])
        self.assertEqual((new, old), ([222], [111]))
        self.assertEqual(self.db.balance(10, 'UNIT', 111), 3750)
        self.assertEqual(self.db.get_unit(10, 111)['buyer_role_id'], 555)
        self.assertEqual(self.db.get_unit(10, 111)['name'], 'ANGEL UNIT')

    def test_already_linked_role_rolls_back_entire_menu_selection(self):
        self.db.register_unit(10, 111, 'ANGEL')
        self.db.link_unit_role(10, 111, 333, 99)
        with self.assertRaisesRegex(RuleError, 'already paired'):
            self.db.register_units_from_roles(10, [(444, 'NEW'), (333, 'CONFLICT')])
        self.assertIsNone(self.db.get_unit(10, 444))
        self.assertEqual(self.db.unit_member_roles(10, 111), [333])

    def test_role_menus_are_guild_scoped(self):
        self.db.register_units_from_roles(10, [(111, 'ANGEL')])
        self.db.register_units_from_roles(20, [(111, 'SURTR')])
        self.assertEqual(self.db.member_units(20, [111])[0]['name'], 'SURTR')
        self.assertEqual(self.db.member_units(10, [111])[0]['name'], 'ANGEL')

    def test_selection_rejects_empty_duplicate_or_too_many_roles(self):
        with self.assertRaises(RuleError):
            self.db.register_units_from_roles(10, [])
        with self.assertRaises(RuleError):
            self.db.register_units_from_roles(10, [(1, 'A'), (1, 'B')])
        with self.assertRaises(RuleError):
            self.db.register_units_from_roles(10, [(i, f'Unit{i}') for i in range(1, 27)])
        with self.assertRaises(RuleError):
            self.db.register_units_from_roles(10, [(0, 'Invalid')])
        self.assertEqual(self.db.units(10), [])


if __name__ == '__main__':
    unittest.main()
