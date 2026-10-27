"""Safety boundaries for the restricted offline continuation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e import complete_phase2_n64 as completion


class ScopedCompletionTests(unittest.TestCase):
    def fixture(self, directory):
        plan = json.loads((completion.HERE / "config_phase2.json").read_text())
        records = []
        for index in range(1, 4):
            trials = []
            for choice in range(2):
                for pattern in range(2):
                    result = directory / f"{index}_{choice}_{pattern}.json"
                    result.write_text("{}")
                    trials.append({"choice_index": choice, "pattern_index": pattern,
                                   "status": "timeout", "result": str(result)})
            records.append({"case_id": completion.CASE_ID,
                            "instance_id": f"instance_{index:02d}", "keygen_seed": index,
                            "solver_seed": 10000 + index, "trials": trials})
        # An out-of-scope record is deliberately unreadable and pending.
        return {"instances": records + [{"case_id": "n128_w8_16", "trials": [{"status": "pending"}]}]}, plan

    def test_only_first_weight_selected_and_censored_attempts_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            inventory, plan = self.fixture(Path(temporary))
            before = deepcopy(inventory)
            records = completion.selected_records(inventory, plan)
            self.assertEqual(len(records), 3)
            self.assertEqual(sum(len(record["trials"]) for record in records), 12)
            self.assertEqual(inventory, before)

    def test_pending_missing_and_duplicate_trials_fail_before_offline_evaluation(self):
        with tempfile.TemporaryDirectory() as temporary:
            inventory, plan = self.fixture(Path(temporary))
            for status in ("pending", "not_run_budget", "running"):
                changed = deepcopy(inventory)
                changed["instances"][0]["trials"][0]["status"] = status
                with self.assertRaisesRegex(ValueError, "attempted"):
                    completion.selected_records(changed, plan)
            changed = deepcopy(inventory)
            changed["instances"][0]["trials"][0]["result"] += ".missing"
            with self.assertRaisesRegex(ValueError, "Missing"):
                completion.selected_records(changed, plan)
            changed = deepcopy(inventory)
            changed["instances"][0]["trials"][0] = deepcopy(changed["instances"][0]["trials"][1])
            with self.assertRaisesRegex(ValueError, "schedule"):
                completion.selected_records(changed, plan)

    def test_original_run_cannot_be_output_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / "original"
            original.mkdir()
            for output in (original, original / "continuation"):
                with self.assertRaisesRegex(ValueError, "separate output"):
                    completion.freeze_scope(original, output)

    def test_continuation_invokes_only_selected_offline_commands_and_resume_reuses_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / "original"
            output = directory / "continuation"
            output.mkdir()
            scope = {"run_dir": str(original), "selected_trial_count": 12,
                     "selected_instance_count": 3, "inventory_sha256": "original-hash",
                     "selected_charged_online_seconds": 306.1,
                     "phase1_preservation": {"passed": True}}
            (output / "scope_frozen.json").write_text(json.dumps(scope))
            calls = []

            def fake_capture(command, prefix, **kwargs):
                module = command[command.index("-m") + 1].rsplit(".", 1)[-1]
                calls.append(module)
                self.assertEqual(command[command.index("--case") + 1], "n64_w4_8")
                self.assertNotIn("--truth", command)
                report = ({"metrics": {"invalid_candidate_count": 0,
                                        "integrity_verified_trials": 12, "instance_count": 3},
                           "inventory_sha256": "original-hash", "selected_case_ids": [completion.CASE_ID]}
                          if module == "evaluate_phase2" else {
                              "passed": True,
                              "evaluation_sha256": completion.file_sha(output / "evaluation_phase2.json")})
                Path(command[command.index("--output") + 1]).write_text(json.dumps(report))
                metadata = {"exit_code": 0}
                prefix.with_suffix(".command.json").write_text(json.dumps(metadata))
                return metadata

            with patch.object(completion, "verify_scope"), \
                 patch.object(completion, "capture", side_effect=fake_capture):
                result = completion.run(original, output, resume=True)
                self.assertEqual(calls, ["evaluate_phase2", "audit_phase2"])
                self.assertEqual(result["new_online_trials"], 0)
                self.assertEqual(completion.run(original, output, resume=True), result)
                self.assertEqual(len(calls), 2)
                # A completed report cannot silently be replaced on resume.
                (output / "evaluation_phase2.json").write_text(json.dumps({
                    "metrics": {"invalid_candidate_count": 0, "integrity_verified_trials": 12,
                                "instance_count": 3},
                    "inventory_sha256": "original-hash", "selected_case_ids": [completion.CASE_ID],
                    "changed": True}))
                with self.assertRaisesRegex(ValueError, "Audited evaluation changed"):
                    completion.run(original, output, resume=True)


if __name__ == "__main__":
    unittest.main()
