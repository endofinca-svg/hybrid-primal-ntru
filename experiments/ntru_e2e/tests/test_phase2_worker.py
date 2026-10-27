"""Public-boundary and censoring regressions, without running native kernels."""
from argparse import Namespace
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e.evaluate_phase2 import evaluate_trial
from experiments.ntru_e2e.solve_phase2 import (
    METHODS, WorkerInterrupted, paired_pattern, validate_inputs, worker,
)


class FakeDecoder:
    interrupted = False
    babai_interrupted = False
    calls = []
    batches = []

    def __init__(self, rows, beta, sieve_dim, seed, event_callback, **kwargs):
        self.reduced_rows = deepcopy(rows)
        self.callback = event_callback
        self.metrics = {}
        self.database_built = False
        self.calls.append("lll")
        self.callback({"event": "lll_complete"})

    def reduce_bkz(self):
        self.calls.append("bkz")
        self.callback({"event": "bkz_complete"})

    def build_database(self):
        self.calls.append("database")
        self.database_built = True
        self.callback({"event": "sieve_complete"})

    def _batch(self, targets, bounds, ids):
        self.batches.append(deepcopy((targets, bounds, ids)))
        return [{"target_id": target_id, "v": [0] * len(self.reduced_rows),
                 "coefficients": [0] * len(self.reduced_rows),
                 "residual_sq": sum(value * value for value in target)}
                for target, target_id in zip(targets, ids)]

    def iter_babai(self, targets, bounds, ids):
        if self.database_built:
            raise AssertionError("Baseline was run after database construction")
        self.calls.append("babai")
        for item in self._batch(targets, bounds, ids):
            yield item
            if self.babai_interrupted:
                raise WorkerInterrupted("synthetic interruption after first Babai candidate")

    def decode(self, targets, bounds, ids):
        self.calls.append("slicer")
        self.callback({"event": "slicer_call_started", "function": "bdgl_like_sieve",
                       "call_id": 1, "external_ids": ids})
        self.callback({"event": "slicer_call_finished", "function": "bdgl_like_sieve", "call_id": 1})
        candidates = self._batch(targets, bounds, ids)
        for candidate in candidates:
            candidate.update(call_id=1, method="slicer")
            self.callback({"event": "candidate_validated", "call_id": 1,
                           "target_id": candidate["target_id"], "residual_sq": candidate["residual_sq"],
                           "candidate": candidate})
            if self.interrupted:
                raise WorkerInterrupted("synthetic interruption after first candidate callback")
        return candidates


