#!/usr/bin/env python3
"""Generate the synthetic paediatric rare-disease cohort that the interface mockups run on.

Every child here is invented. Each one has two layers:

- ``truth`` is what happened, in days of age, with no recording artefacts. The mockup
  hides it by default; it exists so an interface, and later a matcher, can be checked
  against a known answer.
- ``records`` is what the sources hold. One event can appear in several records, coded in
  different terminologies, captured later than it happened, present only in free text, or
  wrong. Every deliberate departure from a clean capture is listed in ``quality`` with the
  records and truth event it touches, so nothing in the data is wrong by accident.

Three times are kept apart on every record, because they answer different questions:
``occurred`` (when it happened, as bounds with a precision), ``recorded_at`` (when the
source wrote it down) and ``available_at`` (when a researcher's system could first see it).

The cohort is weighted towards the developmental and epileptic encephalopathies so that
the reference child, a girl with Dravet syndrome, has close matches, near misses and clear
non-matches. Terminology codes are illustrative: they were written from memory to make
coding variation concrete, not taken from a terminology release. Check any code against
its source before reusing it.

    python demos/mockups/synthetic/generate.py            # writes demos/mockups/data/synthetic-paediatric-rare.json
    python demos/mockups/synthetic/generate.py --check    # regenerates and fails if the file differs
"""

import argparse
import hashlib
import json
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261103
SNAPSHOT = date(2026, 6, 30)
OUTPUT = Path(__file__).resolve().parent.parent / 'data' / 'synthetic-paediatric-rare.json'

SOURCES = {
    'ehr': {'label': 'Academic hospital A · structured EHR', 'kind': 'structured'},
    'letters': {'label': 'Academic hospital A · clinic letters', 'kind': 'free_text', 'language': 'en'},
    'referral_nl': {'label': 'Regional hospital B · referral letter', 'kind': 'free_text', 'language': 'nl'},
    'discharge_fr': {'label': 'Hospital C (Belgium) · discharge summary', 'kind': 'free_text', 'language': 'fr'},
    'genetics': {'label': 'Clinical genetics laboratory · reports', 'kind': 'structured'},
    'pharmacy': {'label': 'Community pharmacy · dispensing', 'kind': 'structured'},
    'diary': {'label': 'Parent-held seizure diary app', 'kind': 'patient_reported'},
    'registry': {'label': 'ERN rare-disease registry · ORPHA/HPO coded', 'kind': 'registry'},
    'nbs': {'label': 'Newborn screening programme', 'kind': 'registry'},
}

QUALITY_KINDS = {
    'missing-measurement': ('missing', 'Expected measurement not taken or not recorded'),
    'missing-unit': ('missing', 'Value recorded without a unit'),
    'missing-result': ('missing', 'Test ordered; no result was ever filed'),
    'follow-up-gap': ('missing', 'No contact for a long stretch; events in the gap went unrecorded'),
    'lost-to-follow-up': ('missing', 'Records stop well before the snapshot; later truth is unobserved'),
    'unit-error': ('incorrect', 'Value entered in the wrong unit (pounds typed into a kilogram field)'),
    'decimal-shift': ('incorrect', 'Value entered with the decimal point in the wrong place'),
    'implausible-value': ('incorrect', 'Value inconsistent with the same child\'s neighbouring values'),
    'date-typo': ('incorrect', 'Date entered with a wrong year'),
    'day-month-swap': ('incorrect', 'Day and month transposed in a date'),
    'duplicate-record': ('incorrect', 'The same event recorded twice by different sources'),
    'copy-forward': ('incorrect', 'Text copied from an earlier letter that is no longer true'),
    'wrong-code': ('incorrect', 'Coded as a different, related condition'),
    'misattributed-record': ('incorrect', 'A sibling\'s record filed in this child\'s chart'),
    'approximate-onset': ('temporal', 'Onset reported approximately, as an age or a span'),
    'conflicting-onset': ('temporal', 'Two sources disagree about when it started'),
    'recorded-late': ('temporal', 'Written down long after it happened'),
    'start-unknown': ('temporal', 'Medication start known only from dispensing; no prescription record'),
    'stop-approximate': ('temporal', 'Medication stop date known only approximately'),
    'reported-after-collection': ('temporal', 'Result reported weeks after the sample was taken'),
    'reclassified': ('temporal', 'Genetic variant classification changed after the first report'),
    'corrected-age': ('temporal', 'Born preterm; milestones are judged against corrected age'),
    'granularity': ('coding', 'Coded with a broader parent concept than what happened'),
    'local-code': ('coding', 'Coded only in a local, non-standard code'),
    'terminology-variant': ('coding', 'The same concept coded in several terminologies'),
    'free-text-only': ('coding', 'Present only in free text; no structured record'),
    'negated-mention': ('free_text', 'Free text names the concept to say it is absent'),
    'family-history-mention': ('free_text', 'Free text names the concept for a relative, not the child'),
    'hypothetical-mention': ('free_text', 'Free text names the concept as a plan or possibility'),
    'uncertain-mention': ('free_text', 'Free text names the concept with hedging'),
    'non-english-note': ('free_text', 'Free text in Dutch or French'),
    'misspelling': ('free_text', 'Free text misspells the concept'),
}

CONCEPTS = {}


def concept(cid, label, kind, codings=(), parents=(), classes=()):
    CONCEPTS[cid] = {'label': label, 'kind': kind, 'parents': list(parents), 'classes': list(classes),
                     'codings': [{'system': s, 'code': c, 'display': d} for s, c, d in codings]}


# Phenotypes. HPO for the registry, ICD-10 (WHO) for the hospital, which is coarser.
concept('seizure', 'Seizure', 'phenotype', [('SNOMED CT', '91175000', 'Seizure'), ('HPO', 'HP:0001250', 'Seizure'),
                                            ('ICD-10', 'R56.8', 'Other and unspecified convulsions')])
concept('febrile_seizure', 'Febrile seizure', 'phenotype', [('SNOMED CT', '41497008', 'Febrile convulsion'), ('HPO', 'HP:0002373', 'Febrile seizure'),
                                                            ('ICD-10', 'R56.0', 'Febrile convulsions')], ['seizure'])
concept('status_epilepticus', 'Status epilepticus', 'phenotype',
        [('SNOMED CT', '230456007', 'Status epilepticus'), ('HPO', 'HP:0002133', 'Status epilepticus'), ('ICD-10', 'G41.9', 'Status epilepticus, unspecified')], ['seizure'])
concept('myoclonic_seizure', 'Myoclonic seizure', 'phenotype',
        [('HPO', 'HP:0002123', 'Generalized myoclonic seizure')], ['seizure'])
concept('epileptic_spasm', 'Epileptic spasms', 'phenotype', [('HPO', 'HP:0011097', 'Epileptic spasm')], ['seizure'])
concept('seizure_cluster', 'Seizure cluster', 'phenotype', [('local', 'SZ-CLUSTER', 'Seizure cluster')], ['seizure'])
concept('dev_delay', 'Global developmental delay', 'phenotype',
        [('SNOMED CT', '248290002', 'Developmental delay'), ('HPO', 'HP:0001263', 'Global developmental delay'), ('ICD-10', 'R62.0', 'Delayed milestone')])
concept('dev_regression', 'Developmental regression', 'phenotype', [('HPO', 'HP:0002376', 'Developmental regression')])
concept('hypotonia', 'Hypotonia', 'phenotype', [('SNOMED CT', '398152000', 'Poor muscle tone'), ('HPO', 'HP:0001252', 'Hypotonia'),
                                                ('ICD-10', 'P94.2', 'Congenital hypotonia')])
concept('ataxia', 'Ataxia', 'phenotype', [('SNOMED CT', '20262006', 'Ataxia'), ('HPO', 'HP:0001251', 'Ataxia'), ('ICD-10', 'R27.0', 'Ataxia, unspecified')])
concept('autistic_behavior', 'Autistic behaviour', 'phenotype', [('HPO', 'HP:0000729', 'Autistic behavior')])
concept('motor_stereotypy', 'Hand stereotypies', 'phenotype', [('HPO', 'HP:0000733', 'Motor stereotypy')])
concept('loss_hand_use', 'Loss of purposeful hand use', 'phenotype', [('local', 'NEURO-HAND', 'Loss of hand use')])
concept('cvi', 'Cerebral visual impairment', 'phenotype', [('HPO', 'HP:0100704', 'Cerebral visual impairment')])
concept('hypomelanotic_macule', 'Hypomelanotic macules', 'phenotype', [('HPO', 'HP:0009719', 'Hypomelanotic macule')])
concept('cardiac_rhabdomyoma', 'Cardiac rhabdomyoma', 'phenotype', [('HPO', 'HP:0009729', 'Cardiac rhabdomyoma')])
concept('feeding_difficulty', 'Feeding difficulties', 'phenotype', [('HPO', 'HP:0011968', 'Feeding difficulties')])
concept('resp_insufficiency', 'Respiratory insufficiency', 'phenotype',
        [('HPO', 'HP:0002093', 'Respiratory insufficiency')])
concept('asthma', 'Asthma', 'phenotype', [('SNOMED CT', '195967001', 'Asthma'), ('ICD-10', 'J45.9', 'Asthma, unspecified')])

# Diagnoses. The hospital's ICD-10 (WHO) has no Dravet code, so it can only say G40.4.
concept('epilepsy', 'Epilepsy', 'diagnosis', [('SNOMED CT', '84757009', 'Epilepsy'), ('ICD-10', 'G40.9', 'Epilepsy, unspecified')])
concept('generalized_epilepsy', 'Generalised epilepsy syndrome', 'diagnosis',
        [('ICD-10', 'G40.4', 'Other generalized epilepsy and epileptic syndromes')], ['epilepsy'])
concept('idiopathic_generalized_epilepsy', 'Idiopathic generalised epilepsy', 'diagnosis',
        [('ICD-10', 'G40.3', 'Generalized idiopathic epilepsy and epileptic syndromes')], ['epilepsy'])
