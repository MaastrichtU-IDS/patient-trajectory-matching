"""State coverage checked against a pointwise finite-grid oracle and RDF round trips."""
from copy import deepcopy
from itertools import product
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from jsonschema import ValidationError
from rdflib import Graph, RDF, RDFS, OWL
from . import state_validity as state, exact_intervals as ei, claim_rdf as cr


def interval(rid,start,end,polarity='positive',**extra):
    return {'id':rid,'kind':'state_interval','patient_id':'P','episode_id':'E','clock_id':'c',
            'state_iri':'urn:state:low','polarity':polarity,'start_us':start,'end_us':end,
            'source_key':'synthetic:'+rid,**extra}


def point(rid,time,polarity='positive'):
    row=interval(rid,0,1,polarity)
    row.pop('start_us');row.pop('end_us');row.update(kind='point_observation',time_us=time)
    return row


def source(records):
    return {'profile':state.SOURCE_PROFILE,'dataset_id':'synthetic','snapshot_id':'one',
            'assertion_policy_id':'synthetic-explicit-assertions-v1',
            'clocks':[{'clock_id':'c','origin':'2026-09-01T00:00:00Z','scope':'patient:P','unit':'microsecond'}],
            'records':records}


def query(start=0,end=30):
    return {'profile':state.QUERY_PROFILE,'id':'sustained','patient_id':'P','episode_id':'E',
            'clock_id':'c','state_iri':'urn:state:low','start_us':start,'end_us':end}


