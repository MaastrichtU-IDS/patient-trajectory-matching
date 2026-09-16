"""A fixed, synthetic cohort projected through the repository's PRO/SOLID adapter.

Serving and matching use only the standard library. Rebuilding the source graphs
requires the pinned graph dependencies. The reference patient is never a candidate.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import copy
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent
sys.path.insert(0, str(SOURCE))
from reference_oracle import evaluate, validate_pattern


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_dataset():
    from patterns.pro_solid import build_graph, project
    # ID, drug, exposure days before index, baseline hours before index,
    # original baseline value/unit, follow-up mg/dL, source-search completeness.
    specs = [
        ('P00', 'DrugA', 7, 24, '10.0', 'mg/L', '1.30', True),
        ('P01', 'DrugA', 7, 24, '1.00', 'mg/dL', '1.30', True),
        ('P02', 'DrugAChild', 5, 48, '1.00', 'mg/dL', '1.40', True),
        ('P03', 'DrugA', 2, 12, '8.0', 'mg/L', '1.20', True),
        ('P04', 'DrugB', 6, 24, '1.00', 'mg/dL', '1.30', True),
        ('P05', 'DrugA', 9, 24, '1.00', 'mg/dL', '1.30', True),
        ('P06', 'DrugB', 9, 24, '1.00', 'mg/dL', '1.40', True),
        ('P07', 'DrugA', 6, 24, '1.00', 'mg/dL', '1.29', True),
        ('P08', 'DrugA', 4, 60, '1.00', 'mg/dL', '1.40', True),
        ('P09', 'DrugA', 10, 24, '1.00', 'mg/dL', '1.30', True),
        ('P10', 'DrugA', 7, 24, '1.00', 'mg/dL', '1.30', False),
    ]
    patients = []
    for i, (pid, drug, days, hours, baseline, unit, followup, complete) in enumerate(specs):
        origin = datetime(2024, 2, 15, tzinfo=timezone.utc) + timedelta(days=i * 3)
        iso = lambda t: t.isoformat().replace('+00:00', 'Z')
        rows = []
        for slot, kind, concept, when, value, source_unit in [
            ('B', 'measurement', 'Creatinine', origin - timedelta(hours=hours), baseline, unit),
            ('E', 'administration', drug, origin - timedelta(days=days), None, None),
            ('F', 'measurement', 'Creatinine', origin, followup, 'mg/dL'),
        ]:
            eid = f'{pid}-{slot}'
            rows.append({'patient_id': pid, 'episode_id': f'{pid}-admission', 'event_id': eid,
                         'record_id': f'source-{eid}', 'event_kind': kind, 'source_code': f'ex:{concept}',
                         'status': 'performed', 'datetime': iso(when), 'value': value, 'unit': source_unit})
        manifest = {'profile': 'pro-solid-2.4', 'origin': iso(origin),
                    'dataset_id': 'guided-synthetic-cohort', 'snapshot_id': 'guided-cohort-v1',
                    'clock_id': f'{pid}-relative', 'patient_id': pid, 'episode_id': f'{pid}-admission',
                    'age': 50, 'source_search_complete': complete}
        # project() performs actual graph validation and retains role/value/source bindings.
        case, evidence = project(build_graph(rows), manifest)
        case.update(case_id=f'GUIDED-{pid}', description=f'Synthetic trajectory {pid}')
        patients.append({'patient_id': pid, 'case': case, 'evidence': evidence,
                         'source_rows': rows, 'manifest': manifest})
    return {'schema_version': 'guided-cohort-1', 'dataset_id': 'guided-synthetic-cohort',
            'snapshot_id': 'guided-cohort-v1', 'reference_patient_id': 'P00', 'patients': patients,
            'generation_base_commit': subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip(),
            'projection_sha256': file_digest(SOURCE / 'patterns/pro_solid.py'),
            'availability_policy': 'Constructed snapshot; no source-availability cutoff or historical replay is asserted.'}


def run_cohort(data, budget):
    if str(budget) not in ('0', '1', '2'):
        raise ValueError('Supported cohort budgets are 0, 1 and 2.')
    pattern = json.loads((SOURCE / 'examples/exemplar.pattern.json').read_text())
    taxonomy = json.loads((SOURCE / 'ontology/toy-taxonomy.json').read_text())
    query = copy.deepcopy(pattern)
    query['budget'] = {'max_total_cost': str(budget), 'max_relaxed_constraints': 2}
    validate_pattern(query, pattern)
    results = []
    membership = {'exact': [], 'relaxed': [], 'unresolved': [], 'not_matched': []}
    groups = {'EXACT': 'exact', 'RELAXED': 'relaxed', 'UNRESOLVED': 'unresolved', 'NONE': 'not_matched'}
    for patient in data['patients']:
        pid = patient['patient_id']
        if pid == data['reference_patient_id']:
            continue
        case = copy.deepcopy(patient['case'])
        case['budget_override'] = None
        result = evaluate(case, query, taxonomy)
        results.append({'patient_id': pid, **result})
        membership[groups[result['accepted_as']]].append(pid)
    membership['included'] = sorted(membership['exact'] + membership['relaxed'])
    return {'schema_version': 'guided-cohort-result-1', 'execution_mode': 'live-python',
            'query': query, 'query_sha256': digest(query), 'taxonomy': taxonomy,
            'oracle_sha256': file_digest(SOURCE / 'reference_oracle.py'),
            'dataset_sha256': digest(data), 'snapshot_id': data['snapshot_id'],
            'reference_excluded': data['reference_patient_id'], 'candidate_count': len(results),
            'results': results, 'membership': membership}


def export_cohort(data, result):
    return {'format': 'guided-cohort-export-1', 'dataset': data, 'evaluation': result}


def verify_export(bundle):
    """Re-evaluate a downloaded query against its exact embedded snapshot and inputs."""
    if bundle.get('format') != 'guided-cohort-export-1':
        raise ValueError('Unsupported export format')
    data, recorded = bundle['dataset'], bundle['evaluation']
    if digest(data) != recorded['dataset_sha256'] or digest(recorded['query']) != recorded['query_sha256']:
        raise ValueError('Export query or dataset fingerprint differs')
    if file_digest(SOURCE / 'reference_oracle.py') != recorded['oracle_sha256']:
        raise ValueError('Use the oracle identified by the export; this checkout has a different implementation')
    actual = run_cohort(data, recorded['query']['budget']['max_total_cost'])
    for key in ('query', 'taxonomy', 'results', 'membership', 'candidate_count', 'reference_excluded'):
        if actual[key] != recorded[key]:
            raise ValueError(f'Reproduction differs: {key}')
    return actual


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-export', type=Path, required=True)
    args = parser.parse_args()
    result = verify_export(json.loads(args.verify_export.read_text()))
    print(json.dumps({'verified': True, 'included': result['membership']['included'],
                      'unresolved': result['membership']['unresolved']}))
