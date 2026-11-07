"""The synthetic cohort generator must build every seed, and the store must accept what the converter makes of it.

Regression for generator defects the scaling benchmark found: a child born after the snapshot had no events
(`min()` of nothing), the reference child could have no visit in the stale-letter window (`StopIteration`), and the
date-typo defect put a different year on each bound of a range that crossed New Year (INVERTED_BOUNDS).
Needs DuckDB only for the store's ingest validation (`patterns/requirements-store.lock.txt`); skipped without it.
"""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover - the store extra is optional
    duckdb = None

spec = importlib.util.spec_from_file_location('synthetic_generate', ROOT / 'demos/mockups/synthetic/generate.py')
generate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generate)

BASE = 20261103
# Seeds that crashed or produced inverted bounds before the fixes.
KNOWN_BAD = [20261121, 20261123, 20261168, 20261185, 20261192, 20261194, 20261248, 20261262, 20261266, 20261311]
SEEDS = KNOWN_BAD + list(range(BASE, BASE + 40))


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class SeedTests(unittest.TestCase):
    def test_every_sampled_seed_builds_and_the_store_accepts_it(self):
        from patterns import execution_store as es
        from patterns.synthetic_ingest import synthetic_ingest
        for seed in SEEDS:
            with self.subTest(seed=seed):
                cohort = json.loads(json.dumps(generate.serialise(generate.build(seed), seed), ensure_ascii=False))
                es.validate_ingest(synthetic_ingest(cohort))


if __name__ == '__main__':
    unittest.main()
