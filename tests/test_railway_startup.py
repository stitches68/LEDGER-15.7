"""Railway storage guards: don't start without a persisted SQLite path."""
from pathlib import Path
import tempfile
import unittest
from start_railway import validate_configuration


class RailwayStorageTests(unittest.TestCase):
    def test_local_environment_is_unrestricted(self):
        self.assertEqual(validate_configuration({
            'DATABASE_PATH': 'ledger.sqlite3'}), 'ledger.sqlite3')

    def test_missing_volume_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'volume missing'):
            validate_configuration({
                'RAILWAY_PROJECT_ID': 'abc',
                'DATABASE_PATH': '/data/ledger.sqlite3'})

    def test_ephemeral_db_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'INSIDE'):
                validate_configuration({
                    'RAILWAY_PROJECT_ID': 'abc',
                    'RAILWAY_VOLUME_MOUNT_PATH': tmp,
                    'DATABASE_PATH': '/app/ledger.sqlite3'})

    def test_relative_db_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'absolute'):
                validate_configuration({
                    'RAILWAY_PROJECT_ID': 'abc',
                    'RAILWAY_VOLUME_MOUNT_PATH': tmp,
                    'DATABASE_PATH': 'ledger.sqlite3'})

    def test_volume_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / 'ledger.sqlite3')
            self.assertEqual(validate_configuration({
                'RAILWAY_PROJECT_ID': 'abc',
                'RAILWAY_VOLUME_MOUNT_PATH': tmp,
                'DATABASE_PATH': target}),target)


if __name__ == '__main__':
    unittest.main()