concept('dee', 'Developmental and epileptic encephalopathy', 'diagnosis', [], ['epilepsy'])
concept('dravet', 'Dravet syndrome', 'diagnosis',
        [('ORPHA', 'ORPHA:33069', 'Dravet syndrome'), ('OMIM', '607208', 'Developmental and epileptic encephalopathy 6A'),
         ('ICD-10-CM', 'G40.83', 'Dravet syndrome'), ('local', 'DX-DRAVET', 'Dravet')], ['dee', 'generalized_epilepsy'])
concept('gefs_plus', 'Genetic epilepsy with febrile seizures plus', 'diagnosis',
        [('OMIM', '604403', 'Generalized epilepsy with febrile seizures plus, type 2'), ('local', 'DX-GEFS', 'GEFS+')],
        ['epilepsy'])
concept('simple_febrile_seizures', 'Simple febrile seizures', 'diagnosis',
        [('ICD-10-CM', 'R56.00', 'Simple febrile convulsions'), ('local', 'DX-FS', 'Febrile seizures')])
concept('pcdh19', 'PCDH19 clustering epilepsy', 'diagnosis',
        [('OMIM', '300088', 'Developmental and epileptic encephalopathy 9')], ['dee'])
concept('cdkl5', 'CDKL5 deficiency disorder', 'diagnosis',
        [('OMIM', '300672', 'Developmental and epileptic encephalopathy 2')], ['dee'])
concept('tsc', 'Tuberous sclerosis complex', 'diagnosis',
        [('SNOMED CT', '7199000', 'Tuberous sclerosis syndrome'), ('ORPHA', 'ORPHA:805', 'Tuberous sclerosis complex'), ('ICD-10', 'Q85.1', 'Tuberous sclerosis')])
concept('rett', 'Rett syndrome', 'diagnosis',
        [('SNOMED CT', '68618008', 'Rett syndrome'), ('ORPHA', 'ORPHA:778', 'Rett syndrome'), ('ICD-10', 'F84.2', "Rett's syndrome"), ('OMIM', '312750', 'Rett syndrome')])
concept('sma', 'Spinal muscular atrophy', 'diagnosis',
        [('ORPHA', 'ORPHA:70', 'Proximal spinal muscular atrophy'), ('ICD-10', 'G12.0', 'Infantile spinal muscular atrophy, type I'),
         ('OMIM', '253300', 'Spinal muscular atrophy, type I')])

# Genes, as HGNC symbols.
for gene in ['SCN1A', 'PCDH19', 'CDKL5', 'TSC1', 'TSC2', 'MECP2', 'SMN1']:
    concept(f'gene:{gene}', gene, 'gene', [('HGNC', gene, gene)])

# Medications, ATC. The sodium-channel blockers are named as a class because they are the
# exposure of interest: in Dravet syndrome they are expected to make seizures worse.
concept('sodium_channel_blocker', 'Sodium-channel-blocking antiseizure medication', 'drug_class')
concept('antiseizure', 'Antiseizure medication', 'drug_class')
NCB = ['sodium_channel_blocker', 'antiseizure']
ASM = ['antiseizure']
for cid, label, atc, classes in [
        ('valproate', 'Valproic acid', 'N03AG01', ASM), ('clobazam', 'Clobazam', 'N05BA09', ASM),
        ('stiripentol', 'Stiripentol', 'N03AX17', ASM), ('fenfluramine', 'Fenfluramine', 'N03AX26', ASM),
        ('cannabidiol', 'Cannabidiol', 'N03AX24', ASM), ('topiramate', 'Topiramate', 'N03AX11', ASM),
        ('levetiracetam', 'Levetiracetam', 'N03AX14', ASM), ('vigabatrin', 'Vigabatrin', 'N03AG04', ASM),
        ('carbamazepine', 'Carbamazepine', 'N03AF01', NCB), ('oxcarbazepine', 'Oxcarbazepine', 'N03AF02', NCB),
        ('lamotrigine', 'Lamotrigine', 'N03AX09', NCB), ('phenytoin', 'Phenytoin', 'N03AB02', NCB),
        ('prednisolone', 'Prednisolone', 'H02AB06', []), ('midazolam', 'Midazolam (rescue)', 'N05CD08', []),
        ('nusinersen', 'Nusinersen', 'M09AX07', []), ('onasemnogene', 'Onasemnogene abeparvovec', 'M09AX09', []),
        ('salbutamol', 'Salbutamol', 'R03AC02', [])]:
    concept(cid, label, 'medication', [('ATC', atc, label)], classes=classes)
concept('everolimus', 'Everolimus', 'medication', [('local', 'MED-EVEROLIMUS', 'Everolimus')])

# Measurements, LOINC; the diary's seizure count has no standard code.
concept('weight', 'Body weight', 'measurement', [('LOINC', '29463-7', 'Body weight')])
concept('height', 'Body height', 'measurement', [('LOINC', '8302-2', 'Body height')])
concept('head_circ', 'Head circumference', 'measurement', [('LOINC', '9843-4', 'Head Occipital-frontal circumference')])
concept('seizure_frequency', 'Seizures per month', 'measurement', [('local', 'DIARY-SZ-MONTH', 'Seizures this month')])

# Procedures and tests.
concept('eeg', 'EEG', 'procedure', [('local', 'PROC-EEG', 'Electroencephalogram')])
concept('mri_brain', 'MRI brain', 'procedure', [('local', 'PROC-MRI-BRAIN', 'MRI brain')])
concept('echo', 'Echocardiogram', 'procedure', [('local', 'PROC-ECHO', 'Echocardiogram')])
concept('genetic_test', 'Genetic test', 'genetic', [('local', 'LAB-GEN', 'Genetic test')])

UNITS = {'weight': 'kg', 'height': 'cm', 'head_circ': 'cm', 'seizure_frequency': '/month'}
PHRASES = {
    'febrile_seizure': ['a febrile seizure', 'a prolonged febrile convulsion', 'a seizure with fever'],
    'seizure': ['an afebrile generalised seizure', 'a seizure without fever', 'a tonic-clonic seizure'],
    'status_epilepticus': ['status epilepticus', 'a prolonged seizure needing two doses of midazolam'],
    'myoclonic_seizure': ['myoclonic jerks', 'myoclonic seizures'],
    'epileptic_spasm': ['clusters of epileptic spasms', 'infantile spasms'],
    'seizure_cluster': ['a cluster of seizures over two days', 'clustered seizures with fever'],
    'dev_delay': ['global developmental delay', 'delayed milestones'],
    'dev_regression': ['loss of previously acquired skills', 'developmental regression'],
    'hypotonia': ['marked hypotonia', 'low tone'],
    'ataxia': ['an unsteady, ataxic gait', 'ataxia'],
    'autistic_behavior': ['autistic features', 'reduced social interaction with repetitive behaviours'],
    'motor_stereotypy': ['hand-wringing stereotypies'],
    'loss_hand_use': ['loss of purposeful hand use'],
    'cvi': ['cerebral visual impairment'],
    'hypomelanotic_macule': ['several hypomelanotic macules', 'ash-leaf macules'],
    'cardiac_rhabdomyoma': ['cardiac rhabdomyomas'],
    'feeding_difficulty': ['feeding difficulties', 'poor feeding'],
    'resp_insufficiency': ['respiratory insufficiency needing non-invasive support'],
}


def iso(moment):
    return moment.strftime('%Y-%m-%dT%H:%M')


def months(day):
    return day / 30.44


def age_text(day):
    if day < 60:
        return f'{day} days'
    if day < 730:
        return f'{round(months(day))} months'
    years, rest = divmod(day, 365)
    m = round(rest / 30.44)
    return f'{years} years' if m in (0, 12) else f'{years} years {m} months'


def growth(cid, day, z, head_brake=0.0):
    """Rough median growth curves, enough to make plausible and implausible values legible."""
    m = months(max(day, 0))
    if cid == 'weight':
        base = 3.4 + 0.7 * m - 0.017 * m * m if m <= 12 else (
            9.35 + 0.2 * (m - 12) if m <= 24 else 11.75 + (m - 24) / 12 * 2.0)
        return round(base * (1 + 0.07 * z), 2)
    if cid == 'height':
        base = 50 + 3.0 * m - 0.085 * m * m if m <= 12 else (
            73.8 + 1.0 * (m - 12) if m <= 24 else 85.8 + (m - 24) / 12 * 6.5)
        return round(base * (1 + 0.035 * z), 1)
    gain = 12 * (1 - math.exp(-m / 8)) + 0.05 * m
    if head_brake and m > 6:
        gain -= head_brake * (12 * (1 - math.exp(-(m - 6) / 8)))
    return round((35 + gain) * (1 + 0.03 * z), 1)


