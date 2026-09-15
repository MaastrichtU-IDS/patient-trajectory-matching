"""Aggregate-only public-demo partition coverage; no real-source record acceptance or execution."""
import argparse
import json
from pathlib import Path
from . import partitioned_window_query as p, verify_indexed_source_windows as prior, exact_intervals as ei


def verify(folder):
    strata={}
    for name in prior.NAMES:
        request=json.loads((ei.ROOT/'examples/indexed-source-windows'/(name+'-request.json')).read_text())
        selection=p.windows.run(folder,request);prior.validate_summary(selection['summary'],name)
        planned=p.plan(selection)
        ei.require(planned['status']=='PLANNED_PENDING_REVIEW','DEMO_PARTITION_PLAN_INCOMPLETE')
        # Do not serialize plan anchors, batch IDs or source records in published aggregate evidence.
        strata[name]={'source_selection_context':selection['summary']['context'],
            'selection_context_id':selection['summary']['context_id'],'plan_context_id':planned['context_id'],
            'status':planned['status'],'summary':planned['summary'],
            'window_outcomes_before_partitioning':selection['summary']['anchor_outcomes'],
            'covered_ordered_record_pairs':sum(a['coverage']['ordered_pair_count'] for a in planned['anchors']),
            'partition_batch_histogram':dict(sorted((str(k),v) for k,v in p.Counter(len(a['batch_ids']) for a in planned['anchors']).items()))}
    return {'profile':'partitioned-window-verification-1.0','source_pin_sha256':ei.digest(prior.PIN.read_text()),
        'verifier_sha256':ei.digest(Path(__file__).read_text()),'artifacts':{f:ei.digest((ei.ROOT/f).read_text()) for f in p.FILES},
        'limits':{'block_size':p.BLOCK_SIZE,'window_records':p.MAX_WINDOW_RECORDS,'batches':p.MAX_BATCHES},
        'patient_rows_or_identifiers_included':False,'real_source_claims_accepted':0,'real_source_mixed_query_executed':False,
        'scope':'Complete pair-covering partition plans only; record review and real-source execution pending.','strata':strata}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input-dir',type=Path,required=True)
    args=parser.parse_args();r=verify(args.input_dir)
    (ei.ROOT/'verification/partitioned-window-demo-report.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({name:row['summary'] for name,row in r['strata'].items()}))
