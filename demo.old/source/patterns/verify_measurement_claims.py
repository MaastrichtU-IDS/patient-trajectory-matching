"""Reproduce synthetic point/scalar import and selection evidence only."""
import json
from copy import deepcopy
from pathlib import Path
from . import exact_intervals as ei, claim_rdf as cr
from . import measurement_claims as mc, mimic_measurement_import as mi


def verify():
    folder = ei.ROOT / 'examples/mimic-measurement'
    request = json.loads((folder / 'request.json').read_text())
    imported = mi.run(folder, request); episode = imported['episodes'][0]
    store = episode['store']; pending = mc.select(store, episode['acceptance_policy'])
    policy = deepcopy(episode['acceptance_policy'])
    policy['decisions'] = [{'id': 'synthetic_' + str(i), 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
        'action': 'accept', 'supersedes': None, 'reason': 'Constructed test selection; no clinical review claimed.'}
        for i, c in enumerate(store['claims'])]
    selected = mc.select(store, policy)
    ei.require(pending['status'] == 'EMPTY_SELECTED_RECORDS' and selected['status'] == 'SELECTED_MEASUREMENT_RECORDS',
               'SYNTHETIC_MEASUREMENT_SELECTION_FAILED')
    return {'profile': 'measurement-claim-verification-1.0', 'synthetic_only': True,
        'patient_data_included': False, 'public_demo_chartevents_analyzed': False,
        'verifier_sha256': ei.digest(Path(__file__).read_text()),
        'fixture_sha256': {str(p.relative_to(ei.ROOT)): ei.digest(p.read_text()) for p in sorted(folder.iterdir())},
        'import_summary': imported['summary'], 'pending_status': pending['status'],
        'selection_context': selected['context'], 'selection_context_id': selected['context_id'],
        'selected_status': selected['status'], 'selected_synthetic_records': len(selected['records']),
        'selected_synthetic_values': [r['event']['value_lexical'] for r in selected['records']],
        'claim_isolation': selected['isolation'], 'temporal_query_supported': False,
        'accepted_occurrence_graph_generated': False, 'clinical_mapping_verified': False}


if __name__ == '__main__':
    report = verify()
    output = ei.ROOT / 'verification/measurement-claim-report.json'
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'pending': report['pending_status'], 'synthetic_selected': report['selected_synthetic_records'],
                      'isolation_axioms': report['claim_isolation']['logical_axioms_checked']}))
