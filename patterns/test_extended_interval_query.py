"""Independent finite-grid checks for the extended interval profile."""
from copy import deepcopy
from itertools import product
import json
import subprocess
import sys
import unittest
from jsonschema import ValidationError
from . import bounded_intervals as bt, bounded_reference as reference, exact_intervals as ei
from . import extended_interval_query as extended
from .temporal_stn import Network, Edge, ZERO, satisfies
from .test_bounded_intervals import fixture


def query(c):
    return {'profile':extended.PROFILE,'id':'test','slots':[
        {'id':'a','class_iri':str(bt.KINDS['infusion'])},
        {'id':'b','class_iri':str(bt.KINDS['specimen_collection'])}], 'constraints':[{'id':'c',**c}]}


def truth(c, w):
    s,e,u,v=(w[k] for k in ('as','ae','bs','be'))
    op=c['operator']
    if op in extended.ALLEN:
        return ei.allen_relation((s,e),(u,v)) == op
    if op == 'duration': return c['minimum_us'] <= e-s <= c['maximum_us']
    if op == 'minimum_overlap': return min(e,v)-max(s,u) >= c['minimum_us']
    return c['min_gap_us'] <= u-e <= c['max_gap_us']


class ExtendedTests(unittest.TestCase):
    def test_all_predicates_exhaustive_grid(self):
        binding={'a':{'start_var':'as','end_var':'ae'},'b':{'start_var':'bs','end_var':'be'}}
        variables={v:{'clock_id':'c'} for v in ('as','ae','bs','be')}
        cs=[{'operator':op,'left':'a','right':'b'} for op in extended.ALLEN]
        cs += [{'operator':'minimum_overlap','left':'a','right':'b','minimum_us':d} for d in (1,2,3)]
        cs += [{'operator':'duration','slot':'a','minimum_us':1,'maximum_us':2},
               {'operator':'gap','left':'a','right':'b','min_gap_us':-1,'max_gap_us':2}]
        for c in cs:
            edges,_=extended.compile_edges(binding,[{'id':'c',**c}],variables)
            for s,e,u,v in product(range(5),repeat=4):
                if s>=e or u>=v: continue
                w=dict(zip(('as','ae','bs','be'),(s,e,u,v)))
                self.assertEqual(all(satisfies(w,x) for x in edges), truth(c,w),(c,w))

    def test_uncertainty_against_enumerated_worlds(self):
        source=fixture([('a','infusion',(0,1),(2,4)),('b','specimen_collection',(1,3),(3,5))])
        snapshot=bt.prepare(source)
        worlds=reference.worlds(source,('P','E'))
        cs=[{'operator':op,'left':'a','right':'b'} for op in extended.ALLEN]
        cs += [{'operator':'minimum_overlap','left':'a','right':'b','minimum_us':2},
               {'operator':'duration','slot':'a','minimum_us':2,'maximum_us':3}]
        for c in cs:
            result=extended.execute(snapshot,query(c))['trajectories'][0]['bindings'][0]
            matches=[truth(c,w) for w in worlds]
            self.assertEqual(result['possible'],any(matches),c)
            self.assertEqual(result['certain'],all(matches),c)
            if result['possible']: self.assertTrue(truth(c,result['possible_witness']))
            if result['status']=='POSSIBLE': self.assertFalse(truth(c,result['counterexample']))

    def test_validation_and_legacy_boundary(self):
        q=query({'operator':'during','left':'a','right':'b'})
        extended.validate(q)
        with self.assertRaises(Exception): bt.validate(q,query=True)
        for mutation in ('same','unknown','extra','boolean','reversed','duplicate'):
            bad=deepcopy(q)
            if mutation=='same': bad['constraints'][0]['right']='a'
            elif mutation=='unknown': bad['slots'][0]['class_iri']='urn:unknown'
            elif mutation=='extra': bad['constraints'][0]['minimum_us']=1
            elif mutation=='duplicate': bad['constraints'].append(deepcopy(bad['constraints'][0]))
            else:
                bad['constraints']=[{'id':'c','operator':'duration','slot':'a','minimum_us':True if mutation=='boolean' else 3,'maximum_us':2}]
            with self.assertRaises((ei.ContractError,ValidationError)): extended.validate(bad)

    def test_clock_mismatch_and_fixed_witness(self):
        src=fixture([('a1','infusion',(0,0),(1,1)),('a2','infusion',(1,1),(2,2)),
                     ('b','specimen_collection',(1,2),(3,3))])
        result=extended.execute(bt.prepare(src),query({'operator':'meets','left':'a','right':'b'}))
        self.assertEqual(result['certain_patient_ids'],[])
        self.assertEqual(result['possible_patient_ids'],['P'])
        src['clocks'].append({**src['clocks'][0],'clock_id':'d'})
        for v in src['variables']:
            if v['id'].startswith('b'): v['clock_id']='d'
        result=extended.execute(bt.prepare(src),query({'operator':'during','left':'a','right':'b'}))
        self.assertTrue(all(b['status']=='INCOMPARABLE' for b in result['trajectories'][0]['bindings']))

    def test_example_cli(self):
        run=subprocess.run([sys.executable,'-m','patterns.extended_interval_query','--source',
            'examples/extended-interval-query/source.json','--query','examples/extended-interval-query/query.json'],
            cwd=ei.ROOT,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(run.stdout)['certain_patient_ids'],['P'])


if __name__=='__main__': unittest.main()
