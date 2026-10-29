"""Targeted controls, state pairing and real small-backend interface checks.

The small public fixture is dimension28/n15; these are preparation tests, not
main-instance numerical diagnostics or attack results.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison.inputs import content_sha256, create_working_copy, load_input_bundle
from experiments.ntru_hybrid_comparison.fixed_backend import FixedSlicerBackend
from .diagnostic_backend import (DiagnosticSlicer, limits, stop_observation, ancestor_uid, probe_gate)
from .common import Journal
from .decode_worker import run

HERE=Path(__file__).resolve().parent

class ControlTests(unittest.TestCase):
    def test_normal_default_and_probe_change_only_saturation_and_limit(self):
        self.assertEqual(limits('D-normal'),{'skip':False,'bypass_saturation':False,'max_iterations':400})
        self.assertEqual(limits('D-probe-400'),{'skip':False,'bypass_saturation':True,'max_iterations':400})
        self.assertEqual(limits('D-probe-1'),{'skip':False,'bypass_saturation':True,'max_iterations':1})
        self.assertTrue(limits('D-skip')['skip'])

    def test_entry_order_statistic_is_not_group_coverage(self):
        entries=[{'stored_len_sq':float(i),'slot_id':i,'uid':i,'group_id':0} for i in range(8)]
        record=stop_observation({'entries':entries},2,[3.0],1.0)
        self.assertEqual(record['index'],2)
        self.assertTrue(record['saturated'])
        self.assertEqual(record['group_coverage_below'],[0])
        record=stop_observation({'entries':entries},2,[2.0/(1+1e-4)],1.0)
        self.assertFalse(record['saturated'])

    def test_skip_does_not_call_kernel_but_preserves_original_sort_boundary(self):
        class S:
            calls=0;sorts=0
            def diagnostic_sort_targets(self):self.sorts+=1
            def bdgl_like_sieve(self,*args):self.calls+=1
        backend=DiagnosticSlicer.__new__(DiagnosticSlicer)
        backend.diagnostic_options=limits('D-skip')
        backend.metrics={'slicer_kernel_calls':0,'slicer_kernel_completed_calls':0}
        slicer=S()
        backend.call_kernel(slicer)
        self.assertEqual((slicer.calls,slicer.sorts),(0,1))

    def test_lineage_follows_reduction_source_across_slot_replacement(self):
        lineage=[[0,10,11,0,0],[1,20,11,1,0],[0,11,12,1,0],[1,30,12,2,0]]
        self.assertEqual(ancestor_uid(12,lineage,{10,20,30}),10)
        self.assertIsNone(ancestor_uid(99,lineage,{10,20,30}))

    def test_probe_gate_depends_on_integrity_not_improvement(self):
        post={'entries':[{'slot_id':1,'uid':7,'group_id':0,'stored_len_sq':99.0,'residual':[9.0]}],
              'iterations_entered':1,'iterations_completed':1,'lineage':[]}
        self.assertTrue(probe_gate('D-probe-1',deepcopy(post),post,{}, {'itercount_slicer':1},True))
        self.assertFalse(probe_gate('D-probe-1',deepcopy(post),post,{}, {'itercount_slicer':0},True))

    def test_unfinished_stage_keeps_null_and_inherited_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Journal(tmp,{'work_deadline':time.monotonic()+10})
            with self.assertRaises(RuntimeError):
                with journal.stage('interrupted'):
                    raise RuntimeError('test interruption')
            saved=json.loads((Path(tmp)/'result.json').read_text())
            self.assertIsNone(saved['timings']['interrupted'])
            self.assertEqual(saved['current_stage'],'interrupted')

    def test_root_request_shape_is_accepted_before_input_load(self):
        request={'input_path':'unreadable_test_input','configuration':'E',
                 'work_deadline':time.monotonic()+1,'hard_deadline':time.monotonic()+6,
                 'pattern_index':0,'started_monotonic':time.monotonic(),
                 'budget_seconds':120,'cleanup_reserve_seconds':5}
        with tempfile.TemporaryDirectory() as tmp:
            result=run(request,tmp)
        self.assertEqual(result['error']['type'],'FileNotFoundError')


class RealBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import g6k.slicer
        assert hasattr(g6k.slicer.RandomizedSlicer,'diagnostic_snapshot'),g6k.slicer.__file__
        cls.bundle=load_input_bundle(HERE.parent/'phase2c_results_20261026/micro_n15_d28.public_input.json',require_reduced_verified=True)
        assert cls.bundle['public_instance']['n']==15
        cls.config={'float_type':'mpfr','precision_bits':192,'sieve_dim':28,'seed':20261026,
                    'db_size':128,'nrand':2,'max_iterations':1,'preprocessing':'bdgl2',
                    'buckets':4,'blocks':1,'multi_hash':1,'interface_test_mode':False,
                    'force_kernel_iterations':False}
        cls.ids=[r['backend_integer_id'] for r in cls.bundle['id_mapping']]
        cls.targets=[r['used'] for r in cls.bundle['targets']]
        cls.bounds=[1000000]*len(cls.ids)

    def backend(self,configuration):
        backend=DiagnosticSlicer.__new__(DiagnosticSlicer)
        backend.diagnostic_options=limits(configuration)
        FixedSlicerBackend.__init__(backend,create_working_copy(self.bundle),self.config)
        backend.preprocess()
        return backend

    def test_normal_matches_original_and_skip_preserves_every_lift(self):
        original=FixedSlicerBackend(create_working_copy(self.bundle),self.config)
        original.preprocess();instances=[]
        factory=original._slicer_factory
        def recording(*args,**kw):
            instance=factory(*args,**kw);instances.append(instance);return instance
        original._slicer_factory=recording
        expected=original.decode(self.targets,self.bounds,self.ids)
        backend=self.backend('D-normal')
        with tempfile.TemporaryDirectory() as tmp:
            slicer,groups,_=backend.construct_targets(self.targets,self.bounds,self.ids,Path(tmp)/'iterations.tsv')
            backend.call_kernel(slicer)
        self.assertEqual(slicer.diagnostic_snapshot(),instances[0].diagnostic_snapshot())
        self.assertEqual(slicer.stats,instances[0].stats)
        self.assertEqual(slicer.diagnostic_snapshot()['iterations_completed'],0)
        skip=self.backend('D-skip')
        with tempfile.TemporaryDirectory() as tmp:
            slicer2,groups2,_=skip.construct_targets(self.targets,self.bounds,self.ids,Path(tmp)/'iterations.tsv')
            skip.call_kernel(slicer2)
        self.assertEqual(slicer.diagnostic_snapshot(),slicer2.diagnostic_snapshot())
        lifts=[]
        for residual,gid in slicer2.itervalues_cdb_t(return_with_index=True):
            for member in groups2[gid]['members']:
                coefficients,vector,distance=skip._mapper._lift(member['target'],[member['sign']*float(x) for x in residual])
                lifts.append((member['target_id'],vector))
        self.assertEqual(set(x[0] for x in lifts),set(self.ids))
        self.assertEqual(len(lifts),len(slicer2.diagnostic_snapshot()['entries'])*len(groups2[0]['members']))
        self.assertEqual(skip.metrics['slicer_kernel_calls'],0)

    def test_probe_single_iteration_and_independent_rebuild_state(self):
        left=self.backend('D-probe-1');right=self.backend('D-probe-1')
        with tempfile.TemporaryDirectory() as tmp:
            s1,g1,_=left.construct_targets(self.targets,self.bounds,self.ids,Path(tmp)/'left.tsv')
            s2,g2,_=right.construct_targets(self.targets,self.bounds,self.ids,Path(tmp)/'right.tsv')
            pre1=s1.diagnostic_snapshot();pre2=s2.diagnostic_snapshot()
            self.assertEqual(pre1,pre2)
            before=dict(s1.stats);left.call_kernel(s1);post=s1.diagnostic_snapshot()
            self.assertEqual(post['iterations_entered'],1)
            self.assertEqual(post['iterations_completed'],1)
            self.assertEqual(s1.stats['itercount_slicer'],1)
            self.assertEqual(s2.diagnostic_snapshot(),pre2)
            self.assertTrue(probe_gate('D-probe-1',pre1,post,before,dict(s1.stats),True))
            self.assertIn('iteration_completed\t1\t1', (Path(tmp)/'left.tsv').read_text())
            self.assertTrue(stop_observation(pre1,len(g1),self.bounds,left._mapper.gh_sub)['saturated'])

if __name__=='__main__':unittest.main()
