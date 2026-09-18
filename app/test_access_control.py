"""Credential-file and authentication boundaries independent of HTTP."""
import base64
import os
from pathlib import Path
import tempfile
import unittest

from app.access_control import OwnerAccess


class OwnerAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'credential'
        self.credential = b'owner:' + b'a' * 40
        self.path.write_bytes(self.credential + b'\n')
        self.path.chmod(0o600)

    def test_exact_credentials_only_and_no_secret_retention(self):
        access = OwnerAccess(self.path)
        self.assertTrue(access.accepts('Basic ' + base64.b64encode(self.credential).decode()))
        for header in (None, '', 'Bearer value', 'Basic !!!', 'Basic ' + 'x' * 600,
                       'Basic ' + base64.b64encode(b'other:' + b'a' * 40).decode(),
                       'Basic ' + base64.b64encode(self.credential + b'x').decode()):
            self.assertFalse(access.accepts(header))
        self.assertNotIn(self.credential.decode(), repr(vars(access)))

    def test_refuses_public_symlink_or_nonregular_file(self):
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            OwnerAccess(self.path)
        self.path.chmod(0o600)
        link = self.path.with_name('link')
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            OwnerAccess(link)
        fifo = self.path.with_name('fifo')
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError):
            OwnerAccess(fifo)

    def test_rejects_weak_oversized_and_multiline_credentials(self):
        for raw in (b'owner:short', b'owner:' + b'x' * 257, b'owner:' + b'x' * 600,
                    b'owner:' + b'x' * 40 + b'\n\n', b'other:' + b'x' * 40):
            self.path.write_bytes(raw)
            with self.assertRaises(ValueError):
                OwnerAccess(self.path)
