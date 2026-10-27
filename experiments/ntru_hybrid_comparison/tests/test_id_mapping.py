"""Pure candidate identity tests; returned records are explicitly simulated."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
import unittest

from experiments.ntru_hybrid_comparison.candidates import (
    CandidateContext, CandidatePolicy, build_candidates,
)
from experiments.ntru_hybrid_comparison.id_mapping import (
    build_id_mapping, reconcile_results, target_sha256, validate_id_mapping,
)


class IDMappingTests(unittest.TestCase):
    def setUp(self):
        self.batch = build_candidates(
            CandidateContext("identity-test-only", "0" * 64, 8, (7, 1)),
            CandidatePolicy("full_nonzero"))
        self.targets = [[2 ** 65 + index, -2 ** 80, 0]
                        for index in range(len(self.batch.entries))]
        self.mapping = build_id_mapping(self.batch, self.targets)

    def test_mapping_roundtrip_keeps_full_candidates_targets_and_order(self):
        restored = json.loads(json.dumps(self.mapping))
        self.assertEqual(validate_id_mapping(restored, batch=self.batch,
                                             targets=self.targets), self.mapping)
        self.assertEqual([row["backend_integer_id"] for row in restored], list(range(8)))
        self.assertEqual([row["target_index"] for row in restored], list(range(8)))
        self.assertEqual([row["u"] for row in restored],
                         [list(entry.u) for entry in self.batch.entries])
        expected_digest = sha256(json.dumps(self.targets[0], separators=(",", ":"))
                                 .encode("ascii")).hexdigest()
        self.assertEqual(restored[0]["target_sha256"], expected_digest)

    def test_reordered_results_restore_identity_from_explicit_id(self):
        results = [{"backend_integer_id": index, "simulated_payload": [index, -index]}
                   for index in (6, 2, 7, 1, 0, 4, 3, 5)]
        reconciled = reconcile_results(self.mapping, results)
        self.assertTrue(reconciled["complete"])
        self.assertEqual(reconciled["status"], "complete")
        for index, row in enumerate(reconciled["ordered_results"]):
            self.assertEqual(row["candidate_sha_id"], self.batch.entries[index].candidate_id)
            self.assertEqual(row["results"][0]["simulated_payload"], [index, -index])
            self.assertEqual(row["identity_status"], "returned")

    def test_unknown_duplicate_and_missing_are_all_explicit(self):
        results = [{"backend_integer_id": 3, "simulated": "a"},
                   {"backend_integer_id": 3, "simulated": "b"},
                   {"backend_integer_id": 100, "simulated": "unknown"}]
        reconciled = reconcile_results(self.mapping, results)
        self.assertFalse(reconciled["complete"])
        self.assertEqual(reconciled["status"], "invalid_return_identity")
        self.assertEqual(reconciled["duplicate_ids"], [3])
        self.assertEqual(reconciled["unknown_ids"], [100])
        self.assertEqual(reconciled["missing_ids"], [0, 1, 2, 4, 5, 6, 7])
        self.assertEqual([r["simulated"] for r in
                          reconciled["ordered_results"][3]["results"]], ["a", "b"])
        self.assertEqual(reconcile_results(self.mapping, []) ["status"], "incomplete")

    def test_simulated_reconciliation_does_not_change_candidates_or_counters(self):
        before = self.batch.to_manifest()
        mapping_before = deepcopy(self.mapping)
        results = [{"backend_integer_id": 0, "nested": [1, 2]}]
        reconciled = reconcile_results(self.mapping, results)
        reconciled["ordered_results"][0]["u"][0] = 99
        reconciled["ordered_results"][0]["results"][0]["nested"][0] = 99
        self.assertEqual(self.batch.to_manifest(), before)
        self.assertEqual(self.mapping, mapping_before)
        self.assertEqual(results[0]["nested"], [1, 2])
        counts = asdict(self.batch.counts)
        self.assertEqual(counts["submitted_count"], 0)
        self.assertEqual(counts["completed_count"], 0)
        self.assertIsNone(counts["projection_group_count"])

    def test_equal_targets_and_opposite_tails_keep_distinct_ids(self):
        # Full-target equality is even stronger than a backend projection collision.
        # Neither equality merges external candidates.
        mapping = build_id_mapping(self.batch, [[0, 0, 0]] * 8)
        self.assertEqual(len(mapping), 8)
        self.assertEqual(len({row["candidate_sha_id"] for row in mapping}), 8)
        self.assertEqual(len({row["target_sha256"] for row in mapping}), 1)
        by_tail = {tuple(row["u"]): row for row in mapping}
        for tail, row in by_tail.items():
            self.assertNotEqual(row["backend_integer_id"],
                                by_tail[tuple(-x for x in tail)]["backend_integer_id"])

    def test_tampering_identity_order_tail_or_target_is_rejected(self):
        mutations = [
            lambda m: m[0].update(candidate_sha_id=m[1]["candidate_sha_id"]),
            lambda m: m[0].update(candidate_sha_id="f" * 64),
            lambda m: m[0].update(backend_integer_id=True),
            lambda m: m[0].update(backend_integer_id=1),
            lambda m: m[0].update(target_index=1),
            lambda m: m[0].update(u=[0, 0]),
            lambda m: m[0].update(u=list(m[1]["u"])),
            lambda m: m[0].update(target_sha256="0" * 64),
            lambda m: m.reverse(),
            lambda m: m[0].update(extra="ignored metadata is forbidden"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                mapping = deepcopy(self.mapping)
                mutate(mapping)
                with self.assertRaises(ValueError):
                    validate_id_mapping(mapping, batch=self.batch, targets=self.targets)
        targets = deepcopy(self.targets)
        targets[0][0] += 1
        with self.assertRaises(ValueError):
            validate_id_mapping(self.mapping, targets=targets)

    def test_invalid_targets_and_positional_or_malformed_results_are_rejected(self):
        for target in ([], [True], [1.0], ["1"], {1, 2}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                target_sha256(target)
        for results in ([{"payload": 0}], [{"backend_integer_id": True}],
                        [{"backend_integer_id": 1.0}], [0], {0: {}}):
            with self.subTest(results=results), self.assertRaises(ValueError):
                reconcile_results(self.mapping, results)
        with self.assertRaises(ValueError):
            build_id_mapping(self.batch, self.targets[:-1])
        with self.assertRaises(ValueError):
            build_id_mapping(self.batch, self.targets[:-1] + [[0]])


if __name__ == "__main__":
    unittest.main()
