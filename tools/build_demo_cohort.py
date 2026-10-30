"""Derive a ranking cohort from the pinned open-access MIMIC-IV demo, outside the repo.

The research workspace ranks pre-index histories. Until now its only cohort was the
eleven authored patients in `demo/cohort-data.json`, so `--public-demo-dir` reached the
recorded treatment-evidence journey and nothing else. This builds the same cohort shape
from the pinned demo release, so the similarity, refinement and trajectory surfaces can
rank real ICU stays.

What is and is not derived, measured on the pinned release:

  baseline_creatinine  928 charted observations of items 220615 / 229761, already in
                       mg/dL, covering 137 of the 140 stays.
  clinical_concepts    inputevents items started in the pre-index window, as
                       `mimic:item/<itemid>`, each with its dictionary category as its
                       one ancestor -- the same shape as the authored ancestor
                       expansion, taken from the source dictionary rather than invented.
  age_band             NOT DERIVED. `anchor_age` lives in `hosp/patients.csv.gz`, which
                       this repository does not pin, and no chartevents item carries it.
                       Every patient reports age_band unobserved, which the engine
                       already handles as missingness and the interface already explains.
                       This is issue S3; do not synthesise a value to fill the column.

The index is `intime + --index-hours` (24 by default), the standard first-day ICU
landmark. 24 hours reaches 136 of 140 stays with at least one pre-index creatinine, and
48 or 72 reach no more, so the longer windows buy nothing. Stays shorter than the offset
have an index after discharge; charting stops at discharge, so their window is simply
the whole stay. The landmark is a modelling choice and is recorded as one: it carries no
clinical review, and the emitted dataset says so.

Output goes to --output-dir, never into the repository: these are rows derived from
patient records, and the project keeps generated source rows outside the tree.
"""
import argparse
import csv
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from patterns import patient_similarity as similarity

PIN = ROOT / 'data/clinical-source-demo-pin.json'
CREATININE_ITEMS = {'220615': 'Creatinine (serum)', '229761': 'Creatinine (whole blood)'}
CHART_TIME = '%Y-%m-%d %H:%M:%S'
VERSION = 'public-demo-ranking-cohort-1.0'
MICROSECOND = 1
SECOND = 1_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_sources(folder, pin_path=PIN):
    """Digest every pinned file before anything reads it.

    A cohort built from an unpinned extract would be indistinguishable from one built
    from the release, and the engine admits a recorded dataset only when its declared
    pin equals this file, so a mismatch here could never become an admissible dataset.
    """
    pin = json.loads(Path(pin_path).read_bytes())
    digests = {}
    for name, meta in pin['files'].items():
        path = folder / f'{name}.csv.gz'
        require(path.exists(), f'Pinned source file is missing: {path}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest == meta['file_sha256'],
                f'{name}.csv.gz does not match the committed pin: {digest}')
        digests[name] = digest
    return pin, digests


def rows(folder, name):
    with gzip.open(folder / f'{name}.csv.gz', 'rt', newline='') as handle:
        yield from csv.DictReader(handle)


def when(value):
    return datetime.strptime(value, CHART_TIME)


def micros(delta):
    return int(delta.total_seconds() * SECOND)


def read_stays(folder, index_hours):
    stays = {}
    for row in rows(folder, 'icustays'):
        intime = when(row['intime'])
        stays[row['stay_id']] = {'stay_id': row['stay_id'], 'subject_id': row['subject_id'],
                                 'intime': intime, 'outtime': when(row['outtime']),
                                 'index': intime + timedelta(hours=index_hours)}
    return stays


