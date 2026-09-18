"""Create consistent private SQLite backups, or restore into a new state directory."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat

NAME = 'recorded-jobs.sqlite3'


def private_regular(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Database must be an owner-private regular file')


def snapshot(source, destination):
    source, destination = Path(source).absolute(), Path(destination)
    private_regular(source)
    parent = destination.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError('Backup directory must be owner-private')
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(destination)) as copied:
                original.backup(copied)
                if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup database integrity check failed')
    except BaseException:
        destination.unlink()
        raise


def restore(backup, state_dir):
    target = Path(state_dir)
    target.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        snapshot(backup, target / NAME)
    except BaseException:
        target.rmdir()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--state-dir', type=Path, required=True)
    backup.add_argument('--output', type=Path, required=True)
    recovered = sub.add_parser('restore')
    recovered.add_argument('--backup', type=Path, required=True)
    recovered.add_argument('--state-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'backup':
        snapshot(args.state_dir / NAME, args.output)
    else:
        restore(args.backup, args.state_dir)
    print('Private database snapshot completed; start the matching application to validate its namespace and evidence.')


if __name__ == '__main__':
    main()
