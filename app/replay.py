"""Verify a downloaded authored research analysis against this checkout."""
import argparse
import json
from pathlib import Path
from demo import cohort
from patterns.patient_similarity import SimilarityEngine
from app.server import Workspace, exact_keys


def verify(bundle):
    exact_keys(bundle, ('format', 'scope', 'similarity', 'comparison', 'exact_replay', 'relaxed_replay', 'manifest_sha256'))
    if bundle['format'] != 'research-workspace-export-1' or bundle['scope'] != 'authored-synthetic-research-prototype':
        raise ValueError('Unsupported research export')
    content = {k:v for k,v in bundle.items() if k != 'manifest_sha256'}
    if cohort.digest(content) != bundle['manifest_sha256']:
        raise ValueError('Export fingerprint differs')
    engine = SimilarityEngine.replay(bundle['similarity'])
    workspace = Workspace()
    admitted, supplied = workspace.engine.metadata(), engine.metadata()
    for key in ('dataset_sha256', 'profile_sha256', 'source_dataset_sha256'):
        if admitted[key] != supplied[key]:
            raise ValueError('Replay differs from the admitted authored fixture: ' + key)
    workspace.engine = engine
    comparison = workspace.compare(bundle['comparison']['revision_id'], bundle['comparison']['budget'])
    if comparison != bundle['comparison']:
        raise ValueError('Trajectory comparison differs on replay')
    for key, field in (('exact_replay', 'exact'), ('relaxed_replay', 'relaxed')):
        result = cohort.verify_export(bundle[key])
        if result != comparison[field]:
            raise ValueError('Embedded trajectory replay differs')
    return {'verified': True, 'revision_count': len(bundle['similarity']['revisions']),
            'reference_patient_id': comparison['exact']['reference_excluded'],
            'exact': comparison['exact']['membership']['included'], 'added': comparison['added']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    with args.manifest.open('rb') as handle:
        raw = handle.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        parser.error('Manifest exceeds the bounded 8 MiB replay limit')
    print(json.dumps(verify(json.loads(raw)), indent=2))


if __name__ == '__main__':
    main()