class StateTests(unittest.TestCase):
    def test_four_examples(self):
        cases=[([interval('a',0,30)],'HOLDS'),([point('a',0),point('b',30)],'UNKNOWN'),
               ([interval('a',0,10),interval('b',10,20,'negative'),interval('c',20,30)],'VIOLATED'),
               ([interval('a',0,10),interval('b',20,30)],'UNKNOWN')]
        for rows,status in cases:
            self.assertEqual(state.execute(source(rows),query())['status'],status)

    def test_finite_grid_oracle(self):
        for values in product(('positive','negative','unknown'),repeat=4):
            rows=[interval('r'+str(i),i,i+1,value) for i,value in enumerate(values) if value!='unknown']
            result=state.execute(source(rows),query(0,4)); coverage=result['coverage']
            self.assertEqual(result['status'],'VIOLATED' if 'negative' in values else 'UNKNOWN' if 'unknown' in values else 'HOLDS')
            for name,value in [('supported','positive'),('refuted','negative'),('unknown','unknown')]:
                self.assertEqual(coverage[name+'_duration_us'],values.count(value))
            self.assertEqual(sum(coverage[k] for k in ('supported_duration_us','refuted_duration_us','unknown_duration_us')),4)
            expected=max((len(s) for s in ''.join('x' if v=='positive' else ' ' for v in values).split()),default=0)
            self.assertEqual(coverage['longest_supported_duration_us'],expected)

    def test_union_and_exact_provenance(self):
        r=state.execute(source([interval('a',-3,10),interval('b',5,20),interval('c',20,40)]),query())
        self.assertEqual(r['status'],'HOLDS')
        self.assertEqual(r['coverage']['positive_intervals'],[{'start_us':0,'end_us':30}])
        middle=next(s for s in r['segments'] if s['start_us']==5)
        self.assertEqual(middle['positive_record_ids'],['a','b'])
        self.assertEqual(r['coverage']['supported_duration_us'],30)
        self.assertEqual(r['coverage']['longest_supported_duration_us'],30)
        self.assertEqual(set(r['evidence']),{'a','b','c'})

    def test_half_open_and_boundary_points(self):
        r=state.execute(source([interval('a',0,30),interval('b',30,40,'negative'),point('p',30)]),query())
        self.assertEqual(r['status'],'HOLDS'); self.assertEqual(r['point_observation_ids'],[])
        r=state.execute(source([interval('a',-10,0)]),query())
        self.assertEqual(r['coverage']['unknown_duration_us'],30)

    def test_conflict_blocks_and_does_not_explode(self):
        r=state.execute(source([interval('a',0,20),interval('b',10,30,'negative')]),query())
        self.assertEqual(r['status'],'BLOCKED_SOURCE_CONFLICT'); self.assertIsNone(r['coverage'])
        self.assertEqual(r['conflicts'],[{'positive_id':'a','negative_id':'b','start_us':10,'end_us':20}])
        # Whole admitted snapshot is checked, including conflicts outside the window.
        self.assertEqual(state.execute(source([interval('a',40,60),interval('b',50,70,'negative')]),query())['status'],'BLOCKED_SOURCE_CONFLICT')

    def test_scope_and_clock_isolation(self):
        s=source([interval('a',0,30,episode_id='OTHER')])
        self.assertEqual(state.execute(s,query())['status'],'UNKNOWN')
        s=source([interval('a',0,30,state_iri='urn:state:other')])
        self.assertEqual(state.execute(s,query())['status'],'UNKNOWN')
        s=source([interval('a',0,30,clock_id='d')])
        s['clocks'].append({**s['clocks'][0],'clock_id':'d'})
        self.assertEqual(state.execute(s,query())['status'],'INCOMPARABLE')
        s['clocks'][1]['scope']='patient:OTHER'
        with self.assertRaises(ei.ContractError): state.execute(s,query())

    def test_empty_unknown_and_negative_point_not_interval(self):
        for rows in ([],[point('a',10,'negative')]):
            r=state.execute(source(rows),query())
            self.assertEqual(r['status'],'UNKNOWN')
            self.assertEqual(r['coverage']['refuted_duration_us'],0)

    def test_invalid_input_and_distinct_primitives(self):
        for mode in ('zero','reversed','boolean','float','point_interval','duplicate','clock','extra'):
            s=source([interval('a',0,30)])
            if mode=='zero': s['records'][0]['end_us']=0
            elif mode=='reversed': s['records'][0]['end_us']=-1
            elif mode=='boolean': s['records'][0]['start_us']=False
            elif mode=='float': s['records'][0]['start_us']=0.0
            elif mode=='point_interval': s['records'][0]['kind']='point_observation'
            elif mode=='duplicate': s['records']*=2
            elif mode=='clock': s['records'][0]['clock_id']='absent'
            elif mode=='extra': s['records'][0]['interpolate']=True
            with self.assertRaises((ei.ContractError,ValidationError)): state.execute(s,query())

    def test_input_ownership_and_context_changes(self):
        s=source([interval('a',0,30)]);q=query();original=deepcopy((s,q))
        r=state.execute(s,q);self.assertEqual((s,q),original)
        s['assertion_policy_id']='different-policy'
        self.assertNotEqual(state.execute(s,q)['context_id'],r['context_id'])
        self.assertEqual(r['source'],original[0])

    def test_rdf_roundtrip_and_class_only_extension(self):
        s=source([interval('a',0,30),point('b',30)])
        graph=state.encode(s)
        self.assertEqual(state.decode(graph),s)
        self.assertEqual(state.execute(state.decode(graph),query())['status'],'HOLDS')
        ontology=Graph().parse(ei.ROOT/'ontology/state-validity-description-profile.ttl',format='turtle')
        for field in state.FIELDS-cr.FIELDS:
            self.assertIn((cr.CP['Field_'+field],RDFS.subClassOf,ei.S.InformationObject),ontology)
        self.assertFalse(list(ontology.subjects(RDF.type,OWL.ObjectProperty)))
        self.assertFalse(list(ontology.subjects(RDF.type,OWL.DatatypeProperty)))
        self.assertEqual(set(graph.predicates()),{RDF.type,ei.S.hasDirectPart,ei.S.refersTo,ei.S.hasValue})
        self.assertNotIn(ei.S.TimeInterval,set(graph.objects(None,RDF.type)))
        self.assertNotIn(ei.S.Process,set(graph.objects(None,RDF.type)))

    def test_cli_and_roundtrip_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph=Path(tmp)/'source.ttl'
            run=subprocess.run([sys.executable,'-m','patterns.state_validity','--source',
                'examples/state-validity/continuous.json','--query','examples/state-validity/query.json','--graph',str(graph)],
                cwd=ei.ROOT,text=True,capture_output=True,check=True)
            self.assertEqual(json.loads(run.stdout)['status'],'HOLDS')
            self.assertEqual(len(state.decode(Graph().parse(graph))['records']),2)


if __name__=='__main__': unittest.main()
