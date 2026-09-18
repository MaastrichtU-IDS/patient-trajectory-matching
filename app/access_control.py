"""Optional single-owner HTTP Basic access for the local research workspace."""
import base64
import binascii
import hashlib
import hmac
import os
import stat


class OwnerAccess:
    def __init__(self, path):
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 512):
                raise ValueError('Auth file must be a private regular file owned by the current user')
            raw = os.read(fd, 513)
        finally:
            os.close(fd)
        credential = raw.removesuffix(b'\n')
        if not credential.startswith(b'owner:'):
            raise ValueError('Auth file must contain owner:<secret>')
        secret = credential[6:]
        if not 32 <= len(secret) <= 256 or any(c < 33 or c > 126 for c in secret):
            raise ValueError('Owner secret must contain 32 through 256 printable non-space ASCII characters')
        self._digest = hashlib.sha256(credential).digest()

    def accepts(self, header):
        if not isinstance(header, str) or len(header) > 512:
            return False
        try:
            scheme, encoded = header.split(' ', 1)
            if scheme.lower() != 'basic':
                return False
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return False
        return hmac.compare_digest(hashlib.sha256(raw).digest(), self._digest)
