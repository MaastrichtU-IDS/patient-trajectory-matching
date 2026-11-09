"""The reference matcher with engine inputs built once per patient, for differentials at chartevents scale.

`execution_store_reference.exhaustive_match` rebuilds every patient's closure-expanded engine events,
and the map of which sources record which concept, for every query, and sends the engine all of a
patient's events. With chartevents (thousands of points per patient, about half a million excluded
records in the demo) and hundreds of queries, that rebuild dominates. D16 requires the differential to
build engine inputs once per patient. `PreparedReference(ingest)` does that:

- once per patient: the engine events (concepts expanded through the closure, times, variables and
  `proper` as the ingest gave them), an index from each expanded concept to the events carrying it,
  whether two events name one time variable, and an index from each expanded concept to the excluded
  records carrying it; once per ingest, the sources recording each concept;
- per query: one `PreparedQuery`; each patient's engine input is the events carrying some concept of
  some step (the step's own or a widening in a relaxation set the engine would try), in ingest order,
  or every event when the patient shares variables; the excluded-records rule sees the excluded
  records carrying such a concept.

Why the result is the same, byte for byte. The engine lets a step bind only an event listing one of
the step's concepts, and `first` and `last` choose among exactly those events. An event no step can bind
constrains nothing unless it names a variable another event names (D24: it may link two events that
can bind), so such patients keep every event. The coverage rule reads only the recorders map and the
patient's coverage. The excluded-records rule (`admission_check`, called unchanged) keeps only records
whose expanded concepts meet a step's concepts, so the records it drops are records it would ignore;
it looks up bound events, which are among those kept. The tests compare `match` with
`exhaustive_match` on seeded queries over interval, point, excluded-record and shared-variable ingests.

The store's candidate loading makes the same event-filter argument, so a differential against this
reference alone does not test that argument independently: the demo verifier keeps `exhaustive_match`
(every event, no filter) on a deterministic slice of its queries.
"""
from __future__ import annotations

from . import execution_store_reference as ref
from . import trajectory_graph as tg


coverage_rule = ref.coverage_rule  # the reference's one copy


class PreparedReference:
    def __init__(self, ingest):
        ancestors = ingest['closure']['ancestors']
        self.ancestors = ancestors
        self.recorders = {}
        self.patients = []
        for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
            events, index = [], {}
            for e in p['events']:
                concepts = ref.expanded(e['concepts'], ancestors)
                event = {'id': e['event_id'], 'concepts': sorted(concepts), 'start': list(e['start'])}
                if 'end' in e:
                    event['end'] = list(e['end'])
                event.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
                for c in concepts:
                    index.setdefault(c, []).append(len(events))
                    self.recorders.setdefault(c, set()).add(e['source'])
                events.append(event)
            excluded = {}
            for position, x in enumerate(p.get('excluded', [])):
                for c in ref.expanded([x['concept']], ancestors):
                    excluded.setdefault(c, []).append(position)
            self.patients.append({'patient_id': p['patient_id'], 'coverage': p['coverage'], 'events': events,
                                  'index': index, 'shares': ref.shares_variables(p),
                                  'excluded': p.get('excluded', []), 'excluded_index': excluded})

    def engine_events(self, patient, wanted):
        if patient['shares']:
            return patient['events']
        positions = sorted({i for c in wanted for i in patient['index'].get(c, ())})
        return [patient['events'][i] for i in positions]

    def match(self, query, windows):
        """`execution_store_reference.exhaustive_match(ingest, query, windows)`, from the prepared inputs."""
        pq = tg.PreparedQuery(query)
        per_step = ref.step_concepts(query)
        wanted = set().union(*per_step.values())
        results = []
        for p in self.patients:
            pid, window = p['patient_id'], windows.get(p['patient_id'])
            events = self.engine_events(p, wanted)
            result = tg.match_prepared(pq, {'patient_id': pid, 'window': window, 'events': events})
            if result['status'] == 'none':
                result = coverage_rule(query, per_step, self.recorders, p['coverage'], window, result)
            if window is not None and result['status'] != 'not_comparable':
                positions = sorted({i for c in wanted for i in p['excluded_index'].get(c, ())})
                relevant = {'patient_id': pid, 'excluded': [p['excluded'][i] for i in positions]}
                result = ref.admission_check(query, per_step, result, relevant, events, self.ancestors, window)
            results.append(result)
        return results
