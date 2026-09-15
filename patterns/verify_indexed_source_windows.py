"""Pinned aggregate window-selection evidence for separate public-demo measurement strata."""
import argparse
import json
from pathlib import Path
from . import indexed_source_windows as windows, exact_intervals as ei, claim_rdf as cr

PIN = ei.ROOT / 'data/clinical-source-demo-pin.json'
NAMES = ('demo-arterial', 'demo-noninvasive', 'demo-art')


def validate_summary(summary, name):
    pin = json.loads(PIN.read_text())
    request = json.loads((ei.ROOT / 'examples/indexed-source-windows' / (name+'-request.json')).read_text())
    ei.require(summary['context']['request'] == request, 'DEMO_WINDOW_REQUEST_MISMATCH')
    ei.require(summary['context_id'] == cr.digest(summary['context']), 'WINDOW_CONTEXT_DIGEST_MISMATCH')
    files = {f['table']:f for f in summary['context']['source_files']}
    ei.require(set(files) == set(pin['files']), 'DEMO_WINDOW_TABLE_SET_MISMATCH')
    for table, expected in pin['files'].items():
        for key, value in expected.items():ei.require(files[table][key] == value, 'DEMO_WINDOW_PIN_MISMATCH:'+table+':'+key)
    ei.require(summary['full_source_scan_complete'] and summary['row_accounting_complete'] and
               summary['all_indexed_windows_agree_with_reference'], 'DEMO_WINDOW_SELECTION_NOT_VERIFIED')
    for table, total in summary['input_rows'].items():
        ei.require(total == files[table]['rows'] and sum(summary['row_outcomes'][table].values()) == total, 'WINDOW_ROW_ACCOUNTING_MISMATCH')
    ei.require(sum(summary['anchor_outcomes'].values()) == summary['anchors'], 'WINDOW_ANCHOR_ACCOUNTING_MISMATCH')
    return summary


def verify(folder):
    results = {}
    for name in NAMES:
        request = json.loads((ei.ROOT / 'examples/indexed-source-windows' / (name+'-request.json')).read_text())
        results[name] = validate_summary(windows.run(folder,request)['summary'],name)
    return {'profile':'indexed-source-windows-verification-1.0','source_url':json.loads(PIN.read_text())['source_url'],
        'source_pin_sha256':ei.digest(PIN.read_text()),'verifier_sha256':ei.digest(Path(__file__).read_text()),
        'published_file_hashes_verified':True,'patient_rows_or_identifiers_included':False,
        'scope':'Separate conditional record-window selections; no acceptance or real-source mixed query.',
        'strata':results}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input-dir',type=Path,required=True)
    args=parser.parse_args();report=verify(args.input_dir)
    (ei.ROOT/'verification/indexed-source-windows-demo-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:{f:v[f] for f in ('status','anchors','anchor_outcomes','max_window_records','unique_records_in_windows')} for k,v in report['strata'].items()}))