def creatinine_records(folder, stays, digest):
    """Charted creatinine in the pre-index window, as baseline_creatinine records.

    The engine selects the minimum value in its own window, so every eligible
    observation is emitted and none is pre-aggregated here. `valuenum` is used for the
    value and the untouched `value` string is retained as the original.
    """
    found = defaultdict(list)
    for index, row in enumerate(rows(folder, 'chartevents'), start=1):
        if row['itemid'] not in CREATININE_ITEMS or row['stay_id'] not in stays:
            continue
        if not row['valuenum'] or row['valueuom'] != 'mg/dL':
            continue
        stay = stays[row['stay_id']]
        occurrence = when(row['charttime'])
        if not stay['intime'] <= occurrence < stay['index']:
            continue
        found[row['stay_id']].append({
            'id': f'creatinine-{row["stay_id"]}-{index}',
            'component': 'baseline_creatinine',
            'value': {'hasValue': row['valuenum']},
            'unit': 'mg/dL',
            'occurrence_us': micros(occurrence - stay['index']),
            'available_at_us': micros(occurrence - stay['index']),
            'source': {'document': f'chartevents.csv.gz:{digest}',
                       'row': str(index),
                       'original_value': row['value'],
                       'original_unit': row['valueuom'],
                       'availability_policy': 'Charted value available at its charttime; '
                                              'no source-system delay is modelled.'}})
    return found


def concept_vocabulary(folder, stays, index_hours, limit):
    """The bounded concept catalogue, chosen by how many stays each item reaches.

    The profile admits at most 100 reviewed concepts and the pre-index window holds 134
    distinct items, so the catalogue is a declared truncation rather than the whole
    vocabulary. Ranking by stay coverage keeps the items that separate patients; the
    emitted dataset records the cut so a reader is not left to infer it.
    """
    labels = {row['itemid']: row for row in rows(folder, 'd_items')}
    reach = defaultdict(set)
    for row in rows(folder, 'inputevents'):
        stay = stays.get(row['stay_id'])
        if stay is None:
            continue
        if stay['intime'] <= when(row['starttime']) < stay['index']:
            reach[row['itemid']].add(row['stay_id'])
    ordered = sorted(reach, key=lambda item: (-len(reach[item]), item))
    selected = ordered[:limit]
    ancestors = {}
    for itemid in selected:
        category = labels[itemid]['category'] or 'Uncategorised'
        ancestors[f'mimic:item/{itemid}'] = [f'mimic:category/{category.replace(" ", "-")}']
    return ancestors, set(selected), len(ordered)


def concept_records(folder, stays, admitted, digest):
    seen, found = set(), defaultdict(list)
    for index, row in enumerate(rows(folder, 'inputevents'), start=1):
        stay = stays.get(row['stay_id'])
        if stay is None or row['itemid'] not in admitted:
            continue
        occurrence = when(row['starttime'])
        if not stay['intime'] <= occurrence < stay['index']:
            continue
        key = (row['stay_id'], row['itemid'])
        if key in seen:
            continue
        seen.add(key)
        found[row['stay_id']].append({
            'id': f'concept-{row["stay_id"]}-{row["itemid"]}',
            'component': 'clinical_concepts',
            'value': [f'mimic:item/{row["itemid"]}'],
            'coverage_complete': True,
            'occurrence_us': micros(occurrence - stay['index']),
            'available_at_us': micros(occurrence - stay['index']),
            'source': {'document': f'inputevents.csv.gz:{digest}',
                       'row': str(index),
                       'availability_policy': 'Administration available at its starttime; '
                                              'no source-system delay is modelled.'}})
    return found


def patients(stays, creatinine, concepts, index_rule):
    result = []
    for stay_id in sorted(stays, key=int):
        stay = stays[stay_id]
        records = creatinine.get(stay_id, []) + concepts.get(stay_id, [])
        if not records:
            continue
        clock = f'stay-{stay_id}-relative'
        for record in records:
            record['clock'] = clock
            record['claim'] = {'id': f'{record["id"]}-claim', 'refersTo': f'{record["id"]}-role'}
            record['role'] = {'id': f'{record["id"]}-role', 'isFeatureOf': f'stay-{stay_id}'}
        result.append({'patient_id': f'stay-{stay_id}',
                       'label': f'ICU stay {stay_id} (subject {stay["subject_id"]})',
                       'episode_id': f'stay-{stay_id}-icu',
                       'clock': clock,
                       'index_us': 0,
                       'index_rule': index_rule,
                       'records': sorted(records, key=lambda r: r['id'])})
    return result


