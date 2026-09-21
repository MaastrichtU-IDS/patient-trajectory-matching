"""Verify every file hashed in the v2.4 release manifest is unchanged."""
import hashlib, json, pathlib, sys

manifest = json.load(open('verification/v24-release-manifest.json'))
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
