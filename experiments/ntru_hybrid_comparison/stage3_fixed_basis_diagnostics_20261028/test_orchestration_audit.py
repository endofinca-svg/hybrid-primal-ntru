"""Independent orchestration integration tests; no numerical backend work."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from . import decode_worker, run
from .common import write


class OwnerIntegration(unittest.TestCase):
    def exercise_owner(self, *, witness_completed=True, stale_witness=False,
                       basis0_eligible=True, probe1_gate=True,
                       probe1_completed=True, basis0_completed=True):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            original_plan = json.loads((run.HERE / 'plan.json').read_text())
            write(stage / 'plan.json', original_plan)
            write(stage / 'diagnostic_runtime.json', {'environment': {}})
            witness = {'all_checks_passed': True, 'patterns': [
                {'pattern_index': index, 'actual_saved_input_public_mapping_matches': True}
                for index in (0, 2)]}
            if stale_witness:
                write(stage / 'witness_consistency.json', witness)

            def supervise(argv, **options):
                output = Path(options['stdout_path']).parent
                task_id = output.name
                (output / 'file_access.jsonl').touch()
                (output / 'file_opens.log').touch()
                request = json.loads((output / 'request.json').read_text())
                calls.append({'task_id': task_id, 'request': request, 'argv': argv,
                              'options': options})
                if task_id.startswith('pattern_'):
                    index = int(task_id.rsplit('_', 1)[1])
                    write(output / 'basis_manifest.json', {
                        'eligible_for_same_basis_diagnostics': index == 2 or basis0_eligible})
                if task_id == 'witness_check':
                    if not witness_completed:
                        return {'execution_status': 'timeout', 'returncode': -15,
                                'cleanup_verified': True}
                    write(stage / 'witness_consistency.json', witness)
                write(output / 'result.json', {'status': 'completed', 'success': False,
                      'probe_400_gate_passed': probe1_gate})
                if ((task_id == 'decode_p2_D-probe-1' and not probe1_completed)
                        or (task_id == 'pattern_0' and not basis0_completed)):
                    # A useful partial artifact may have been saved before
                    # a later stage failed; it cannot bless task completion.
                    write(output / 'result.json', {'status': 'error',
                          'probe_400_gate_passed': probe1_gate})
                    return {'execution_status': 'worker_nonzero_exit', 'returncode': 2,
                            'cleanup_verified': True}
                return {'execution_status': 'completed', 'returncode': 0,
                        'cleanup_verified': True}

            start = time.monotonic()
            with patch.object(run, 'HERE', stage), patch.object(run, 'supervise_command', supervise), redirect_stdout(io.StringIO()):
                run.owner({'global_started_monotonic': start})
            final = json.loads((stage / 'owner.final.json').read_text())
            return calls, final, start

    def test_repository_path_and_exact_finite_schedule(self):
        self.assertEqual(run.REPO, Path(__file__).resolve().parents[3])
        calls, final, start = self.exercise_owner()
        expected = ['pattern_0', 'pattern_2', 'witness_check', 'decode_p0_E',
                    'decode_p0_D-normal', 'decode_p2_E', 'decode_p2_D-skip',
                    'decode_p2_D-normal', 'decode_p2_D-probe-1',
                    'decode_p2_D-probe-400', 'independent_validation']
        self.assertEqual([row['task_id'] for row in calls], expected)
        self.assertEqual([row['task_id'] for row in final['records']], expected)
        # All synthetic solver success fields are false: the probe400 gate
        # depends solely on probe1 control/counter/state integrity.
        self.assertIn('decode_p2_D-probe-400', [row['task_id'] for row in calls])
        for call in calls:
            request, options = call['request'], call['options']
            self.assertEqual(options['ownership'], 'process_group')
            self.assertEqual(request['hard_deadline'] - request['work_deadline'], 5)
            self.assertLessEqual(request['hard_deadline'], start + 4800)
            self.assertLess(options['hard_deadline'], request['hard_deadline'])
            if call['task_id'].startswith('decode_'):
                self.assertNotIn('anchor', request)
                self.assertNotIn('witness', request)
                self.assertNotIn('correct_tail', request)
                expected_budget = 120 if request['configuration'] == 'E' else 600
                self.assertEqual(request['budget_seconds'], expected_budget)

    def test_failed_current_witness_cannot_use_stale_success(self):
        calls, final, _ = self.exercise_owner(witness_completed=False, stale_witness=True)
        self.assertFalse(any(row['task_id'].startswith('decode_') for row in calls))
        decoded = [row for row in final['records'] if row['task_id'].startswith('decode_')]
        self.assertEqual(len(decoded), 7)
        self.assertTrue(all(row['status'] == 'not_run' and row['wall_seconds'] is None for row in decoded))

    def test_index0_digest_mismatch_does_not_block_index2(self):
        calls, final, _ = self.exercise_owner(basis0_eligible=False)
        identifiers = [row['task_id'] for row in calls]
        self.assertNotIn('decode_p0_E', identifiers)
        self.assertNotIn('decode_p0_D-normal', identifiers)
        self.assertIn('decode_p2_E', identifiers)
        self.assertIn('decode_p2_D-probe-400', identifiers)

    def test_probe_integrity_failure_blocks_probe400_only(self):
        calls, final, _ = self.exercise_owner(probe1_gate=False)
        identifiers = [row['task_id'] for row in calls]
        self.assertIn('decode_p2_D-probe-1', identifiers)
        self.assertNotIn('decode_p2_D-probe-400', identifiers)
        self.assertEqual(next(row for row in final['records'] if row['task_id'] == 'decode_p2_D-probe-400')['status'], 'not_run')

    def test_failed_probe1_cannot_use_partial_integrity_flag(self):
        calls, final, _ = self.exercise_owner(probe1_gate=True, probe1_completed=False)
        identifiers = [row['task_id'] for row in calls]
        self.assertIn('decode_p2_D-probe-1', identifiers)
        self.assertNotIn('decode_p2_D-probe-400', identifiers)

    def test_failed_basis_finalization_cannot_use_partial_manifest(self):
        calls, final, _ = self.exercise_owner(basis0_eligible=True, basis0_completed=False)
        identifiers = [row['task_id'] for row in calls]
        self.assertNotIn('decode_p0_E', identifiers)
        self.assertNotIn('decode_p0_D-normal', identifiers)
        self.assertIn('decode_p2_E', identifiers)

    def test_actual_owner_request_shape_reaches_decoder_input_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            now = time.monotonic()
            request = {'input_path': '/synthetic/missing/input.json', 'configuration': 'E',
                       'pattern_index': 0, **run.window(now, 120, now)}
            with patch.object(decode_worker, 'load_input_bundle', side_effect=RuntimeError('synthetic input boundary reached')) as load:
                result = decode_worker.run(request, directory)
            load.assert_called_once_with(request['input_path'], require_reduced_verified=True)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['error']['message'], 'synthetic input boundary reached')


if __name__ == '__main__':
    unittest.main()