def build(folder, *, index_hours, concept_limit, pin_path=PIN):
    pin, digests = verify_sources(folder, pin_path)
    stays = read_stays(folder, index_hours)
    ancestors, admitted, available = concept_vocabulary(folder, stays, index_hours, concept_limit)
    creatinine = creatinine_records(folder, stays, digests['chartevents'])
    concepts = concept_records(folder, stays, admitted, digests['inputevents'])
    index_rule = (f'ICU intime plus {index_hours} hours; every record strictly before it. '
                  'Modelling landmark only -- no clinical review.')
    pool = patients(stays, creatinine, concepts, index_rule)
    require(len(pool) >= 2, 'Pinned demo yielded fewer than two rankable stays')

    dataset = {
        'dataset_id': f'mimic-iv-demo-2.2-icu-first-{index_hours}h',
        'label': f'{len(pool)} pinned MIMIC-IV demo ICU stays, first {index_hours} hours',
        'scope': 'recorded_public_demo',
        'clinical_validity_claim': False,
        'index_rule': index_rule,
        'source_pin': digests,
        'construction_origin': {
            'path': str(Path(folder).name),
            'sha256': hashlib.sha256(
                json.dumps(digests, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'availability_policy': 'Charted and administered values available at their recorded '
                                   'time; no source-system delay is modelled.',
            'original_availability_policy': 'MIMIC-IV demo 2.2 charttime and starttime as released.',
            # Every construction_origin value is capped at 200 characters by the engine,
            # so this states the two facts a reader must not miss and the module
            # docstring carries the rest.
            'scope': f'{VERSION}; age_band not derivable from the pinned files, unobserved '
                     f'for every stay; concepts cut to {len(ancestors)} of {available} by '
                     'stay coverage; source fidelity only, no clinical approval.'},
        'patients': pool}

    profile = {
        'profile_id': f'public-demo-first-{index_hours}h-ranking-1.0',
        'clinical_validity_claim': False,
        'mapping_review': 'pinned_public_demo_source_fidelity',
        'weights': {'age_band': '0', 'baseline_creatinine': '1', 'clinical_concepts': '1'},
        'minimum_coverage': '0.5',
        'history_us': index_hours * similarity.HOUR,
        'creatinine_window_us': index_hours * similarity.HOUR,
        'creatinine_scale': '1',
        'creatinine_unit': 'mg/dL',
        'concept_ancestors': ancestors}
    return dataset, profile, pin


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mimic-dir', type=Path, required=True,
                        help='Directory holding the four pinned demo .csv.gz files')
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='Private directory for the derived cohort; keep it outside the repository')
    parser.add_argument('--index-hours', type=int, default=24,
                        help='Index landmark as hours after ICU intime (default 24)')
    parser.add_argument('--concept-limit', type=int, default=80,
                        help='Reviewed concepts retained, by stay coverage (default 80, profile ceiling 100)')
    args = parser.parse_args()
    require(0 < args.index_hours <= 24 * 365, 'index-hours must be a positive window within a year')
    require(0 < args.concept_limit <= 100, 'concept-limit must be between 1 and the profile ceiling of 100')

    dataset, profile, _ = build(args.mimic_dir, index_hours=args.index_hours,
                                concept_limit=args.concept_limit)
    # Admit through the engine before writing: a cohort the workspace would refuse to
    # load is not worth leaving on disk looking usable.
    engine = similarity.SimilarityEngine(dataset, profile)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'dataset.json').write_text(json.dumps(dataset, indent=2, sort_keys=True) + '\n')
    (output / 'profile.json').write_text(json.dumps(profile, indent=2, sort_keys=True) + '\n')

    metadata = engine.metadata()
    # Counted from the emitted records, not from engine internals: this reports what was
    # derived. Whether a record is then eligible is the engine's judgement at query time.
    carrying = defaultdict(int)
    for patient in dataset['patients']:
        for component in {record['component'] for record in patient['records']}:
            carrying[component] += 1
    print(f'cohort written to {output}')
    print(f'  stays ranked        {len(dataset["patients"])}')
    print(f'  records             {sum(len(p["records"]) for p in dataset["patients"])}')
    print(f'  reviewed concepts   {len(profile["concept_ancestors"])}')
    print(f'  dataset_sha256      {metadata["dataset_sha256"]}')
    for component in similarity.COMPONENTS:
        print(f'  stays with {component:20} {carrying[component]} of {len(dataset["patients"])}')


if __name__ == '__main__':
    main()