class Child:
    def __init__(self, pid, key, archetype, rng, sex, birth, scripted=False, ga_weeks=39):
        self.pid, self.key, self.archetype, self.rng = pid, key, archetype, rng
        self.sex, self.birth, self.ga, self.scripted = sex, birth, ga_weeks, scripted
        self.truth, self.records, self.quality = [], [], []
        self.record_seq = 0  # never decremented: records are removed later, and ids must not be reused
        self.visits, self.encounters = [], {}
        self.snapshot_day = (SNAPSHOT - birth).days
        self.last_contact = self.snapshot_day - rng.randint(5, 70)
        self.gap = None
        self.diary_from = None
        self.registry = rng.random() < 0.6
        self.family_history = None
        self.z = rng.gauss(0, 0.8)
        self.head_brake = 0.0
        self.referral = None

    # Truth
    def event(self, cid, day, **kw):
        ev = {'event_id': f'{self.pid}-T{len(self.truth) + 1:03d}', 'concept': cid,
              'kind': CONCEPTS[cid]['kind'], 'day': int(day)}
        ev.update({k: v for k, v in kw.items() if v is not None})
        self.truth.append(ev)
        return ev

    def on(self, day):
        return self.birth + timedelta(days=int(day))

    def moment(self, day, hour=None, minute=None):
        hour = self.rng.randint(8, 21) if hour is None else hour
        minute = self.rng.randint(0, 59) if minute is None else minute
        return datetime.combine(self.on(day), datetime.min.time()) + timedelta(hours=hour, minutes=minute)

    # Records
    def record(self, source, kind, truth=None, **kw):
        self.record_seq += 1
        rec = {'record_id': f'{self.pid}-R{self.record_seq:04d}', 'source': source, 'kind': kind,
               'truth_event_id': truth['event_id'] if truth else None}
        rec.update({k: v for k, v in kw.items() if v is not None})
        self.records.append(rec)
        return rec

    def case(self, kind, records=(), truth=None, detail=''):
        assert kind in QUALITY_KINDS, kind
        self.quality.append({'case_id': f'{self.pid}-Q{len(self.quality) + 1:03d}', 'kind': kind,
                             'record_ids': [r['record_id'] for r in records],
                             'truth_event_id': truth['event_id'] if truth else None, 'detail': detail})

    def exact(self, day, hour=None, minute=None):
        when = iso(self.moment(day, hour, minute))
        return {'earliest': when, 'latest': when, 'precision': 'minute', 'as_written': when.replace('T', ' ')}

    def on_day(self, day, as_written=None):
        d = self.on(day)
        return {'earliest': f'{d}T00:00', 'latest': f'{d}T23:59', 'precision': 'day',
                'as_written': as_written or d.strftime('%d-%m-%Y')}

    def around(self, day, spread, as_written):
        lo, hi = self.on(day - spread), self.on(day + spread)
        return {'earliest': f'{lo}T00:00', 'latest': f'{hi}T23:59', 'precision': 'approximate', 'as_written': as_written}

    def in_month(self, day):
        d = self.on(day)
        first = d.replace(day=1)
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        return {'earliest': f'{first}T00:00', 'latest': f'{last}T23:59', 'precision': 'month',
                'as_written': d.strftime('%B %Y')}

    def next_visit(self, day):
        later = [v for v in self.visits if v >= day]
        return later[0] if later else None

    def observed(self, day):
        if day > self.last_contact:
            return False
        return not (self.gap and self.gap[0] <= day <= self.gap[1])

    def coding(self, cid, system):
        for c in CONCEPTS[cid]['codings']:
            if c['system'] == system:
                return dict(c)
        return None


def pick(rng, items):
    return items[rng.randrange(len(items))]


# Archetypes: each writes a truth timeline in days of age.

def seizure_frequency(child, start, base, peak_month, meds, until):
    """Monthly seizure counts shaped by age and by the medications active that month.

    Sodium-channel blockers aggravate seizures in Dravet syndrome specifically; elsewhere in
    this cohort they are modelled as modestly effective, so a rise after exposure is a
    signal of the syndrome rather than of the drug.
    """
    aggravates = child.archetype == 'dravet'
    effect = {'valproate': 0.8, 'clobazam': 0.8, 'stiripentol': 0.5, 'fenfluramine': 0.4, 'cannabidiol': 0.75,
              'topiramate': 0.85, 'levetiracetam': 0.85, 'vigabatrin': 0.5, 'prednisolone': 0.6,
              'carbamazepine': 2.6, 'oxcarbazepine': 2.4, 'lamotrigine': 2.2, 'phenytoin': 2.0}
    if not aggravates:
        effect.update(carbamazepine=0.85, oxcarbazepine=0.85, lamotrigine=0.85, phenytoin=0.85)
    day = start
    while day <= until:
        m = months(day)
        rate = base * (0.6 + 0.8 * math.exp(-((m - peak_month) / 14) ** 2))
        for med in meds:
            if med['day'] <= day <= (med.get('end_day') or 10 ** 6):
                rate *= effect.get(med['concept'], 1.0)
        child.event('seizure_frequency', day, value=round(rate, 1), unit='/month')
        day += 30


def dravet(c, o):
    r = c.rng
    onset = o.get('onset', r.randint(140, 260))
    fs = c.event('febrile_seizure', onset, setting=o.get('first_setting', 'ed'),
                 duration_min=o.get('first_duration', pick(r, [15, 20, 25, 35])), side=pick(r, ['right', 'left']))
    day = onset
    for gap in o.get('repeat_gaps', [r.randint(25, 70) for _ in range(r.randint(1, 2))]):
        day += gap
        c.event('febrile_seizure', day, setting='home', duration_min=pick(r, [3, 5, 8]))
    se_day = o.get('se', onset + r.randint(50, 160) if r.random() < 0.75 else None)
    if se_day:
        c.event('status_epilepticus', se_day, setting='admission', duration_min=pick(r, [40, 45, 55, 70]))
    afebrile = o.get('afebrile', onset + r.randint(120, 220))
    c.event('seizure', afebrile, setting='home')
    if o.get('myoclonic', r.random() < 0.7):
        c.event('myoclonic_seizure', o.get('myoclonic_day', r.randint(420, 840)), setting='clinic')
    if r.random() < 0.9 or 'delay' in o:
        c.event('dev_delay', o.get('delay', r.randint(540, 1000)), setting='clinic')
    if o.get('ataxia', r.random() < 0.5):
        c.event('ataxia', o.get('ataxia_day', r.randint(950, 1700)), setting='clinic')
    if r.random() < 0.3:
        c.event('autistic_behavior', r.randint(950, 1800), setting='clinic')

    meds = []
    valproate = c.event('valproate', o.get('valproate', (se_day or onset + 60) + 3), end_day=None)
    meds.append(valproate)
    collected = o.get('genetic_collected', onset + r.randint(80, 330))
    reported = o.get('genetic_reported', collected + r.randint(45, 100))
    ncb_day = o.get('ncb_day')
    if ncb_day is None and o.get('ncb', r.random() < 0.4):
        ncb_day = r.randint(onset + 90, reported - 5) if reported - 5 > onset + 90 else onset + 90
    if ncb_day:
        drug = o.get('ncb_drug', pick(r, ['carbamazepine', 'oxcarbazepine', 'lamotrigine']))
        meds.append(c.event(drug, ncb_day, end_day=ncb_day + o.get('ncb_days', r.randint(30, 70)),
                            stop_reason='seizures worsened', prescribed_by=o.get('ncb_by')))
    meds.append(c.event('clobazam', o.get('clobazam', valproate['day'] + r.randint(80, 220))))
    result = o.get('result', 'pathogenic' if r.random() < 0.85 else 'negative')
    hgvs = f'c.{r.randint(300, 5900)}{pick(r, ["C>T", "G>A", "delA", "dupT"])}'
    genetic = c.event('genetic_test', collected, gene='SCN1A', method='Epilepsy gene panel', collected_day=collected,
                      reported_day=reported, hgvs=hgvs if result != 'negative' else None,
                      classification={'pathogenic': 'pathogenic', 'vus': 'uncertain significance',
                                      'negative': 'no relevant variant'}[result],
                      reclassified_day=o.get('reclassified_day'),
                      reclassified_to='pathogenic' if o.get('reclassified_day') else None,
                      result_missing=o.get('result_missing'))
    confirm = genetic.get('reclassified_day') or (reported if result == 'pathogenic' else r.randint(600, 900))
    c.event('dravet', confirm + o.get('dx_lag', r.randint(0, 30)), basis='genetic' if result != 'negative' else 'clinical',
            provisional_day=o.get('provisional_day'))
    meds.append(c.event('stiripentol', o.get('stiripentol', confirm + r.randint(30, 140))))
    if o.get('fenfluramine', r.random() < 0.5):
        meds.append(c.event('fenfluramine', o.get('fenfluramine_day', r.randint(1100, 2000))))
    c.event('eeg', onset + r.randint(3, 15), result='Normal background; no epileptiform discharges.')
    c.event('eeg', r.randint(500, 900), result='Generalised and multifocal epileptiform discharges; photosensitivity.')
    seizure_frequency(c, onset + 30, o.get('rate', r.uniform(3, 8)), 18, meds, c.snapshot_day)
    if o.get('diary', r.random() < 0.7):
        c.diary_from = o.get('diary_from', onset + r.randint(20, 90))
    c.family_history = o.get('family', pick(r, [None, None, ('maternal cousin', 'febrile_seizure')]))
    return fs


def gefs_plus(c, o):
    r = c.rng
    onset = r.randint(180, 900)
    first = c.event('febrile_seizure', onset, setting=pick(r, ['ed', 'home']), duration_min=pick(r, [3, 5, 10]))
    day = onset
    while day < min(2400, c.snapshot_day):
        day += r.randint(120, 400)
        c.event('febrile_seizure', day, setting='home', duration_min=pick(r, [2, 4, 6]))
    meds = []
    if r.random() < 0.6:
        c.event('seizure', r.randint(1100, 2600), setting='home')
        meds.append(c.event(pick(r, ['valproate', 'levetiracetam']), r.randint(1200, 2700)))
    if r.random() < 0.5:
        collected = onset + r.randint(200, 700)
        positive = r.random() < 0.45
        c.event('genetic_test', collected, gene='SCN1A', method='Epilepsy gene panel', collected_day=collected,
                reported_day=collected + r.randint(40, 90), classification='pathogenic' if positive else 'no relevant variant',
                hgvs=f'c.{r.randint(300, 5900)}G>A' if positive else None)
    c.event('gefs_plus', onset + r.randint(400, 1200))
    seizure_frequency(c, onset + 30, 0.4, 30, meds, min(c.snapshot_day, 2600))
    c.family_history = ('father', 'febrile_seizure') if r.random() < 0.6 else ('older brother', 'seizure')
    return first


def febrile_only(c, o):
    r = c.rng
    onset = r.randint(200, 1500)
    first = c.event('febrile_seizure', onset, setting=pick(r, ['ed', 'ed', 'home']), duration_min=pick(r, [2, 3, 5]))
    for _ in range(r.randint(0, 2)):
        c.event('febrile_seizure', onset + r.randint(90, 500), setting='home', duration_min=pick(r, [2, 4]))
    if r.random() < 0.4:
        c.event('midazolam', onset + 1, end_day=None)
    c.event('simple_febrile_seizures', onset + r.randint(0, 60))
    if r.random() < 0.3:
        c.event('eeg', onset + r.randint(10, 40), result='Normal.')
    c.last_contact = min(c.last_contact, onset + r.randint(500, 1200))
    return first