class PhaseTwoWorkerTests(unittest.TestCase):
    def setUp(self):
        self.public = {"schema": "ntru-e2e-public-v1", "instance_id": "small",
                       "n": 8, "q": 17, "ring": "x^n+1", "h": [1] + [0] * 7,
                       "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}}
        self.config = {"n": 8, "q": 17, "w_f": 1, "w_g": 1, "k": 2, "kappa": 4,
                       "patterns_per_instance": 2, "bkz_max_loops": 2,
                       "slicer_randomizations": 16, "slicer_max_iterations": 400,
                       "choices": [{"name": "fake", "bkz_beta": 2, "sieve_dim": 4}],
                       "plan_sha256": "a" * 64}
        FakeDecoder.calls = []
        FakeDecoder.batches = []
        FakeDecoder.interrupted = False
        FakeDecoder.babai_interrupted = False

    def run_worker(self, directory):
        public_path = directory / "public.json"
        config_path = directory / "online.json"
        output_path = directory / "result.json"
        public_path.write_text(json.dumps(self.public))
        config_path.write_text(json.dumps(self.config))
        args = Namespace(public=public_path, config=config_path, output=output_path,
                         seed=10001, pattern_index=0, choice_index=0)
        with patch("experiments.ntru_e2e.phase2_adapter.Phase2Decoder", FakeDecoder), redirect_stdout(io.StringIO()):
            code = worker(args)
        return code, args, json.loads(output_path.read_text())

    def audit(self, args, result):
        truth = {"instance_id": "small", **result["arms"]["direct_tail"]["candidates"][0]}
        return evaluate_trial(self.public, truth, result,
                              public_sha256=hashlib.sha256(args.public.read_bytes()).hexdigest(),
                              config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest())

    def test_all_arms_share_inputs_run_after_success_and_pass_independent_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            code, args, result = self.run_worker(Path(temporary))
            self.assertEqual(code, 0)
            self.assertEqual(FakeDecoder.calls, ["lll", "babai", "bkz", "babai", "database", "slicer"])
            self.assertEqual(FakeDecoder.batches[0], FakeDecoder.batches[1])
            self.assertEqual(FakeDecoder.batches[1], FakeDecoder.batches[2])
            self.assertEqual(result["status"], "complete")
            report = self.audit(args, result)
            self.assertTrue(report["integrity_verified"])
            expected = {"direct_tail": 8, "lll_rows": 4, "lll_babai": 8,
                        "bkz_rows": 4, "bkz_babai": 8, "slicer": 8}
            for name, count in expected.items():
                with self.subTest(method=name):
                    arm = result["arms"][name]
                    self.assertEqual(arm["status"], "complete")
                    self.assertEqual(len(arm["candidates"]), count)
                    self.assertEqual(report["arms"][name]["verified_candidate_count"], count)
                    self.assertGreaterEqual(arm["first_valid_seconds"], arm["method_setup_seconds"])
                    self.assertAlmostEqual(arm["seconds"], arm["operation_seconds"] + arm["method_setup_seconds"])
                    self.assertGreaterEqual(arm["total_first_valid_seconds"], arm["first_valid_seconds"])
            self.assertEqual({item["sign"] for item in result["arms"]["lll_rows"]["candidates"]}, {-1, 1})
            events = [json.loads(line) for line in args.output.with_suffix(".events.jsonl").read_text().splitlines()]
            self.assertEqual(events, result["events"])
            completions = [event["method"] for event in events if event.get("event") == "arm_complete"]
            self.assertEqual(completions, list(METHODS))
            database_index = next(index for index, event in enumerate(events) if event.get("event") == "sieve_complete")
            self.assertEqual([event["method"] for event in events[:database_index]
                              if event.get("event") == "arm_complete"], list(METHODS[:-1]))
            self.assertEqual(result["plan_sha256"], "a" * 64)

    def test_interruption_after_callback_preserves_candidate_without_decode_return(self):
        FakeDecoder.interrupted = True
        with tempfile.TemporaryDirectory() as temporary:
            code, args, result = self.run_worker(Path(temporary))
            self.assertEqual(code, 143)
            self.assertEqual(result["status"], "interrupted")
            self.assertTrue(all(result["arms"][name]["status"] == "complete" for name in METHODS[:-1]))
            self.assertEqual(result["arms"]["slicer"]["status"], "running")
            self.assertEqual(len(result["arms"]["slicer"]["candidates"]), 1)
            self.assertEqual(self.audit(args, result)["arms"]["slicer"]["verified_candidate_count"], 1)

    def test_babai_interruption_preserves_the_first_yielded_candidate(self):
        FakeDecoder.babai_interrupted = True
        with tempfile.TemporaryDirectory() as temporary:
            code, args, result = self.run_worker(Path(temporary))
            self.assertEqual(code, 143)
            self.assertEqual(result["arms"]["lll_babai"]["status"], "running")
            self.assertEqual(len(result["arms"]["lll_babai"]["candidates"]), 1)
            self.assertEqual(result["arms"]["bkz_rows"]["status"], "not_run")
            report = self.audit(args, result)
            self.assertEqual(report["arms"]["lll_babai"]["verified_candidate_count"], 1)
            self.assertTrue(report["comparison"]["comparison_censored"])

    def test_existing_result_and_event_log_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, args, _ = self.run_worker(Path(temporary))
            before = {path: path.read_bytes() for path in (args.output, args.output.with_suffix(".events.jsonl"))}
            with self.assertRaises(FileExistsError):
                worker(args)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            args.output.unlink()
            with self.assertRaises(FileExistsError):
                worker(args)
            self.assertFalse(args.output.exists())

    def test_private_metadata_rejected_at_both_online_boundaries(self):
        for key in ("truth", "f", "g", "keygen_seed", "keygen_seeds", "private"):
            with self.subTest(key=key):
                changed_public = {**self.public, key: []}
                with self.assertRaises(ValueError):
                    validate_inputs(changed_public, self.config)
                changed_config = {**self.config, key: []}
                with self.assertRaises(ValueError):
                    validate_inputs(self.public, changed_config)

    def test_pairing_matches_frozen_rng_order_across_choices_and_weights(self):
        config = {**self.config, "n": 64, "w_f": 4, "w_g": 8}
        rng = random.Random(10001)
        for index in range(2):
            expected_pattern = sorted(rng.sample(range(64), 2))
            expected_seed = rng.randrange(1, 2 ** 31)
            original = paired_pattern(config, 10001, index)
            self.assertEqual(original[0]["P"], expected_pattern)
            self.assertEqual(original[1], expected_seed)
            changed = {**config, "w_f": 8, "w_g": 16, "choices": list(reversed(config["choices"]))}
            self.assertEqual(original, paired_pattern(changed, 10001, index))
        self.assertNotEqual(paired_pattern(config, 10001, 0), paired_pattern(config, 10001, 1))


if __name__ == "__main__":
    unittest.main()
