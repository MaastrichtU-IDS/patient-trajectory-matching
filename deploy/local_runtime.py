"""Local research container launcher; preserves the loopback-only access boundary."""
import os
from pathlib import Path
import sys
import tempfile

from app.access_control import OwnerAccess


def prepare_credentials(source, destination):
    """Copy a mounted secret into an owner-private regular file (K8s mounts symlinks)."""
    with open(source, 'rb') as stream:
        raw = stream.read(513)
    if len(raw) > 512:
        raise ValueError('Mounted owner credential exceeds 512 bytes')
    target = Path(destination)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        OwnerAccess(target)
    except BaseException:
        target.unlink()
        raise
    return target


def main():
    if os.getuid() == 0:
        raise ValueError('Local research container must run as a non-root user')
    private = Path(tempfile.mkdtemp(prefix='ptm-owner-', dir='/tmp'))
    credential = prepare_credentials('/run/secrets/owner', private / 'owner')
    state = Path('/var/lib/trajectory/recorded')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    arguments = [sys.executable, '-m', 'app.server', '--host', '127.0.0.1',
                 '--port', '8080', '--state-dir', str(state), '--auth-file', str(credential)]
    clinical_features = os.environ.get('PTM_CLINICAL_FEATURES', '')
    if clinical_features:
        if not Path(clinical_features).is_absolute():
            raise ValueError('PTM_CLINICAL_FEATURES must be an absolute startup path')
        arguments.extend(['--clinical-features', clinical_features])
    os.execv(sys.executable, arguments)


if __name__ == '__main__':
    main()
