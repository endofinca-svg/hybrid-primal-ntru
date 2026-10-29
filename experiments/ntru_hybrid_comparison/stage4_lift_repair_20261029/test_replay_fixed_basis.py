"""Replay transport, coverage and safety tests; no lattice backend execution."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from . import replay_fixed_basis as replay


def fixture():
    vector = [0, 1, 0, 0, 1, 0]
    rows = [[int(i == j) for j in range(6)] for i in range(6)]
    public = {'schema': 'ntru-e2e-public-v1', 'instance_id': 'synthetic-replay-test',
              'n': 4, 'q': 17, 'ring': 'x^n+1', 'h': [1, 0, 0, 0],
              'distribution': {'kind': 'fixed_weight_ternary', 'w_f': 1, 'w_g': 1}}
    bundle = {'public_instance': public, 'coordinates': {'P': [0], 'C': [1, 2], 'T': [3]},
              'targets': [{'used': [0] * 6}]}
    mapping = {'backend_integer_id': 0, 'candidate_sha_id': 'synthetic', 'target_index': 0, 'u': [0]}
    row = {'phase': 'post', 'backend_integer_id': 0, 'tail': [0], 'target_index': 0,
           'candidate_sha_id': 'synthetic', 'sign': -1, 'slot_id': 0,
           'projection_residual': [0.125] * 40, 'projection_gh_sub': 2.0,
           'ancestor_slot_id': 999, 'ancestor_uid': 999, 'kernel_changed_entry': True}
    mapper = SimpleNamespace(rows=rows, gh_sub=2.0,
                             G=SimpleNamespace(babai=Mock(return_value=vector)),
                             _lift=Mock(return_value=(vector, vector, 2)))
    return bundle, mapping, row, mapper


class SavedProjectionReplay(unittest.TestCase):
    def test_all_40_coordinates_and_sign_reach_repaired_lift(self):
        bundle, mapping, row, mapper = fixture()
        repaired = replay.repair_row(row, bundle, mapping, mapper, 'D-skip')
        mapper._lift.assert_called_once_with([0] * 6, [-0.125] * 40)
        self.assertEqual(repaired['coefficients'], [0, 1, 0, 0, 1, 0])
        self.assertEqual(repaired['ambient_residual_sq'], 2)
        self.assertEqual(repaired['full_norm_sq'], 2)
        self.assertEqual(repaired['ambient_residual'], [0, 1, 0, 0, 1, 0])
        self.assertTrue(repaired['public_validation']['public_valid'])

    def test_direct_E_does_not_call_slicer_lift(self):
        bundle, mapping, row, mapper = fixture()
        repaired = replay.repair_row(row, bundle, mapping, mapper, 'E')
        mapper._lift.assert_not_called()
        mapper.G.babai.assert_called_once_with([0] * 6)
        self.assertTrue(repaired['public_validation']['public_valid'])

    def test_wrong_gh_and_corrupt_distance_are_rejected(self):
        bundle, mapping, row, mapper = fixture()
        row['projection_gh_sub'] = 3
        with self.assertRaisesRegex(ValueError, 'normalization'):
            replay.repair_row(row, bundle, mapping, mapper, 'D-skip')
        row['projection_gh_sub'] = 2
        mapper._lift.return_value = ([0] * 6, [0] * 6, 9)
        with self.assertRaisesRegex(ValueError, 'exact integer'):
            replay.repair_row(row, bundle, mapping, mapper, 'D-skip')

    def test_tail_and_ID_mapping_and_projection_shape_are_checked(self):
        bundle, mapping, row, mapper = fixture()
        replay.validate_source_row(row, {0: mapping}, 'D-skip')
        wrong = deepcopy(row)
        wrong['tail'] = [1]
        with self.assertRaisesRegex(ValueError, 'mapping'):
            replay.validate_source_row(wrong, {0: mapping}, 'D-skip')
        wrong = deepcopy(row)
        wrong['projection_residual'] = [0] * 39
        with self.assertRaisesRegex(ValueError, '40-dimensional'):
            replay.validate_source_row(wrong, {0: mapping}, 'D-skip')

    def test_Dskip_original_slot_selection_does_not_use_ancestor_labels(self):
        bundle, mapping, row, mapper = fixture()
        repaired = replay.repair_row(row, bundle, mapping, mapper, 'D-skip')
        baseline = {0: repaired}
        comparison = replay.e_consistency('D-skip', row, repaired, baseline)
        self.assertTrue(comparison['all_match'])
        row['slot_id'] = 1
        self.assertIsNone(replay.e_consistency('D-skip', row, repaired, baseline))
        row['slot_id'] = 0
        self.assertIsNone(replay.e_consistency('D-probe-400', row, repaired, baseline))

    def test_post_filter_preserves_original_line_numbers_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'telemetry.jsonl'
            records = [{'phase': 'pre', 'uid': 1}, {'phase': 'post', 'uid': 3},
                       {'phase': 'post', 'uid': 2}, {'phase': 'babai', 'uid': 4}]
            path.write_text(''.join(json.dumps(row) + '\n' for row in records))
            selected = list(replay.selected_rows(path, 'D-probe-1'))
            self.assertEqual([(line, row['uid']) for line, row in selected], [(2, 3), (3, 2)])
            self.assertEqual(list(replay.selected_rows(path, 'E')), [(4, records[3])])

    def test_coverage_allows_probe_group_redistribution_and_detects_prefix(self):
        counts = Counter({i: 16 for i in range(80)})
        counts[0], counts[1] = 2, 30
        self.assertTrue(replay.coverage(counts, counts, 1280)['every_saved_selected_record_replayed'])
        partial = counts.copy()
        partial[1] -= 1
        self.assertFalse(replay.coverage(counts, partial, 1280)['every_saved_selected_record_replayed'])
        missing = counts.copy()
        del missing[3]
        self.assertFalse(replay.coverage(counts, missing, 1280)['all_80_replayed_targets'])

    def test_request_rejects_witness_unknown_sources_and_expired_work(self):
        now = time.monotonic()
        request = {'configuration': 'D-skip', 'input_path': str(replay.STAGE3 / 'pattern_2/input.json'),
                   'source_path': str(replay.STAGE3 / 'decode_p2_D-skip/target_telemetry.jsonl'),
                   'work_deadline': now + 20, 'hard_deadline': now + 25, 'global_deadline': now + 30,
                   'started_monotonic': now}
        replay.validate_request(request)
        with self.assertRaisesRegex(ValueError, 'witness'):
            replay.validate_request({**request, 'anchor': [1, 0]})
        with self.assertRaisesRegex(ValueError, 'only original'):
            replay.validate_request({**request, 'source_path': str(replay.STAGE3 / 'decode_p2_D-normal/target_telemetry.jsonl')})
        with self.assertRaises(TimeoutError):
            replay.check_deadline({**request, 'work_deadline': now - 1})


if __name__ == '__main__':
    unittest.main()