def pcdh19(c, o):
    r = c.rng
    onset = r.randint(200, 1000)
    first = c.event('seizure_cluster', onset, setting='admission', with_fever=True)
    day = onset
    meds = [c.event('clobazam', onset + r.randint(5, 40)), c.event('levetiracetam', onset + r.randint(60, 200))]
    while day < c.snapshot_day - 200:
        day += r.randint(90, 240)
        c.event('seizure_cluster', day, setting=pick(r, ['home', 'admission']), with_fever=r.random() < 0.6)
    if r.random() < 0.25:
        drug = pick(r, ['oxcarbazepine', 'lamotrigine'])
        meds.append(c.event(drug, onset + r.randint(300, 700), end_day=None))
    if r.random() < 0.6:
        c.event('dev_delay', onset + r.randint(200, 800), setting='clinic')
    if r.random() < 0.5:
        c.event('autistic_behavior', r.randint(1000, 2000), setting='clinic')
    collected = onset + r.randint(30, 300)
    c.event('genetic_test', collected, gene='PCDH19', method='Epilepsy gene panel', collected_day=collected,
            reported_day=collected + r.randint(40, 100), classification='pathogenic',
            hgvs=f'c.{r.randint(100, 3000)}C>T')
    c.event('pcdh19', collected + r.randint(60, 140))
    seizure_frequency(c, onset + 30, 2.5, 20, meds, c.snapshot_day)
    if r.random() < 0.6:
        c.diary_from = onset + r.randint(20, 90)
    return first


def cdkl5(c, o):
    r = c.rng
    onset = r.randint(14, 75)
    first = c.event('seizure', onset, setting='admission')
    c.event('hypotonia', r.randint(20, 120), setting='clinic')
    c.event('epileptic_spasm', r.randint(90, 200), setting='admission')
    c.event('dev_delay', r.randint(150, 300), setting='clinic')
    c.event('cvi', r.randint(150, 400), setting='clinic')
    meds = [c.event('levetiracetam', onset + 2), c.event('vigabatrin', r.randint(100, 210)),
            c.event('prednisolone', r.randint(110, 230), end_day=None)]
    meds[-1]['end_day'] = meds[-1]['day'] + 28
    if r.random() < 0.4:
        drug = pick(r, ['carbamazepine', 'phenytoin'])
        d = r.randint(60, 300)
        meds.append(c.event(drug, d, end_day=d + r.randint(30, 120)))
    collected = onset + r.randint(20, 120)
    c.event('genetic_test', collected, gene='CDKL5', method='Epilepsy gene panel', collected_day=collected,
            reported_day=collected + r.randint(40, 100), classification='pathogenic', hgvs=f'c.{r.randint(100, 2800)}delA')
    c.event('cdkl5', collected + r.randint(50, 130))
    c.event('eeg', onset + 3, result='Multifocal epileptiform discharges.')
    seizure_frequency(c, onset + 30, 25, 8, meds, c.snapshot_day)
    if r.random() < 0.6:
        c.diary_from = onset + r.randint(20, 60)
    return first


def tsc(c, o):
    r = c.rng
    first = c.event('cardiac_rhabdomyoma', r.randint(0, 3), setting='admission')
    c.event('echo', first['day'], result='Multiple ventricular rhabdomyomas; no outflow obstruction.')
    c.event('hypomelanotic_macule', r.randint(60, 300), setting='clinic')
    meds = []
    if r.random() < 0.75:
        spasm = r.randint(100, 260)
        c.event('epileptic_spasm', spasm, setting='admission')
        meds.append(c.event('vigabatrin', spasm + r.randint(1, 10)))
        if r.random() < 0.5:
            c.event('dev_delay', r.randint(400, 900), setting='clinic')
        seizure_frequency(c, spasm + 30, 6, 10, meds, c.snapshot_day)
        if r.random() < 0.5:
            c.diary_from = spasm + r.randint(20, 60)
    c.event('mri_brain', r.randint(30, 200), result='Multiple cortical tubers and subependymal nodules.')
    if r.random() < 0.4:
        meds.append(c.event('everolimus', r.randint(700, 1600)))
    gene = 'TSC2' if r.random() < 0.7 else 'TSC1'
    collected = r.randint(20, 200)
    c.event('genetic_test', collected, gene=gene, method='Single-gene test', collected_day=collected,
            reported_day=collected + r.randint(40, 100), classification='pathogenic', hgvs=f'c.{r.randint(100, 5000)}C>T')
    c.event('tsc', r.randint(30, 150))
    return first


def rett(c, o):
    r = c.rng
    reg = r.randint(400, 900)
    first = c.event('dev_regression', reg, setting='clinic')
    c.event('loss_hand_use', reg + r.randint(0, 120), setting='clinic')
    c.event('motor_stereotypy', reg + r.randint(30, 200), setting='clinic')
    c.head_brake = r.uniform(0.25, 0.45)
    meds = []
    if r.random() < 0.5:
        s = r.randint(800, 1800)
        c.event('seizure', s, setting='home')
        meds.append(c.event(pick(r, ['levetiracetam', 'valproate']), s + r.randint(10, 60)))
        seizure_frequency(c, s + 30, 1.5, 40, meds, c.snapshot_day)
    collected = reg + r.randint(60, 300)
    c.event('genetic_test', collected, gene='MECP2', method='Single-gene test', collected_day=collected,
            reported_day=collected + r.randint(30, 80), classification='pathogenic', hgvs=f'c.{r.randint(300, 1200)}C>T')
    c.event('rett', collected + r.randint(40, 120))
    return first


def sma(c, o):
    r = c.rng
    screened = c.birth >= date(2022, 10, 1)
    if screened:
        first = c.event('genetic_test', r.randint(3, 6), gene='SMN1', method='Newborn screening, confirmed by SMN1 copy number',
                        collected_day=3, reported_day=r.randint(9, 16), classification='homozygous deletion', screened=True)
        c.event(pick(r, ['nusinersen', 'onasemnogene']), r.randint(18, 40))
        c.event('sma', first['reported_day'] + r.randint(1, 5))
        if r.random() < 0.3:
            c.event('hypotonia', r.randint(200, 400), setting='clinic')
    else:
        first = c.event('hypotonia', r.randint(40, 130), setting='clinic')
        c.event('feeding_difficulty', first['day'] + r.randint(10, 60), setting='clinic')
        if r.random() < 0.6:
            c.event('resp_insufficiency', first['day'] + r.randint(40, 150), setting='admission')
        collected = first['day'] + r.randint(10, 40)
        c.event('genetic_test', collected, gene='SMN1', method='SMN1 copy number', collected_day=collected,
                reported_day=collected + r.randint(7, 20), classification='homozygous deletion')
        c.event('sma', collected + r.randint(10, 30))
        c.event(pick(r, ['nusinersen', 'onasemnogene']), collected + r.randint(25, 60))
    c.z -= 1.0
    return first


ARCHETYPES = {'dravet': dravet, 'gefs_plus': gefs_plus, 'febrile_only': febrile_only, 'pcdh19': pcdh19,
              'cdkl5': cdkl5, 'tsc': tsc, 'rett': rett, 'sma': sma}


# Capture: turns truth into records. Random defects apply unless the child is scripted.

def schedule_visits(c, first_day):
    r = c.rng
    day = first_day + r.randint(7, 25)
    while day <= c.last_contact:
        if c.observed(day):
            c.visits.append(day)
        day += r.randint(60, 140)
    for ev in c.truth:
        if ev.get('setting') in ('ed', 'admission') and not ev.get('elsewhere') and c.observed(ev['day']):
            c.encounters[ev['day']] = ev.get('setting')


def ehr_condition(c, ev, coding, day_recorded=None, occurred=None, as_label=None):
    recorded = c.moment(day_recorded if day_recorded is not None else ev['day'])
    return c.record('ehr', 'condition', ev, coding=coding, label_as_recorded=as_label or (coding or {}).get('display'),
                    occurred=occurred or c.on_day(ev['day']), recorded_at=iso(recorded),
                    available_at=iso(recorded + timedelta(hours=2)), status='active')


def registry_entry(c, ev, coding, day):
    export = date(c.on(day).year + 1, 1, 31)
    return c.record('registry', 'registry_entry', ev, coding=coding, label_as_recorded=coding['display'],
                    occurred=c.in_month(ev['day']), recorded_at=iso(c.moment(day)),
                    available_at=f'{export}T06:00')


