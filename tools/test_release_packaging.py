"""Operational boundaries: mounted credentials and consistent private database copies."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from deploy.local_runtime import prepare_credentials
from deploy.state_archive import snapshot, restore, NAME


class ReleasePackagingTests(unittest.TestCase):
    def test_mounted_secret_becomes_private_owned_regular_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'secret-value'
            source.write_text('owner:' + 'x' * 40 + '\n')
            mounted = root / 'mounted'
            mounted.symlink_to(source)
            output = prepare_credentials(mounted, root / 'private' / 'owner')
            self.assertEqual(output.read_bytes(), source.read_bytes())
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertFalse(output.is_symlink())
            with self.assertRaises(FileExistsError):
                prepare_credentials(mounted, output)
            source.write_text('owner:short')
            with self.assertRaises(ValueError):
                prepare_credentials(mounted, root / 'bad')
            self.assertFalse((root / 'bad').exists())

    def test_live_snapshot_restore_does_not_include_later_writes_or_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            live = root / NAME
            live.touch(mode=0o600)
            with sqlite3.connect(live) as db:
                db.execute('create table evidence (id text primary key, value text)')
                db.execute("insert into evidence values ('first', 'retained')")
                db.commit()
                copied = root / 'backup.sqlite3'
                snapshot(live, copied)
                db.execute("insert into evidence values ('second', 'later')")
                db.commit()
                with self.assertRaises(FileExistsError):
                    snapshot(live, copied)
            restored = root / 'restored'
            restore(copied, restored)
            with sqlite3.connect(restored / NAME) as db:
                self.assertEqual(db.execute('select * from evidence').fetchall(), [('first', 'retained')])
            self.assertEqual((restored / NAME).stat().st_mode & 0o777, 0o600)
            self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                restore(copied, restored)
            linked = root / 'linked.sqlite3'
            linked.symlink_to(live)
            with self.assertRaises(ValueError):
                snapshot(linked, root / 'linked-backup.sqlite3')
            live.chmod(0o644)
            with self.assertRaises(ValueError):
                snapshot(live, root / 'unsafe.sqlite3')


if __name__ == '__main__':
    unittest.main()
