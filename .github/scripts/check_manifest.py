"""Verify every file hashed in the current release manifest is unchanged.

Superseded manifests stay in verification/ as historical release records and are not
re-checked: they describe the tree at the time of that release, not the tree today.
"""
import hashlib, json, pathlib, sys

manifest = json.load(open('verification/v25-release-manifest.json'))
files = manifest['files']
problems = []
for name, expected in files.items():
    path = pathlib.Path(name)
    if not path.exists():
        problems.append(f'{name}: missing')
        continue
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        problems.append(f'{name}: expected {expected}, got {actual}')
for p in problems:
    print(f'::error::{p}')
print(f'manifest: checked {len(files)} files, {len(problems)} problem(s)')
sys.exit(1 if problems else 0)
