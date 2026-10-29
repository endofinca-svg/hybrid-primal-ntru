"""Dry continuation checks; never repeats an already-started configuration."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from . import continue_run as run
from .common import write


class Continuation(unittest.TestCase):
    def exercise(self, probe_gate=True):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            write(stage/'plan.json', json.loads((run.HERE/'plan.json').read_text()))
            write(stage/'diagnostic_runtime.json', {'environment': {}})
            first = [{'task_id': name, 'status': 'completed', 'cleanup_verified': True}
                     for name in ('pattern_0','pattern_2','witness_check','decode_p0_E')]
            first.append({'task_id':'decode_p0_D-normal','status':'worker_nonzero_exit',
                          'cleanup_verified':True})
            first.extend({'task_id':'decode_p2_'+name,'status':'not_run'} for name in
                         ('E','D-skip','D-normal','D-probe-1','D-probe-400'))
            write(stage/'owner.final.json', {'records':first})
            original = (stage/'owner.final.json').read_bytes()
            write(stage/'pattern_2/basis_manifest.json', {'eligible_for_same_basis_diagnostics':True})
            write(stage/'witness_consistency.json', {'all_checks_passed':True, 'patterns':[
                {'pattern_index':2,'actual_saved_input_public_mapping_matches':True}]})
            calls = []
            def supervise(argv, **options):
                task = Path(options['stdout_path']).parent
                calls.append((task.name, argv, json.loads((task/'request.json').read_text())))
                write(task/'result.json', {'status':'completed','success':False,
                    'probe_400_gate_passed':probe_gate})
                return {'execution_status':'completed','returncode':0,'cleanup_verified':True}
            origin = time.monotonic()-1100
            with patch.object(run,'HERE',stage), patch.object(run,'supervise_command',supervise), \
                    patch.object(run,'access_check',return_value={'all_checks_passed':True}), \
                    redirect_stdout(io.StringIO()):
                run.owner({'global_started_monotonic':origin})
            self.assertEqual((stage/'owner.final.json').read_bytes(), original)
            final = json.loads((stage/'continuation_owner.final.json').read_text())
            return calls, final, origin

    def test_only_original_never_started_configs_and_original_clock(self):
        calls, final, origin = self.exercise()
        self.assertEqual([v[0] for v in calls], ['decode_p2_E','decode_p2_D-skip',
            'decode_p2_D-normal','decode_p2_D-probe-1','decode_p2_D-probe-400','independent_validation'])
        self.assertEqual(final['global_started_monotonic'], origin)
        self.assertEqual(sum(r['task_id']=='decode_p0_D-normal' for r in final['records']),1)
        for task,argv,request in calls:
            self.assertIn('4096',argv)
            self.assertIn('-yy',argv)
            self.assertTrue(any('audit_entry_v2.py' in item for item in argv))
            self.assertLessEqual(request['hard_deadline'], origin+4800)
            self.assertEqual(request['hard_deadline']-request['work_deadline'],5)

    def test_probe_gate_retained(self):
        calls, final, origin = self.exercise(probe_gate=False)
        self.assertNotIn('decode_p2_D-probe-400',[v[0] for v in calls])
        last = next(r for r in final['records'] if r['task_id']=='decode_p2_D-probe-400')
        self.assertEqual(last['status'],'not_run')
