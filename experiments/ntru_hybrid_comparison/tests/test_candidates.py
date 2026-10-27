"""Pure candidate correctness tests; no lattice backend or experimental keygen."""

from dataclasses import FrozenInstanceError
from itertools import product as cartesian_product
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison import candidates as subject
from experiments.ntru_hybrid_comparison.candidates import (
    CandidateContext, CandidatePolicy, build_candidates, context_from_public,
)


def context(kappa=4, **overrides):
    values = {"instance_id": "hand-constructed", "public_sha256": "0" * 64,
              "n": 16, "tail_positions": tuple(range(kappa))}
    values.update(overrides)
    return CandidateContext(**values)


def public():
    return {"schema": "ntru-e2e-public-v1", "instance_id": "hand-constructed",
            "n": 4, "q": 7, "ring": "x^n+1", "h": [1, 0, 0, 0],
            "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}}


class CandidateTests(unittest.TestCase):
    def test_full4_matches_independent_cartesian_oracle(self):
        batch = build_candidates(context(), CandidatePolicy("full_nonzero"))
        oracle = {u for u in cartesian_product((-1, 0, 1), repeat=4) if any(u)}
        self.assertEqual(set(batch.vectors), oracle)
        self.assertEqual(len(batch.entries), 80)
        self.assertEqual(len(set(batch.vectors)), 80)
        self.assertEqual(len({item.candidate_id for item in batch.entries}), 80)
        for u in batch.vectors:
            self.assertIn(tuple(-x for x in u), oracle)

    def test_small_truncations_match_independent_exhaustive_oracle(self):
        for kappa in range(1, 6):
            for cap in range(1, kappa + 1):
                with self.subTest(kappa=kappa, cap=cap):
                    batch = build_candidates(context(kappa), CandidatePolicy("weight_range", cap))
                    oracle = {u for u in cartesian_product((-1, 0, 1), repeat=kappa)
                              if 1 <= sum(x != 0 for x in u) <= cap}
                    self.assertEqual(set(batch.vectors), oracle)

    def test_required_count_table_and_static_progress(self):
        for kappa, expected2, expected3 in ((4, 32, 64), (6, 72, 232), (8, 128, 576),
                                          (10, 200, 1160), (12, 288, 2048)):
            for cap, expected in ((2, expected2), (3, expected3)):
                with self.subTest(kappa=kappa, cap=cap):
                    batch = build_candidates(context(kappa), CandidatePolicy("weight_range", cap))
                    self.assertEqual(len(batch.entries), expected)
                    self.assertEqual(batch.counts.generated_raw_count, expected)
                    self.assertEqual(batch.counts.policy_kept_count, expected)
                    self.assertEqual(batch.counts.exact_unique_count, expected)
                    self.assertEqual(batch.counts.theoretical_full_nonzero_count, 3**kappa - 1)
                    self.assertIsNone(batch.counts.projection_group_count)
                    self.assertEqual(batch.counts.submitted_count, 0)
                    self.assertEqual(batch.counts.completed_count, 0)
                    self.assertEqual(len(set(batch.vectors)), expected)
                    for item in batch.entries:
                        self.assertEqual(len(item.u), kappa)
                        self.assertTrue(all(type(x) is int and x in (-1, 0, 1) for x in item.u))
                        self.assertEqual(item.weight, sum(x != 0 for x in item.u))
                        self.assertTrue(1 <= item.weight <= cap)

    def test_truncated_generation_only_visits_requested_sign_supports(self):
        calls = []

        def instrumented_product(alphabet, *, repeat):
            self.assertEqual(tuple(alphabet), (-1, 1))
            self.assertLessEqual(repeat, 2)
            calls.append(repeat)
            return cartesian_product(alphabet, repeat=repeat)

        with patch.object(subject, "product", instrumented_product):
            batch = build_candidates(context(12), CandidatePolicy("weight_range", 2))
        self.assertEqual(batch.counts.generated_raw_count, 288)
        self.assertEqual(batch.counts.theoretical_full_nonzero_count, 531440)
        self.assertEqual(calls.count(1), 12)
        self.assertEqual(calls.count(2), 66)

    def test_order_ids_manifest_and_digest_repeat(self):
        a = build_candidates(context(), CandidatePolicy("full_nonzero"))
        b = build_candidates(context(), CandidatePolicy("full_nonzero"))
        self.assertEqual(a, b)
        self.assertEqual(a.to_manifest(), b.to_manifest())
        self.assertEqual(a.entries[:4], b.entries[:4])
        self.assertEqual(a.vectors[:4], ((-1, 0, 0, 0), (1, 0, 0, 0),
                                        (0, -1, 0, 0), (0, 1, 0, 0)))
        self.assertEqual([x.weight for x in a.entries], sorted(x.weight for x in a.entries))
        self.assertEqual(json.loads(json.dumps(a.to_manifest())), a.to_manifest())

    def test_ids_cross_rules_and_equal_order_digest_independent_of_policy(self):
        full = build_candidates(context(), CandidatePolicy("full_nonzero"))
        capped = build_candidates(context(), CandidatePolicy("weight_range", 2))
        full_ids = {x.u: x.candidate_id for x in full.entries}
        self.assertTrue(all(x.candidate_id == full_ids[x.u] for x in capped.entries))
        all_weights = build_candidates(context(), CandidatePolicy("weight_range", 4))
        self.assertEqual(full.entries, all_weights.entries)
        self.assertEqual(full.ordered_sha256, all_weights.ordered_sha256)

    def test_exact_duplicates_keep_first_and_signs_remain_distinct(self):
        source = [(1, 0, 0, 0), (0, 0, 0, 0), (-1, 0, 0, 0),
                  (1, 0, 0, 0), (1, 1, 1, 0), (-1, 0, 0, 0)]
        batch = build_candidates(context(), CandidatePolicy("weight_range", 2),
                                 supplied_candidates=iter(source))
        self.assertEqual(batch.vectors, ((1, 0, 0, 0), (-1, 0, 0, 0)))
        self.assertEqual(batch.counts.generated_raw_count, 6)
        self.assertEqual(batch.counts.policy_kept_count, 4)
        self.assertEqual(batch.counts.exact_unique_count, 2)
        self.assertEqual(batch.counts.submitted_count, 0)
        self.assertEqual(batch.counts.completed_count, 0)
        self.assertIsNone(batch.counts.projection_group_count)

    def test_context_order_instance_key_and_dimension_influence_ids(self):
        base = build_candidates(context(), CandidatePolicy("weight_range", 1))
        for changed in (context(tail_positions=(3, 2, 1, 0)), context(instance_id="second"),
                        context(public_sha256="1" * 64), context(n=17)):
            other = build_candidates(changed, CandidatePolicy("weight_range", 1))
            self.assertEqual(base.vectors, other.vectors)
            self.assertNotEqual(base.ordered_sha256, other.ordered_sha256)
            self.assertTrue(all(a.candidate_id != b.candidate_id
                                for a, b in zip(base.entries, other.entries)))

    def test_context_policy_and_vectors_are_immutable(self):
        positions = [0, 1, 2, 3]
        ctx = context(tail_positions=positions)
        positions.reverse()
        self.assertEqual(ctx.tail_positions, (0, 1, 2, 3))
        policy = CandidatePolicy("weight_range", 2)
        batch = build_candidates(ctx, policy)
        for obj, field, value in ((ctx, "n", 4), (policy, "max_weight", 1),
                                  (batch.counts, "submitted_count", 32)):
            with self.assertRaises(FrozenInstanceError):
                setattr(obj, field, value)
        self.assertIsInstance(batch.entries, tuple)
        self.assertIsInstance(batch.entries[0].u, tuple)

    def test_invalid_contexts_reject_kappa_zero_invalid_or_duplicate_positions(self):
        bad = ({"tail_positions": ()}, {"tail_positions": (0, 0)},
               {"tail_positions": (0, 16)}, {"tail_positions": (True, 1)},
               {"tail_positions": (1.0,)}, {"tail_positions": {0, 1}},
               {"tail_positions": {0: "a", 1: "b"}}, {"n": 0}, {"n": True},
               {"public_sha256": "invalid"}, {"instance_id": ""})
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                context(**overrides)

    def test_invalid_policies_and_supplied_tails_rejected(self):
        for args in (("unknown",), ("weight_range",), ("weight_range", 0),
                     ("weight_range", True), ("weight_range", 1.5), ("full_nonzero", 2)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                CandidatePolicy(*args)
        with self.assertRaises(ValueError):
            build_candidates(context(), CandidatePolicy("weight_range", 5))
        with self.assertRaises(ValueError):
            build_candidates(context(), CandidatePolicy("full_nonzero"),
                             supplied_candidates={(1, 0, 0, 0)})
        for tail in ((1, 0), (True, 0, 0, 0), (1.0, 0, 0, 0), (2, 0, 0, 0), None):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                build_candidates(context(), CandidatePolicy("full_nonzero"),
                                 supplied_candidates=[tail])

    def test_public_context_whitelist_and_canonical_hash(self):
        first = public()
        second = dict(reversed(tuple(first.items())))
        a = context_from_public(first, (3, 1))
        b = context_from_public(second, [3, 1])
        self.assertEqual(a, b)
        self.assertEqual(a.tail_positions, (3, 1))
        with self.assertRaises(ValueError):
            context_from_public(first, {3, 1})
        with self.assertRaises(ValueError):
            context_from_public(dict(first, q=2), (3, 1))
        changed = public()
        changed["h"] = [2, 0, 0, 0]
        self.assertNotEqual(a.public_sha256, context_from_public(changed, (3, 1)).public_sha256)
        for extra in ("reference_f", "f", "truth", "reference_label"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                context_from_public(dict(first, **{extra: [1, 0, 0, 0]}), (3, 1))
        bad = public()
        bad["distribution"] = dict(bad["distribution"], secret_seed=7)
        with self.assertRaises(ValueError):
            context_from_public(bad, (3, 1))

    def test_deterministic_across_python_hash_seeds(self):
        code = ("from experiments.ntru_hybrid_comparison.candidates import *; "
                "c=CandidateContext('test','0'*64,8,(6,1,4,2)); "
                "b=build_candidates(c,CandidatePolicy('full_nonzero')); "
                "import json; print(json.dumps(b.to_manifest(),sort_keys=True))")
        outputs = []
        for seed in ("0", "17"):
            result = subprocess.run([sys.executable, "-c", code], check=True,
                                    capture_output=True, text=True, timeout=5,
                                    env=dict(os.environ, PYTHONHASHSEED=seed,
                                             PYTHONDONTWRITEBYTECODE="1"))
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