def capture_phenotype(c, ev):
    r = c.rng
    cid = ev['concept']
    if not c.observed(ev['day']):
        return
    acute = ev.get('setting') in ('ed', 'admission') and not ev.get('elsewhere')
    visit = ev['day'] if acute else c.next_visit(ev['day'])
    if visit is None:
        return
    if acute:
        occurred = c.exact(ev['day'])
    elif ev.get('elsewhere'):
        occurred = c.on_day(ev['day'], f'{c.on(ev["day"]):%d-%m-%Y} (per referral letter)')
    elif visit - ev['day'] <= 14:
        occurred = c.on_day(ev['day'])
    else:
        weeks = round((visit - ev['day']) / 7)
        occurred = c.around(ev['day'], max(7, (visit - ev['day']) // 4),
                            f'about {weeks} weeks ago' if weeks < 10 else f'at around {age_text(ev["day"])}')
    coded = r.random() < (0.9 if acute else 0.75) or c.scripted
    if coded:
        coding = c.coding(cid, 'ICD-10')
        detail = None
        if coding is None:
            parent = next((p for p in CONCEPTS[cid]['parents'] if c.coding(p, 'ICD-10')), None)
            if parent:
                coding, detail = c.coding(parent, 'ICD-10'), f'{CONCEPTS[cid]["label"]} coded as {CONCEPTS[parent]["label"]}'
            else:
                coding = c.coding(cid, 'local') or {'system': 'local', 'code': 'OBS-' + cid.upper()[:10],
                                                    'display': CONCEPTS[cid]['label']}
        rec = ehr_condition(c, ev, coding, visit, occurred)
        if detail:
            c.case('granularity', [rec], ev, detail)
        elif coding['system'] == 'local':
            c.case('local-code', [rec], ev, f'{CONCEPTS[cid]["label"]} recorded with local code {coding["code"]}')
        if occurred['precision'] == 'approximate' and not c.scripted:
            c.case('approximate-onset', [rec], ev, f'Reported as "{occurred["as_written"]}"')
        if not acute and visit - ev['day'] > 60:
            c.case('recorded-late', [rec], ev, f'Recorded {visit - ev["day"]} days after it happened')
        if acute and cid.endswith('seizure') and r.random() < 0.08 and not c.scripted:
            dup = c.record('ehr', 'condition', ev, coding=c.coding('seizure', 'ICD-10'),
                           label_as_recorded='Convulsions', occurred=c.on_day(ev['day']),
                           recorded_at=iso(c.moment(ev['day'] + 1)), available_at=iso(c.moment(ev['day'] + 1)),
                           status='active')
            c.case('duplicate-record', [rec, dup], ev, 'Entered again the next day by the ward, less specifically')
    else:
        ev['_free_text_only'] = True
    hpo = c.coding(cid, 'HPO')
    if c.registry and hpo:
        registry_entry(c, ev, hpo, visit)
    if c.diary_from is not None and cid in ('seizure', 'febrile_seizure', 'status_epilepticus', 'seizure_cluster',
                                             'myoclonic_seizure') and ev['day'] >= c.diary_from:
        c.record('diary', 'diary_entry', ev, label_as_recorded={'febrile_seizure': 'Fit with fever',
                                                                'status_epilepticus': 'Long fit (ambulance)'}.get(cid, 'Fit'),
                 occurred=c.on_day(ev['day']), recorded_at=iso(c.moment(ev['day'], 21)),
                 available_at=iso(c.moment(c.next_visit(ev['day']) or ev['day'], 9)))


def capture_diagnosis(c, ev):
    r = c.rng
    cid = ev['concept']
    if not c.observed(ev['day']):
        return
    visit = c.next_visit(ev['day']) or ev['day']
    records = []
    if ev.get('_free_text_only'):
        return
    if cid == 'dravet':
        style = ev.get('_ehr_style') or pick(r, ['coarse', 'coarse', 'local', 'cm'])
        if style == 'coarse':
            rec = ehr_condition(c, ev, c.coding('generalized_epilepsy', 'ICD-10'), visit)
            c.case('granularity', [rec], ev, 'Dravet syndrome coded as ICD-10 G40.4; WHO ICD-10 has no narrower code')
        elif style == 'wrong':
            rec = ehr_condition(c, ev, c.coding('idiopathic_generalized_epilepsy', 'ICD-10'), visit)
            c.case('wrong-code', [rec], ev, 'Dravet syndrome coded as idiopathic generalised epilepsy (G40.3)')
        elif style == 'local':
            rec = ehr_condition(c, ev, c.coding('dravet', 'local'), visit)
            c.case('local-code', [rec], ev, 'Dravet syndrome recorded with local code DX-DRAVET')
        else:
            rec = ehr_condition(c, ev, c.coding('dravet', 'ICD-10-CM'), visit)
        records.append(rec)
    else:
        coding = c.coding(cid, 'ICD-10') or c.coding(cid, 'ICD-10-CM') or c.coding(cid, 'OMIM') or c.coding(cid, 'local')
        records.append(ehr_condition(c, ev, coding, visit))
    if c.registry and c.coding(cid, 'ORPHA'):
        records.append(registry_entry(c, ev, c.coding(cid, 'ORPHA'), visit))
    if c.coding(cid, 'OMIM') and ev.get('basis', 'genetic') == 'genetic' and cid not in ('gefs_plus',):
        records.append(c.record('genetics', 'interpretation', ev, coding=c.coding(cid, 'OMIM'),
                                label_as_recorded=c.coding(cid, 'OMIM')['display'], occurred=c.on_day(ev['day']),
                                recorded_at=iso(c.moment(ev['day'])), available_at=iso(c.moment(ev['day'] + 5))))
    systems = {rec['coding']['system'] for rec in records if rec.get('coding')}
    if len(systems) > 1:
        c.case('terminology-variant', records, ev, 'Coded as ' + ', '.join(
            f'{rec["coding"]["system"]} {rec["coding"]["code"]}' for rec in records))


def capture_genetic(c, ev):
    r = c.rng
    collected, reported = ev['collected_day'], ev['reported_day']
    if not c.observed(collected):
        return
    order = c.record('ehr', 'test_order', ev, coding=c.coding('genetic_test', 'local'),
                     label_as_recorded=ev['method'], occurred=c.on_day(collected), recorded_at=iso(c.moment(collected)),
                     available_at=iso(c.moment(collected)), status='ordered', attributes={'gene_requested': ev['gene']})
    if ev.get('result_missing'):
        c.case('missing-result', [order], ev, f'{ev["method"]} ordered; no report filed')
        return
    if ev.get('screened'):
        c.record('nbs', 'screening_result', ev, label_as_recorded='Heel-prick screen: SMN1 absent',
                 occurred=c.on_day(collected), recorded_at=iso(c.moment(collected + 4)),
                 available_at=iso(c.moment(collected + 4)), attributes={'gene': 'SMN1', 'result': 'screen positive'})
    result = c.record('genetics', 'genetic_result', ev, coding=c.coding(f'gene:{ev["gene"]}', 'HGNC'),
                      label_as_recorded=f'{ev["gene"]}: {ev["classification"]}', occurred=c.on_day(collected),
                      recorded_at=iso(c.moment(reported)), available_at=iso(c.moment(reported + r.randint(2, 12))),
                      status='final', attributes={'gene': ev['gene'], 'hgvs': ev.get('hgvs'),
                                                  'classification': ev['classification'], 'method': ev['method'],
                                                  'specimen_collected': str(c.on(collected))})
    if reported - collected > 56:
        c.case('reported-after-collection', [order, result], ev,
               f'Sample taken {c.on(collected)}; reported {c.on(reported)} ({reported - collected} days)')
    if ev.get('reclassified_day') and c.observed(ev['reclassified_day']):
        d = ev['reclassified_day']
        amended = c.record('genetics', 'genetic_result', ev, coding=c.coding(f'gene:{ev["gene"]}', 'HGNC'),
                           label_as_recorded=f'{ev["gene"]}: {ev["reclassified_to"]} (amended)', occurred=c.on_day(collected),
                           recorded_at=iso(c.moment(d)), available_at=iso(c.moment(d + 7)), status='amended',
                           supersedes=result['record_id'],
                           attributes={'gene': ev['gene'], 'hgvs': ev.get('hgvs'), 'classification': ev['reclassified_to'],
                                       'previous_classification': ev['classification'], 'method': ev['method']})
        c.case('reclassified', [result, amended], ev,
               f'{ev["gene"]} {ev.get("hgvs")}: {ev["classification"]} on {c.on(reported)}, '
               f'{ev["reclassified_to"]} on {c.on(d)}')


def capture_medication(c, ev):
    r = c.rng
    cid = ev['concept']
    start, end = ev['day'], ev.get('end_day')
    if start > c.last_contact:
        return
    atc = c.coding(cid, 'ATC') or c.coding(cid, 'local')
    elsewhere = ev.get('prescribed_by') == 'regional_hospital'
    records = []
    if c.observed(start) and not elsewhere:
        when = c.exact(start) if start in c.encounters else c.on_day(start)
        records.append(c.record('ehr', 'medication_order', ev, coding=atc, label_as_recorded=CONCEPTS[cid]['label'],
                                occurred=when, recorded_at=iso(c.moment(start)), available_at=iso(c.moment(start)),
                                status='active'))
    dispense_until = min(end or c.last_contact, c.last_contact)
    day = start + r.randint(0, 2)
    first_dispense = None
    step = 30
    while day <= dispense_until and cid not in ('midazolam', 'onasemnogene', 'nusinersen'):
        d = day
        if d <= c.last_contact:
            month_end = (c.on(d).replace(day=1) + timedelta(days=32)).replace(day=1)
            when = c.on_day(d)
            rec = c.record('pharmacy', 'dispensing', ev, coding=atc, label_as_recorded=CONCEPTS[cid]['label'],
                           occurred=when, recorded_at=iso(c.moment(d)), available_at=f'{month_end + timedelta(days=14)}T06:00',
                           attributes={'supply_days': step})
            first_dispense = first_dispense or rec
        day += step
        step = 90 if day - start > 60 else 30
    if elsewhere and first_dispense:
        c.case('start-unknown', [first_dispense], ev,
               f'Started by regional hospital B; no prescription here, first dispensed {first_dispense["occurred"]["earliest"][:10]}')
    if end and c.observed(end):
        visit = c.next_visit(end)
        if visit:
            stop = c.record('ehr', 'medication_order', ev, coding=atc, label_as_recorded=CONCEPTS[cid]['label'],
                            occurred=c.around(end, max(5, (visit - end) // 3), f'about {max(1, round((visit - end) / 7))} weeks ago'),
                            recorded_at=iso(c.moment(visit)), available_at=iso(c.moment(visit)), status='stopped',
                            attributes={'reason': ev.get('stop_reason')})
            c.case('stop-approximate', [stop], ev, f'Stop reported at clinic {visit - end} days later')


def capture_measurement_visits(c):
    """Growth at every clinic visit; the diary's monthly seizure counts."""
    r = c.rng
    previous = {}
    for v in c.visits:
        for cid in ('weight', 'height', 'head_circ'):
            if cid == 'head_circ' and v > 1100:
                continue
            value = growth(cid, v, c.z, c.head_brake)
            ev = c.event(cid, v, value=value, unit=UNITS[cid])
            if not c.scripted and r.random() < 0.02:
                c.case('missing-measurement', [], ev, f'{CONCEPTS[cid]["label"]} not recorded at the visit')
                continue
            shown, unit, case = value, UNITS[cid], None
            roll = 1.0 if c.scripted else r.random()
            if cid == 'weight' and roll < 0.025:
                shown, case = round(value * 2.2046, 1), ('unit-error', f'{value} kg entered as {round(value * 2.2046, 1)}')
            elif roll < 0.037:
                shown, case = round(value / 10, 2), ('decimal-shift', f'{value} entered as {round(value / 10, 2)}')
            elif roll < 0.052:
                unit, case = None, ('missing-unit', 'Unit left blank')
            elif cid == 'height' and roll < 0.065 and previous.get(cid):
                shown, case = round(previous[cid] - r.uniform(3, 6), 1), ('implausible-value', 'Height lower than at the previous visit')
            rec = c.record('ehr', 'measurement', ev, coding=c.coding(cid, 'LOINC'), label_as_recorded=CONCEPTS[cid]['label'],
                           occurred=c.exact(v), recorded_at=iso(c.moment(v)), available_at=iso(c.moment(v)),
                           value=shown, unit=unit)
            if case:
                c.case(case[0], [rec], ev, case[1])
            previous[cid] = value
    for ev in [e for e in c.truth if e['concept'] == 'seizure_frequency']:
        if c.diary_from is None or ev['day'] < c.diary_from or not c.observed(ev['day']):
            continue
        if not c.scripted and r.random() < 0.05:
            c.case('missing-measurement', [], ev, 'No diary entries this month')
            continue
        count = max(0, round(ev['value'] * r.uniform(0.75, 1.25)))
        c.record('diary', 'diary_summary', ev, coding=c.coding('seizure_frequency', 'local'),
                 label_as_recorded='Seizures this month', occurred=c.in_month(ev['day']),
                 recorded_at=iso(c.moment(ev['day'] + 29, 21)),
                 available_at=iso(c.moment(c.next_visit(ev['day']) or ev['day'] + 30, 9)), value=count, unit='/month')


def capture_procedure(c, ev):
    if not c.observed(ev['day']):
        return
    if ev.get('result_missing'):
        order = c.record('ehr', 'procedure', ev, coding=c.coding(ev['concept'], 'local'),
                         label_as_recorded=CONCEPTS[ev['concept']]['label'], occurred=c.on_day(ev['day']),
                         recorded_at=iso(c.moment(ev['day'])), available_at=iso(c.moment(ev['day'])), status='ordered')
        c.case('missing-result', [order], ev, f'{CONCEPTS[ev["concept"]]["label"]} ordered; no report filed')
        return order
    rec = c.record('ehr', 'procedure', ev, coding=c.coding(ev['concept'], 'local'), label_as_recorded=CONCEPTS[ev['concept']]['label'],
                   occurred=c.on_day(ev['day']), recorded_at=iso(c.moment(ev['day'] + 1)),
                   available_at=iso(c.moment(ev['day'] + 2)), status='final', text=ev.get('result'))
    return rec


# Free text.

class Note:
    def __init__(self):
        self.text = ''
        self.mentions = []

    def say(self, text):
        self.text += text

    def mention(self, text, concept_id, truth=None, assertion='present', experiencer='patient', time_expression=None,
                historical=False):
        start = len(self.text)
        self.text += text
        m = {'span': [start, start + len(text)], 'text': text, 'concept': concept_id, 'assertion': assertion,
             'experiencer': experiencer, 'truth_event_id': truth['event_id'] if truth else None}
        if time_expression:
            m['time_expression'] = time_expression
        if historical:
            m['historical'] = True
        self.mentions.append(m)
        return m


def when_text(c, ev, visit):
    gap = visit - ev['day']
    if gap <= 3:
        return 'this week'
    if gap <= 70:
        weeks = max(1, round(gap / 7))
        return 'about a week ago' if weeks == 1 else f'about {weeks} weeks ago'
    return f'at around {age_text(ev["day"])}'


def clinic_letter(c, visit, previous, letter_index, scripted_lines):
    r = c.rng
    note = Note()
    note.say(f'Paediatric neurology review at {age_text(visit)}. ')
    if c.ga < 37 and visit < 730:
        note.say(f'Born at {c.ga} weeks; corrected age {age_text(visit - (40 - c.ga) * 7)}. ')
    window = [e for e in c.truth if previous < e['day'] <= visit and e['kind'] in ('phenotype', 'medication', 'diagnosis',
                                                                                    'genetic', 'procedure')]
    stale = scripted_lines.get(('copy_forward', visit))
    flags = []
    if stale:
        note.say(stale['text'])
        flags.append(('copy-forward', stale))
        window = [e for e in window if e['kind'] != 'phenotype']
    for ev in sorted(window, key=lambda e: e['day']):
        cid, kind = ev['concept'], ev['kind']
        if kind == 'phenotype' and cid in PHRASES:
            hedge = scripted_lines.get(('uncertain', ev['event_id'])) or (r.random() < 0.15 and cid in ('dev_delay', 'autistic_behavior'))
            phrase = pick(r, PHRASES[cid])
            if hedge:
                note.say('Parents wonder whether there has been ')
                note.mention(f'possible {phrase}', cid, ev, assertion='possible', time_expression=when_text(c, ev, visit))
                note.say('; we will reassess. ')
                flags.append(('uncertain-mention', ev))
            else:
                note.say('Since the last review: ' if not any(m['assertion'] == 'present' and not m.get('historical')
                                                              for m in note.mentions) else 'Also ')
                note.mention(phrase, cid, ev, time_expression=when_text(c, ev, visit))
                if ev.get('duration_min'):
                    note.say(f', lasting about {ev["duration_min"]} minutes')
                note.say(f' ({when_text(c, ev, visit)}). ')
        elif kind == 'medication' and ev['day'] > previous:
            label = CONCEPTS[cid]['label'].split(' (')[0].lower()
            spelled = scripted_lines.get(('misspell', ev['event_id']))
            note.mention(spelled or label.capitalize(), cid, ev)
            note.say(' was started. ' if not ev.get('prescribed_by') else ' was started by the regional hospital. ')
            if spelled:
                flags.append(('misspelling', ev))
        elif kind == 'genetic':
            note.say(f'{ev["method"]} sent ({ev["gene"]}). ')
        elif kind == 'diagnosis':
            note.say('This confirms ')
            note.mention(CONCEPTS[cid]['label'], cid, ev)
            note.say('. ')
        elif kind == 'procedure' and ev.get('result'):
            note.say(f'{CONCEPTS[cid]["label"]}: {ev["result"]} ')
    for ev in c.truth:
        if ev['kind'] == 'genetic' and previous < ev.get('reported_day', -1) <= visit and not ev.get('result_missing'):
            note.say(f'{ev["method"]} result: ')
            if ev['classification'] == 'no relevant variant':
                note.mention(f'no relevant variant in {ev["gene"]}', f'gene:{ev["gene"]}', ev, assertion='absent')
                note.say('. ')
            else:
                phrase = {'pathogenic': f'pathogenic {ev["gene"]} variant',
                          'uncertain significance': f'{ev["gene"]} variant of uncertain significance',
                          'homozygous deletion': f'homozygous {ev["gene"]} deletion'}[ev['classification']]
                note.mention(phrase, f'gene:{ev["gene"]}', ev)
                note.say(f' ({ev["hgvs"]}). ' if ev.get('hgvs') else '. ')
        if ev['kind'] == 'medication' and ev.get('end_day') and previous < ev['end_day'] <= visit:
            note.mention(CONCEPTS[ev['concept']]['label'].split(' (')[0], ev['concept'], ev)
            note.say(f' was stopped {when_text(c, {"day": ev["end_day"]}, visit)}' +
                     (f' because {ev["stop_reason"]}. ' if ev.get('stop_reason') else '. '))
        if ev['kind'] == 'genetic' and ev.get('reclassified_day') and previous < ev['reclassified_day'] <= visit:
            note.say('The laboratory has reclassified the ')
            note.mention(f'{ev["gene"]} variant as pathogenic', f'gene:{ev["gene"]}', ev)
            note.say('. ')
        if ev['kind'] == 'diagnosis' and ev.get('provisional_day') and previous < ev['provisional_day'] <= visit:
            note.say('The history is suggestive of ')
            note.mention(f'possible {CONCEPTS[ev["concept"]]["label"]}', ev['concept'], ev, assertion='possible')
            note.say('; genetics awaited. ')
            flags.append(('uncertain-mention', ev))
    current = [e for e in c.truth if e['kind'] == 'medication' and e['day'] <= visit and
               (not e.get('end_day') or e['end_day'] > visit) and e['concept'] not in ('midazolam',)]
    if current:
        note.say('Current medication: ')
        for i, ev in enumerate(current):
            note.mention(CONCEPTS[ev['concept']]['label'].split(' (')[0].lower(), ev['concept'], ev)
            note.say(', ' if i < len(current) - 1 else '. ')
    if letter_index > 0 and letter_index % 4 == 0:
        firsts = [e for e in c.truth if e['kind'] == 'phenotype' and e['day'] < previous][:2]
        if firsts:
            note.say('Background: ')
            for i, ev in enumerate(firsts):
                note.mention(PHRASES.get(ev['concept'], [CONCEPTS[ev['concept']]['label']])[0], ev['concept'], ev,
                             time_expression=f'at {age_text(ev["day"])}', historical=True)
                note.say(f' at {age_text(ev["day"])}' + ('; ' if i < len(firsts) - 1 else '. '))
    if letter_index == 0 and c.family_history:
        who, cid = c.family_history
        note.say(f'Family history: {who} had ')
        note.mention('febrile seizures' if cid == 'febrile_seizure' else 'epilepsy', cid,
                     experiencer='family', time_expression='in childhood')
        note.say('. ')
        flags.append(('family-history-mention', None))
    regressed = any(e['concept'] == 'dev_regression' and e['day'] <= visit for e in c.truth)
    if not regressed and r.random() < 0.5:
        note.say('No ')
        note.mention('regression', 'dev_regression', assertion='absent')
        note.say(' of motor or language skills. ')
        flags.append(('negated-mention', None))
    if c.archetype != 'tsc' and letter_index == 1:
        note.say('Skin examination: no ')
        note.mention('hypomelanotic macules', 'hypomelanotic_macule', assertion='absent')
        note.say('. ')
    upcoming = [e for e in c.truth if e['kind'] == 'medication' and visit < e['day'] <= visit + 300
                and not e.get('prescribed_by') and not e.get('stop_reason')]
    if upcoming and r.random() < 0.6:
        ev = upcoming[0]
        note.say('Plan: if seizures continue, consider ')
        note.mention(CONCEPTS[ev['concept']]['label'].split(' (')[0].lower(), ev['concept'], assertion='hypothetical')
        note.say('. ')
        flags.append(('hypothetical-mention', None))
    rec = c.record('letters', 'note', None, label_as_recorded='Clinic letter', occurred=c.on_day(visit),
                   recorded_at=iso(c.moment(visit)), available_at=iso(c.moment(visit + r.randint(3, 12), 16)),
                   text=note.text.strip(), language='en', mentions=note.mentions)
    return rec, flags


def referral_letter_nl(c, ev=None, meds=()):
    note = Note()
    if ev:
        note.say(f'Verwijsbrief. Patiënte van {round(months(ev["day"]))} maanden werd op {c.on(ev["day"]).strftime("%d-%m-%Y")} '
                 'gezien op de SEH met een ')
        note.mention('koortsstuip', 'febrile_seizure', ev, time_expression=c.on(ev['day']).strftime('%d-%m-%Y'))
        note.say(f' van ongeveer {ev.get("duration_min", 20)} minuten, {"rechts" if ev.get("side") == "right" else "links"}zijdig. ')
    else:
        note.say('Brief aan de kinderneuroloog. Toename van aanvallen. ')
    for med in meds:
        note.say('Gestart met ')
        note.mention(CONCEPTS[med['concept']]['label'].lower().replace('valproic acid', 'valproïnezuur'), med['concept'], med)
        note.say(f' op {c.on(med["day"]).strftime("%d-%m-%Y")}. ')
    note.say('Graag verdere analyse.')
    day = max([ev['day'] if ev else 0] + [m['day'] for m in meds]) + 6
    return c.record('referral_nl', 'note', None, label_as_recorded='Verwijsbrief', occurred=c.on_day(day),
                    recorded_at=iso(c.moment(day)), available_at=iso(c.moment(day + 9)), text=note.text,
                    language='nl', mentions=note.mentions)


def discharge_fr(c, se, dx):
    note = Note()
    note.say(f'Résumé de sortie. Enfant de {age_text(se["day"]).replace("months", "mois").replace("years", "ans")}, '
             'hospitalisé pour un ')
    note.mention('état de mal épileptique fébrile', 'status_epilepticus', se)
    note.say(f' le {c.on(se["day"]).strftime("%d/%m/%Y")}. Diagnostic retenu : ')
    note.mention('syndrome de Dravet', 'dravet', dx)
    note.say(' (diagnostic clinique, analyse SCN1A négative). Traitement de sortie : valproate, clobazam.')
    day = max(se['day'], dx['day']) + 4
    return c.record('discharge_fr', 'note', None, label_as_recorded='Résumé de sortie', occurred=c.on_day(day),
                    recorded_at=iso(c.moment(day)), available_at=iso(c.moment(day + 21)), text=note.text,
                    language='fr', mentions=note.mentions)


def capture(c, scripted_lines):
    """Records for every truth event, then free text, then the cases free text creates."""
    if not c.visits:
        schedule_visits(c, max(min(e['day'] for e in c.truth), 0))
    for ev in list(c.truth):
        kind = ev['kind']
        if kind == 'phenotype':
            capture_phenotype(c, ev)
        elif kind == 'genetic':
            capture_genetic(c, ev)
        elif kind == 'medication':
            capture_medication(c, ev)
        elif kind == 'procedure':
            capture_procedure(c, ev)
    for ev in [e for e in c.truth if e['kind'] == 'diagnosis']:
        capture_diagnosis(c, ev)
    capture_measurement_visits(c)
    previous, flags_seen = -1, set()
    for i, v in enumerate(c.visits):
        rec, flags = clinic_letter(c, v, previous, i, scripted_lines)
        for kind, ev in flags:
            if kind == 'copy-forward':
                c.case(kind, [rec], None, ev['detail'])
            elif kind in ('family-history-mention', 'negated-mention', 'hypothetical-mention'):
                if kind not in flags_seen:
                    c.case(kind, [rec], None, f'First {kind.replace("-mention", "").replace("-", " ")} statement')
                    flags_seen.add(kind)
            else:
                c.case(kind, [rec], ev)
        previous = v
    for ev in c.truth:
        if ev.pop('_free_text_only', False):
            mentioned = [r for r in c.records if r['kind'] == 'note' and
                         any(m['truth_event_id'] == ev['event_id'] for m in r.get('mentions', []))]
            if mentioned:
                c.case('free-text-only', mentioned[:1], ev, f'{CONCEPTS[ev["concept"]]["label"]} appears only in letters')
        ev.pop('_ehr_style', None)


# The reference child and the scripted edge cases.

def script_reference(c):
    """C001: girl, Dravet syndrome, sodium-channel blocker before diagnosis. Every defect here is deliberate."""
    o = {'onset': 190, 'first_duration': 25, 'repeat_gaps': [40], 'se': 285, 'afebrile': 360, 'myoclonic': True,
         'myoclonic_day': 425, 'delay': 540, 'ataxia': True, 'ataxia_day': 1100, 'valproate': 290,
         'genetic_collected': 330, 'genetic_reported': 412, 'result': 'vus', 'reclassified_day': 760,
         'ncb_day': 400, 'ncb_drug': 'carbamazepine', 'ncb_days': 45, 'ncb_by': 'regional_hospital',
         'clobazam': 480, 'stiripentol': 600, 'fenfluramine': True, 'fenfluramine_day': 1300, 'provisional_day': 466,
         'dx_lag': 0, 'rate': 5.0, 'diary': True, 'diary_from': 355, 'family': ('maternal cousin', 'febrile_seizure')}
    dravet(c, o)
    c.truth[0]['elsewhere'] = 'regional hospital B'
    c.registry = True
    c.event('eeg', 300, result=None, result_missing=True)
    for ev in c.truth:
        if ev['concept'] == 'dravet':
            ev['_ehr_style'] = 'coarse'
    return o


def apply_reference_defects(c, lines):
    by = lambda cid, n=0: [e for e in c.truth if e['concept'] == cid][n]
    first, second, se = by('febrile_seizure'), by('febrile_seizure', 1), by('status_epilepticus')
    # First seizure: ED at the regional hospital; the parents' diary remembers five months.
    referral = referral_letter_nl(c, first)
    c.case('non-english-note', [referral], first, 'Referral from regional hospital B, in Dutch')
    ncb = by('carbamazepine')
    letter = referral_letter_nl(c, None, [ncb])
    c.case('non-english-note', [letter], ncb, 'Regional hospital B letter, in Dutch: the only prescription evidence')
    diary = c.record('diary', 'diary_entry', first, label_as_recorded='First fit (entered later from memory)',
                     occurred=c.around(152, 15, 'about 5 months old'), recorded_at=iso(c.moment(370, 21)),
                     available_at=iso(c.moment(466, 9)))
    ehr_first = [r for r in c.records if r['truth_event_id'] == first['event_id'] and r['source'] == 'ehr']
    c.case('conflicting-onset', ehr_first + [referral, diary], first,
           f'Hospital: {c.on(first["day"])} (6 months). Diary, entered months later: "about 5 months old"')
    c.case('approximate-onset', [diary], first, 'Parent diary: "about 5 months old"')
    # Second seizure at home, only told at the next visit.
    second_recs = [r for r in c.records if r['truth_event_id'] == second['event_id'] and r['source'] == 'ehr']
    if second_recs:
        c.case('approximate-onset', second_recs, second, f'Reported as "{second_recs[0]["occurred"]["as_written"]}"')
    # Status epilepticus: duplicated by the ward.
    se_recs = [r for r in c.records if r['truth_event_id'] == se['event_id'] and r['source'] == 'ehr']
    dup = c.record('ehr', 'condition', se, coding=c.coding('seizure', 'ICD-10'), label_as_recorded='Convulsions',
                   occurred=c.on_day(se['day']), recorded_at=iso(c.moment(se['day'] + 1)),
                   available_at=iso(c.moment(se['day'] + 1)), status='active')
    c.case('duplicate-record', se_recs + [dup], se, 'Ward re-entered the admission diagnosis next day as R56.8')
    # A valproate dispensing with day and month swapped.
    disp = [r for r in c.records if r['source'] == 'pharmacy' and r['truth_event_id'] == by('valproate')['event_id']]
    target = next(r for r in disp if int(r['occurred']['earliest'][8:10]) <= 12 and
                  r['occurred']['earliest'][5:7] != r['occurred']['earliest'][8:10])
    y, m, d = target['occurred']['earliest'][:10].split('-')
    swapped = f'{y}-{d}-{m}'
    target['occurred'] = {'earliest': f'{swapped}T00:00', 'latest': f'{swapped}T23:59', 'precision': 'day',
                          'as_written': f'{m}-{d}-{y}'}
    c.case('day-month-swap', [target], by('valproate'), f'Dispensed {y}-{m}-{d}; entered as {swapped}')
    # Growth: one weight in pounds, one head circumference with a shifted decimal, one unit blank.
    weights = [r for r in c.records if r['kind'] == 'measurement' and r['coding']['code'] == '29463-7']
    heads = [r for r in c.records if r['kind'] == 'measurement' and r['coding']['code'] == '9843-4']
    heights = [r for r in c.records if r['kind'] == 'measurement' and r['coding']['code'] == '8302-2']
    w = weights[4]
    true_w = w['value']
    w['value'] = round(true_w * 2.2046, 1)
    c.case('unit-error', [w], next(e for e in c.truth if e['event_id'] == w['truth_event_id']),
           f'{true_w} kg entered as {w["value"]} kg (pounds)')
    h = heads[5]
    true_h = h['value']
    h['value'] = round(true_h / 10, 2)
    c.case('decimal-shift', [h], next(e for e in c.truth if e['event_id'] == h['truth_event_id']),
           f'{true_h} cm entered as {h["value"]} cm')
    heights[3].pop('unit', None)
    c.case('missing-unit', [heights[3]], next(e for e in c.truth if e['event_id'] == heights[3]['truth_event_id']),
           'Height recorded without a unit')
    # The plateau at 18 months was hedged in the letter and never coded.
    delay = by('dev_delay')
    for r in [r for r in c.records if r['truth_event_id'] == delay['event_id'] and r['source'] in ('ehr', 'registry')]:
        c.records.remove(r)
    delay['_free_text_only'] = True


def reference_lines(c):
    by = lambda cid: next(e for e in c.truth if e['concept'] == cid)
    stale_visit = next(v for v in c.visits if 780 <= v <= 900)
    return {
        ('copy_forward', stale_visit): {'text': 'Seizure-free for three months on current treatment. ',
                                        'detail': 'Copied from the previous letter; the diary shows seizures every month'},
        ('uncertain', by('dev_delay')['event_id']): True,
        ('misspell', by('stiripentol')['event_id']): 'Stirapentol',
    }


def scripted_edge_cases(children):
    """Defects that should appear on candidates the reference child is likely to be compared with."""
    by_key = {c.key: c for c in children}
    pre = by_key['dravet-preterm']
    c = pre
    c.case('corrected-age', [], None, f'Born at {c.ga} weeks; corrected age is {round((40 - c.ga) / 4.35, 1)} months less')
    lost = by_key['dravet-lost']
    lost.case('lost-to-follow-up', [], None, f'Last contact at {age_text(lost.last_contact)}; family moved abroad')
    gap = by_key['dravet-gap']
    gap.case('follow-up-gap', [], None, f'No contact between {age_text(gap.gap[0])} and {age_text(gap.gap[1])}')
    sib = by_key['dravet-sibling']
    d = sib.visits[3]
    rec = sib.record('pharmacy', 'dispensing', None, coding=sib.coding('salbutamol', 'ATC'), label_as_recorded='Salbutamol',
                     occurred=sib.on_day(d), recorded_at=iso(sib.moment(d)), available_at=iso(sib.moment(d + 30)),
                     attributes={'supply_days': 30})
    sib.case('misattributed-record', [rec], None, 'Older brother\'s inhaler dispensed under this child\'s identifier')
    typo = by_key['dravet-typo']
    target = next(r for r in typo.records if r['kind'] == 'condition' and r['source'] == 'ehr')
    good = target['occurred']['earliest'][:4]
    bad = good[:2] + good[3] + good[2] if good[2] != good[3] else str(int(good) + 10)
    target['occurred'] = dict(target['occurred'], earliest=bad + target['occurred']['earliest'][4:],
                              latest=bad + target['occurred']['latest'][4:])
    typo.case('date-typo', [target], next(e for e in typo.truth if e['event_id'] == target['truth_event_id']),
              f'Year {good} entered as {bad}')


# Cohort.

PLAN = [
    ('reference', 'dravet', 'F', 2019, {}),
    ('dravet-ncb-1', 'dravet', 'M', 2018, {'ncb': True}), ('dravet-ncb-2', 'dravet', 'F', 2020, {'ncb': True}),
    ('dravet-ncb-3', 'dravet', 'M', 2017, {'ncb': True}), ('dravet-1', 'dravet', 'F', 2016, {'ncb': False}),
    ('dravet-2', 'dravet', 'M', 2021, {'ncb': False}), ('dravet-sibling', 'dravet', 'M', 2019, {'ncb': False}),
    ('dravet-typo', 'dravet', 'F', 2018, {'ncb': True}),
    ('dravet-preterm', 'dravet', 'F', 2020, {'ncb': True}),
    ('dravet-negative-fr', 'dravet', 'M', 2019, {'result': 'negative', 'ncb': False}),
    ('dravet-lost', 'dravet', 'F', 2017, {'ncb': True}),
    ('dravet-gap', 'dravet', 'M', 2016, {'ncb': False}),
] + [(f'gefs-{i}', 'gefs_plus', s, y, {}) for i, (s, y) in enumerate([('M', 2017), ('F', 2016), ('M', 2019), ('F', 2018),
                                                                       ('M', 2015), ('F', 2020)])] \
  + [(f'fs-{i}', 'febrile_only', s, y, {}) for i, (s, y) in enumerate([('F', 2019), ('M', 2020), ('M', 2018), ('F', 2021),
                                                                        ('M', 2017), ('F', 2022)])] \
  + [(f'pcdh19-{i}', 'pcdh19', 'F', y, {}) for i, y in enumerate([2016, 2018, 2019, 2020, 2017])] \
  + [(f'cdkl5-{i}', 'cdkl5', s, y, {}) for i, (s, y) in enumerate([('F', 2019), ('F', 2021), ('M', 2020), ('F', 2017)])] \
  + [(f'tsc-{i}', 'tsc', s, y, {}) for i, (s, y) in enumerate([('M', 2018), ('F', 2020), ('M', 2016), ('F', 2022),
                                                                ('M', 2019), ('F', 2021)])] \
  + [(f'rett-{i}', 'rett', 'F', y, {}) for i, y in enumerate([2016, 2018, 2019, 2020, 2021])] \
  + [(f'sma-{i}', 'sma', s, y, {}) for i, (s, y) in enumerate([('M', 2020), ('F', 2023), ('M', 2023), ('F', 2021)])]


def build(seed=SEED):
    rng = random.Random(seed)
    ids = [f'C{i:03d}' for i in range(2, len(PLAN) + 1)]
    rng.shuffle(ids)
    ids = ['C001'] + ids
    children = []
    for pid, (key, archetype, sex, year, overrides) in zip(ids, PLAN):
        crng = random.Random(f'{seed}:{key}')
        birth = date(year, 1, 1) + timedelta(days=crng.randint(0, 364))
        child = Child(pid, key, archetype, crng, sex, birth, scripted=(key == 'reference'),
                      ga_weeks=29 if key == 'dravet-preterm' else 39)
        if key == 'reference':
            child.birth = date(2019, 4, 12)
            child.snapshot_day = (SNAPSHOT - child.birth).days
            child.last_contact = child.snapshot_day - 21
            script_reference(child)
        else:
            if key == 'dravet-lost':
                child.last_contact = crng.randint(1000, 1300)
            if key == 'dravet-gap':
                child.gap = (crng.randint(900, 1100), crng.randint(1500, 1700))
            ARCHETYPES[archetype](child, overrides)
            if key == 'dravet-ncb-2':
                next(e for e in child.truth if e['concept'] == 'dravet')['_ehr_style'] = 'wrong'
        child.truth = [e for e in child.truth if e['day'] <= child.snapshot_day]
        children.append(child)
    for child in children:
        if child.key == 'reference':
            schedule_visits(child, min(e['day'] for e in child.truth))
            lines = reference_lines(child)
            capture(child, lines)
            apply_reference_defects(child, lines)
            for ev in child.truth:
                if ev.pop('_free_text_only', False):
                    mentioned = [r for r in child.records if r['kind'] == 'note' and
                                 any(m['truth_event_id'] == ev['event_id'] for m in r.get('mentions', []))]
                    child.case('free-text-only', mentioned[:1], ev,
                               f'{CONCEPTS[ev["concept"]]["label"]} appears only in letters, hedged')
        else:
            capture(child, {})
        if child.key == 'dravet-negative-fr':
            se = next((e for e in child.truth if e['concept'] == 'status_epilepticus'), None) or \
                child.truth[0]
            dx = next(e for e in child.truth if e['concept'] == 'dravet')
            for r in [r for r in child.records if r['truth_event_id'] == dx['event_id']]:
                child.records.remove(r)
            child.quality = [q for q in child.quality if q['truth_event_id'] != dx['event_id']]
            fr = discharge_fr(child, se, dx)
            child.case('non-english-note', [fr], dx, 'Discharge summary from hospital C, in French')
            child.case('free-text-only', [fr], dx, 'Dravet syndrome named only in free text: the French discharge '
                       'summary and later clinic letters; never coded')
    scripted_edge_cases(children)
    return children


def check_record_ids(children):
    """Record ids must be unique across the dataset; the converter keys on source/record_id."""
    seen = {}
    for c in children:
        for r in c.records:
            rid = r['record_id']
            assert rid not in seen, f'duplicate record_id {rid}: {seen[rid]} and {c.pid} {r["source"]}/{r["kind"]}'
            seen[rid] = f'{c.pid} {r["source"]}/{r["kind"]}'


def serialise(children, seed):
    check_record_ids(children)
    patients = []
    for c in sorted(children, key=lambda c: c.pid):
        c.records.sort(key=lambda r: (r['occurred']['earliest'], r['record_id']))
        patients.append({
            'patient_id': c.pid, 'sex': c.sex, 'birth_date': str(c.birth), 'gestational_age_weeks': c.ga,
            'follow_up': {'last_contact': str(c.on(c.last_contact)), 'snapshot': str(SNAPSHOT),
                          'gap': [str(c.on(c.gap[0])), str(c.on(c.gap[1]))] if c.gap else None},
            'truth': {'condition_group': c.archetype, 'events': c.truth},
            'records': c.records,
            'quality': c.quality,
        })
    return {
        'schema': 'ptm-mockup-synthetic-1',
        'dataset_id': 'synthetic-paediatric-rare-v0',
        'notice': ('Entirely synthetic: no real patient contributed to any value. Terminology codes are illustrative, '
                   'written from memory, and unverified; check them against the source terminology before reuse.'),
        'generator': {'script': 'demos/mockups/synthetic/generate.py', 'seed': seed, 'snapshot': str(SNAPSHOT)},
        'reference_patient_id': 'C001',
        'sources': SOURCES,
        'concepts': CONCEPTS,
        'quality_kinds': {k: {'category': v[0], 'description': v[1]} for k, v in QUALITY_KINDS.items()},
        'patients': patients,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--check', action='store_true', help='fail if the committed file differs from a fresh run')
    args = parser.parse_args()
    data = serialise(build(args.seed), args.seed)
    text = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False) + '\n'
    if args.check:
        current = args.output.read_text(encoding='utf-8') if args.output.exists() else ''
        if current != text:
            sys.exit(f'{args.output} differs from a fresh run with seed {args.seed}')
        print(f'{args.output} reproduces')
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding='utf-8')
    kinds = {}
    for p in data['patients']:
        for q in p['quality']:
            kinds[q['kind']] = kinds.get(q['kind'], 0) + 1
    records = sum(len(p['records']) for p in data['patients'])
    print(f'{len(data["patients"])} children, {records} records, '
          f'{sum(kinds.values())} quality cases across {len(kinds)}/{len(QUALITY_KINDS)} kinds')
    missing = sorted(set(QUALITY_KINDS) - set(kinds))
    if missing:
        print('kinds not exercised: ' + ', '.join(missing))
    print(f'sha256 {hashlib.sha256(text.encode()).hexdigest()}  {args.output}')


if __name__ == '__main__':
    main()
