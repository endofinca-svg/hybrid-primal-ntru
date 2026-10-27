"""Public/private boundaries and independent offline acceptance checks."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e.evaluate import evaluate_files, evaluate_records, validate_candidate
from experiments.ntru_e2e.generate import generate_instance, generate_instances


def _fixture():
    public = {
        "schema": "ntru-e2e-public-v1",
        "instance_id": "test",
        "n": 4,
        "q": 17,
        "ring": "x^n+1",
        "h": [1, 0, 0, 0],
        "distribution": {"kind": "fixed_weight_ternary", "w_f": 2, "w_g": 2},
    }
    truth = {"instance_id": "test", "f": [1, 1, 0, 0], "g": [1, 1, 0, 0]}
    trial = {
        "P": [2],
        "C": [0, 3],
        "T": [1],
        "guesses": [[0], [1], [-1]],
        "processed_guess_ids": [0, 1, 2],
        "slicer_candidates": [],
    }
    return public, truth, {"status": "exhausted", "recovered": None, "trials": [trial]}


def _recovered(result, f, g):
    # This fixture has h=1, so t=-f_T and the lattice vector has first
    # block f_C.  Keep an actual nonzero tail and a zero pattern coordinate.
    trial = result["trials"][0]
    n = len(f)
    pattern = [next(index for index, value in enumerate(f) if value == 0)]
    tail = [next(index for index, value in enumerate(f) if value != 0)]
    core = [index for index in range(n) if index not in pattern + tail]
    guess = [f[index] for index in tail]
    target = [0] * (n + len(core))
    target[tail[0]] = -guess[0]
    vector = [value + origin for value, origin in zip(g + [f[index] for index in core], target)]
    trial.update(
        P=pattern,
        C=core,
        T=tail,
        guesses=[[0], guess],
        targets=[[0] * len(target), target],
        processed_guess_ids=[0, 1],
        events=[
            {
                "event": "slicer_call_started",
                "function": "bdgl_like_sieve",
                "call_id": 7,
                "external_ids": [0, 1],
            },
            {"event": "slicer_call_finished", "function": "bdgl_like_sieve", "call_id": 7},
        ],
    )
    source = {"stage": "slicer", "trial_id": 0, "call_id": 7, "target_id": 1}
    result["recovered"] = {"f": f, "g": g, "v": vector, "source": source}
    trial["slicer_candidates"].append(
        {"f": f, "g": g, "v": vector, "call_id": 7, "target_id": 1, "valid": True}
    )
    result["status"] = "recovered"


class GenerationTests(unittest.TestCase):
    def test_generation_preserves_existing_records_before_any_new_write(self):
        with tempfile.TemporaryDirectory() as directory:
            public_dir = Path(directory) / "public"
            public_dir.mkdir()
            existing = public_dir / "instance_02.json"
            existing.write_text("preserve this existing result\n")
            with self.assertRaisesRegex(FileExistsError, "Preserving existing"):
                generate_instances(directory)
            self.assertEqual(existing.read_text(), "preserve this existing result\n")
            self.assertFalse((public_dir / "instance_01.json").exists())
            self.assertFalse((Path(directory) / "private" / "instance_01.truth.json").exists())

    def test_public_private_boundary_and_independent_relation(self):
        with tempfile.TemporaryDirectory() as directory:
            records = generate_instances(directory)
            self.assertEqual(len(records), 3)
            for index, record in enumerate(records, start=1):
                public_path = Path(record["public"])
                truth_path = Path(record["truth"])
                self.assertEqual(public_path.parent.name, "public")
                self.assertEqual(truth_path.parent.name, "private")
                self.assertEqual(public_path.name, f"instance_{index:02d}.json")
                public_bytes = public_path.read_bytes()
                public, truth = json.loads(public_bytes), json.loads(truth_path.read_text())
                self.assertEqual(
                    set(public), {"schema", "instance_id", "n", "q", "ring", "h", "distribution"}
                )
                self.assertEqual(
                    public["distribution"],
                    {"kind": "fixed_weight_ternary", "w_f": 2, "w_g": 4},
                )
                self.assertEqual(truth["keygen_seed"], index)
                self.assertEqual(truth["public_sha256"], hashlib.sha256(public_bytes).hexdigest())
                self.assertTrue(validate_candidate(public, truth)["valid"])
                changed = deepcopy(public)
                changed["h"][0] = (changed["h"][0] + 1) % changed["q"]
                self.assertEqual(validate_candidate(changed, truth)["reason"], "public_relation")
                self.assertEqual(truth_path.stat().st_mode & 0o777, 0o600)

    def test_reproducible_keygen_and_capped_inverse_rejection(self):
        first = generate_instance(8, 257, 2, 4, 12, "same")
        self.assertEqual(first, generate_instance(8, 257, 2, 4, 12, "same"))
        with patch(
            "experiments.ntru_e2e.generate.inverse_mod", side_effect=ValueError("not invertible")
        ) as inverse:
            with self.assertRaisesRegex(ValueError, "128"):
                generate_instance(8, 257, 2, 4, 12, "same")
            self.assertEqual(inverse.call_count, 128)

    def test_evaluation_rejects_wrong_public_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            records = generate_instances(directory, seeds=[19])
            public_path, truth_path = records[0]["public"], records[0]["truth"]
            result_path = Path(directory) / "result.json"
            public_bytes = Path(public_path).read_bytes()
            result_path.write_text(json.dumps({
                "instance_id": json.loads(public_bytes)["instance_id"],
                "public_sha256": hashlib.sha256(public_bytes).hexdigest(),
                "recovered": None,
                "trials": [],
            }))
            self.assertEqual(
                evaluate_files(public_path, truth_path, result_path)["classification"], "no_recovery"
            )
            with Path(public_path).open("a") as output:
                output.write(" ")
            with self.assertRaisesRegex(ValueError, "sha256"):
                evaluate_files(public_path, truth_path, result_path)

    def test_file_evaluation_requires_matching_result_id_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            record = generate_instances(directory, seeds=[20])[0]
            public_path, truth_path = record["public"], record["truth"]
            public_bytes = Path(public_path).read_bytes()
            result = {
                "instance_id": json.loads(public_bytes)["instance_id"],
                "public_sha256": hashlib.sha256(public_bytes).hexdigest(),
                "recovered": None,
                "trials": [],
            }
            result_path = Path(directory) / "result.json"
            for field, wrong_value in (("instance_id", "another_instance"), ("public_sha256", "0" * 64)):
                for missing in (False, True):
                    with self.subTest(field=field, missing=missing):
                        changed = deepcopy(result)
                        if missing:
                            del changed[field]
                        else:
                            changed[field] = wrong_value
                        result_path.write_text(json.dumps(changed))
                        with self.assertRaisesRegex(ValueError, f"result {field}"):
                            evaluate_files(public_path, truth_path, result_path)


class IndependentEvaluationTests(unittest.TestCase):
    def test_signed_wraparound_equivalence_and_trace(self):
        public, truth, result = _fixture()
        # -x^3(1+x) = 1-x^3 modulo x^4+1.
        _recovered(result, [1, 0, 0, -1], [1, 0, 0, -1])
        report = evaluate_records(public, truth, result)
        self.assertEqual(report["classification"], "equivalent_secret")
        self.assertEqual(report["equivalence"], {"shift": 3, "sign": -1})
        self.assertTrue(report["candidate_valid"])
        self.assertTrue(report["end_to_end_passed"])
        self.assertEqual(report["candidate_validation"]["combined_norm_sq"], 4)
        result["trials"][0]["slicer_candidates"].clear()
        report = evaluate_records(public, truth, result)
        self.assertTrue(report["candidate_valid"])
        self.assertFalse(report["end_to_end_passed"])
        self.assertEqual(report["failure_category"], "validation")

    def test_other_valid_short_secret_is_distinguished(self):
        public, truth, result = _fixture()
        _recovered(result, [1, 0, 1, 0], [1, 0, 1, 0])
        report = evaluate_records(public, truth, result)
        self.assertEqual(report["classification"], "other_valid_short_secret")
        self.assertIsNone(report["equivalence"])
        self.assertTrue(report["end_to_end_passed"])

    def test_relation_weight_and_invertibility_are_all_required(self):
        public, truth, result = _fixture()
        _recovered(result, truth["f"], [1, -1, 0, 0])
        report = evaluate_records(public, truth, result)
        self.assertEqual(report["candidate_validation"]["reason"], "public_relation")
        self.assertEqual(report["failure_category"], "validation")
        self.assertEqual(
            validate_candidate(public, {"f": [True, 1, 0, 0], "g": truth["g"]})["reason"],
            "f_not_ternary",
        )
        self.assertEqual(
            validate_candidate(public, {"f": [1, 0, 0, 0], "g": truth["g"]})["reason"],
            "f_weight",
        )
        public = deepcopy(public)
        public.update(n=3, q=7, h=[1, 0, 0])
        # x+1 divides x^3+1, so the relation alone is insufficient.
        self.assertEqual(
            validate_candidate(public, {"f": [1, 1, 0], "g": [1, 1, 0]})["reason"],
            "f_not_invertible",
        )

    def test_coverage_separates_nonzero_tail_and_actual_processing(self):
        public, truth, result = _fixture()
        trial = result["trials"][0]
        trial["processed_guess_ids"] = []
        report = evaluate_records(public, truth, result)
        self.assertTrue(report["coverage"]["has_compatible_nonzero_tail"])
        self.assertFalse(report["coverage"]["has_processed_compatible_rotation"])
        self.assertEqual(report["failure_category"], "coverage")
        trial["processed_guess_ids"] = [1]
        report = evaluate_records(public, truth, result)
        self.assertTrue(report["coverage"]["has_processed_compatible_nonzero_tail"])
        self.assertEqual(report["failure_category"], "decoding")
        trial["guesses"] = [[0]]
        trial["processed_guess_ids"] = [0]
        report = evaluate_records(public, truth, result)
        self.assertTrue(report["coverage"]["has_compatible_rotation"])
        self.assertFalse(report["coverage"]["has_compatible_nonzero_tail"])
        self.assertFalse(report["coverage"]["has_processed_compatible_nonzero_tail"])
        self.assertEqual(report["failure_category"], "coverage")

    def test_valid_candidate_requires_real_vector_guess_target_and_call_trace(self):
        public, truth, original = _fixture()
        _recovered(original, truth["f"], truth["g"])
        self.assertTrue(evaluate_records(public, truth, original)["end_to_end_passed"])
        mutations = {
            "wrong_vector": lambda result: result["trials"][0]["slicer_candidates"][0]["v"].__setitem__(0, 3),
            "short_vector": lambda result: result["trials"][0]["slicer_candidates"][0]["v"].pop(),
            "wrong_guess": lambda result: result["trials"][0]["guesses"][1].__setitem__(0, -1),
            "wrong_target": lambda result: result["trials"][0]["targets"][1].__setitem__(0, 4),
            "missing_events": lambda result: result["trials"][0].__setitem__("events", []),
            "unfinished_call": lambda result: result["trials"][0]["events"].pop(),
            "wrong_function": lambda result: result["trials"][0]["events"][0].__setitem__("function", "babai"),
            "wrong_call_id": lambda result: result["trials"][0]["events"][1].__setitem__("call_id", 8),
            "wrong_external_id": lambda result: result["trials"][0]["events"][0].__setitem__("external_ids", [3]),
            "not_processed": lambda result: result["trials"][0].__setitem__("processed_guess_ids", []),
            "invalid_partition": lambda result: result["trials"][0]["C"].append(0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                result = deepcopy(original)
                mutate(result)
                report = evaluate_records(public, truth, result)
                self.assertTrue(report["candidate_valid"])
                self.assertFalse(report["end_to_end_passed"])
                self.assertEqual(report["failure_category"], "validation")

    def test_failure_classification_uses_processed_golden_target_output(self):
        public, truth, result = _fixture()
        trial = result["trials"][0]
        trial["processed_guess_ids"] = [1]
        # Target zero is not a nonzero golden tail, even if its decoder
        # returned a candidate.  The processed golden target still failed
        # at the decoding stage.
        trial["slicer_candidates"] = [{"target_id": 0, "valid": False}]
        self.assertEqual(evaluate_records(public, truth, result)["failure_category"], "decoding")
        # A candidate for an unprocessed duplicate golden guess must not be
        # attributed to the processed target either.
        trial["guesses"].append([1])
        trial["slicer_candidates"] = [{"target_id": 3, "valid": False}]
        self.assertEqual(evaluate_records(public, truth, result)["failure_category"], "decoding")
        trial["slicer_candidates"] = [{"target_id": 1, "valid": False}]
        self.assertEqual(evaluate_records(public, truth, result)["failure_category"], "validation")

    def test_reconstruction_uses_actual_centered_target(self):
        public, truth, result = _fixture()
        # h=-1 has canonical public representative q-1.  The true centered
        # target for f_T=1 is +1, rather than -(q-1).
        public["h"][0] = 16
        truth["g"] = [-value for value in truth["f"]]
        _recovered(result, truth["f"], truth["g"])
        trial = result["trials"][0]
        tail_coordinate = trial["T"][0]
        trial["targets"][1][tail_coordinate] = 1
        vector = truth["g"] + [truth["f"][index] for index in trial["C"]]
        vector[tail_coordinate] += 1
        trial["slicer_candidates"][0]["v"] = vector
        result["recovered"]["v"] = vector
        report = evaluate_records(public, truth, result)
        self.assertTrue(report["end_to_end_passed"])
        self.assertTrue(report["provenance"]["lattice_membership_verified"])

    def test_timeout_and_malformed_partition_are_visible(self):
        public, truth, result = _fixture()
        result["status"] = "timeout"
        self.assertEqual(evaluate_records(public, truth, result)["failure_category"], "timeout")
        result["status"] = "exhausted"
        result["trials"][0]["P"] = [True]
        report = evaluate_records(public, truth, result)
        self.assertFalse(report["coverage"]["trials"][0]["valid_partition"])
        self.assertEqual(report["failure_category"], "coverage")


if __name__ == "__main__":
    unittest.main()
