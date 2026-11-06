"""Locate the repository resources the engine reads, in a checkout or when installed.

In a checkout (``../schemas`` and ``../ontology`` exist next to this package) ``ROOT`` is
the plain repository ``Path``, so behaviour is unchanged. Installed from the wheel it is a
small read-only locator: ``patterns/...`` is served from the package, ``schemas/...`` and
``ontology/...`` from ``patterns/_contracts``, and anything else is a repository resource
(examples, data, reports) that is refused. Nothing here writes anything.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CONTRACTS = PACKAGE_DIR / '_contracts'


class InstalledRoot:
    """Path-like stand-in for the repository root when the engine is installed."""

    def __init__(self, package_dir=PACKAGE_DIR):
        self._package = Path(package_dir)
        self._base = self._package.parent
        self._contracts = self._package / '_contracts'

    def _target(self, parts):
        first = parts[0] if parts else None
        if first == 'patterns':
            return self._package.joinpath(*parts[1:])
        if first in ('schemas', 'ontology'):
            return self._contracts.joinpath(*parts)
        raise ValueError('REPOSITORY_RESOURCE_UNAVAILABLE:' + '/'.join(parts))

    def __truediv__(self, other):
        parts = Path(os.fspath(other)).parts
        if not parts or Path(os.fspath(other)).is_absolute() or '..' in parts:
            raise ValueError('REPOSITORY_RESOURCE_UNAVAILABLE:' + os.fspath(other))
        return self._target(parts)

    def joinpath(self, *others):
        return self / os.path.join(*(os.fspath(o) for o in others))

    def __fspath__(self):
        return str(self._base)

    def __str__(self):
        return str(self._base)

    def __repr__(self):
        return f'InstalledRoot({str(self._base)!r})'

    def resolve(self, strict=False):
        return self

    def absolute(self):
        return self

    def __eq__(self, other):
        return isinstance(other, InstalledRoot) and other._base == self._base

    def __hash__(self):
        return hash(self._base)

    def is_dir(self):
        return self._base.is_dir()

    def exists(self):
        return self._base.exists()

    @property
    def parts(self):
        return self._base.parts


def is_checkout(package_dir=PACKAGE_DIR):
    root = Path(package_dir).parent
    return (root / 'schemas').is_dir() and (root / 'ontology').is_dir()


def locate_root(package_dir=PACKAGE_DIR):
    package_dir = Path(package_dir).resolve()
    return package_dir.parents[0] if is_checkout(package_dir) else InstalledRoot(package_dir)


ROOT = locate_root()
