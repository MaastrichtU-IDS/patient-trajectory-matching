"""Finite, constant-cost relaxation with fixed modification and fixed witness certainty."""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from . import bounded_intervals as bt, bounded_cohort as cohort, exact_intervals as ei
from . import mixed_record_query as mixed
from . import extended_interval_query as extended

PROFILE = 'robust-temporal-relaxation-1.0'
SCHEMA = ei.ROOT / 'schemas/robust-relaxation.schema.json'
MIXED_INPUTS = ('interval-store','interval-policy','semantic-policy','measurement-store','measurement-policy','alignment')


def variants(query, policy):
    """Validate the entire catalogue before filtering by budget; never edit the source."""
    Draft202012Validator(json.loads(SCHEMA.read_text())).validate(policy)
    ei.require(type(policy['max_changed_targets']) is int, 'INTEGER_BUDGET_REQUIRED')
    if policy['kind'] == 'bounded':
        bt.validate(query, query=True)
        targets = {c['id']:c for c in query['constraints'] if c['operator']=='gap'}
    elif policy['kind'] == 'extended':
        extended.validate(query)
        targets = {c['id']:c for c in query['constraints']
                   if c['operator'] in ('gap', 'duration', 'minimum_overlap')}
    else:
        # The existing mixed entry point remains the authoritative query validator.
        mixed.validate_query(query)
        targets = {'baseline':query['baseline']}
    ei.require(set(policy['relaxable_targets']) <= targets.keys(), 'INVALID_RELAXABLE_TARGET')
    ids = [o['id'] for o in policy['options']]
    ei.require(len(ids)==len(set(ids)) and 'original' not in ids, 'DUPLICATE_OR_RESERVED_OPTION')
    result = [{'id':'original','cost':'0','changes':[],'query':deepcopy(query)}]
    excluded = []
    for option in policy['options']:
        changed = [c['target'] for c in option['changes']]
        ei.require(len(changed)==len(set(changed)), 'DUPLICATE_CHANGE_TARGET')
        ei.require(set(changed)<=set(policy['relaxable_targets']), 'PROTECTED_TARGET')
        modified = deepcopy(query)
        for c in option['changes']:
            old = targets[c['target']]
            if policy['kind']=='mixed':
                target=modified['baseline']
                low, high = 'min_before_start_us','max_before_start_us'
            else:
                target=next(t for t in modified['constraints'] if t['id']==c['target'])
                low, high = ('minimum_us','maximum_us') if old['operator']=='duration' else ('min_gap_us','max_gap_us')
            if policy['kind']=='extended' and old['operator']=='minimum_overlap':
                ei.fields(c, ('target','minimum_us'))
                minimum = ei.checked_us(c['minimum_us'])
                ei.require(0 < minimum < old['minimum_us'], 'NOT_A_POSITIVE_OVERLAP_RELAXATION')
                target['minimum_us'] = minimum
                continue
            ei.fields(c, ('target','lower_us','upper_us'))
            lower, upper = ei.checked_us(c['lower_us']), ei.checked_us(c['upper_us'])
            ei.require(lower<=old[low] and upper>=old[high] and (lower,upper)!=(old[low],old[high]), 'NOT_A_STRICT_WIDENING')
            if policy['kind']=='mixed':
                ei.require(lower>=0, 'NEGATIVE_BASELINE_WINDOW')
            elif old['operator']=='duration':
                ei.require(lower>0, 'NONPOSITIVE_DURATION_BOUND')
            target.update({low:lower,high:upper})
        if policy['kind']=='extended':
            extended.validate(modified)
        if Decimal(option['cost'])>Decimal(policy['max_cost']) or len(changed)>policy['max_changed_targets']:
            excluded.append(option['id'])
        else:
            result.append({**deepcopy(option),'query':modified})
    return result, excluded


def _bindings(result, kind):
    if kind in ('bounded','extended'):
        for t in result['trajectories']:
            for b in t['bindings']:
                yield t['patient_id'],t['episode_id'],b['status'],b['slots'],b
    else:
        for b in result['baseline_bindings']:
            yield b['patient_id'],b['episode_id'],b['eligibility']['status'],{
                'treatment':b['treatment'],'baseline_id':b['baseline_id']},b


def execute(source, query, policy):
    options, excluded = variants(query, policy)
    if policy['kind']=='mixed':
        ei.fields(source, MIXED_INPUTS)
    extra = ('patterns/extended_interval_query.py', 'schemas/extended-interval-query.schema.json') if policy['kind']=='extended' else ()
    context = {'profile':PROFILE,'source_sha256':ei.digest(ei.canonical(source)),
               'query':deepcopy(query),'policy':deepcopy(policy),
               'artifacts':{p:ei.digest((ei.ROOT/p).read_text()) for p in
                            sorted(set(mixed.FILES + extra + ('patterns/bounded_intervals.py',
                            'patterns/robust_relaxation.py','schemas/robust-relaxation.schema.json')))}}
    result = {'profile':PROFILE,'context':context,'context_id':ei.digest(ei.canonical(context)),
              'certainty_semantics':'exists_catalogue_modification_exists_named_binding_forall_source_timelines',
              'scope':'explicit_catalogue_within_budget_over_represented_records',
              'status':'BLOCKED','search_complete':False,'excluded_by_budget':excluded,
              'robust_patient_ids':None,'possible_patient_ids':None,'best_robust_matches':None,'evaluations':[]}
    try:
        snapshot = bt.prepare(source) if policy['kind'] in ('bounded','extended') else None
        for option in options:
            executor = extended.execute if policy['kind']=='extended' else cohort.execute
            run = (executor(snapshot,option['query']) if snapshot else
                   mixed.execute(*(source[n] for n in MIXED_INPUTS),option['query']))
            result['evaluations'].append({'option':option,'result':run})
            if policy['kind']=='mixed' and (run['status']!='COMPLETED_RECORD_QUERY' or
                                            not run['search_complete_over_selected_records']):
                result['reason']='INCOMPLETE_UNDERLYING_QUERY'
                return result
            if policy['kind'] in ('bounded','extended') and run.get('search_complete') is not True:
                result['reason']='INCOMPLETE_UNDERLYING_QUERY'
                return result
    except (bt.InconsistentSource, ei.ContractError) as error:
        result['reason']=str(error)
        return result
    robust, possible = [], set()
    for evaluation in result['evaluations']:
        option=evaluation['option']
        for patient,episode,status,binding,evidence in _bindings(evaluation['result'],policy['kind']):
            if status in ('CERTAIN','POSSIBLE'): possible.add(patient)
            if status=='CERTAIN':
                robust.append({'patient_id':patient,'episode_id':episode,'option_id':option['id'],
                               'cost':option['cost'],'binding':binding,'evidence':evidence})
    robust.sort(key=lambda r:(Decimal(r['cost']),r['option_id'] != 'original',
                              r['option_id'],ei.canonical(r['binding']),r['episode_id']))
    best={}
    for row in robust: best.setdefault(row['patient_id'],row)
    result.update(status='COMPLETED',search_complete=True,robust_patient_ids=sorted(best),
                  possible_patient_ids=sorted(possible),best_robust_matches=[best[p] for p in sorted(best)])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','query','policy'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    result=execute(*(json.loads(getattr(args,n).read_text()) for n in ('source','query','policy')))
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='COMPLETED' else 2


if __name__=='__main__': raise SystemExit(main())
