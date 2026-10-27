"""Frozen scheduling and interruption accounting, without running lattice code."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e import run_phase2 as runner


class PhaseTwoRunnerTests(unittest.TestCase):
    def test_frozen_plan_rejects_tuning_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            changed = json.loads((runner.HERE / "config_phase2.json").read_text())
            changed["total_online_budget_seconds"] += 1
            config = root / "changed.json"
            config.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "previously frozen"):
                runner.prepare(root / "run", config)
            self.assertFalse((root / "run").exists())

    def test_prepare_freezes_every_config_and_pairs_patterns_before_key_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            plan, summary = runner.prepare(output, runner.HERE / "config_phase2.json")
            self.assertEqual(len(summary["instances"]), 11)
            self.assertEqual(sum(len(record["trials"]) for record in summary["instances"]), 44)
            self.assertEqual(sum(trial["trial_budget_seconds"] for record in summary["instances"]
                                 for trial in record["trials"]), 1440)
            self.assertEqual(summary["generation"], {})
            self.assertTrue(json.loads((output / "schedule_frozen.json").read_text())["frozen_before_key_generation"])
            paired = {}
            for record in summary["instances"]:
                self.assertFalse(Path(record["public"]).exists())
                self.assertFalse(Path(record["truth"]).exists())
                for trial in record["trials"]:
                    config = Path(trial["config"])
                    self.assertEqual(hashlib.sha256(config.read_bytes()).hexdigest(), trial["config_sha256"])
                    online = json.loads(config.read_text())
                    self.assertFalse({"truth", "keygen_seed", "keygen_seeds", "private"}.intersection(online))
                    key = (online["n"], record["solver_seed"], trial["pattern_index"])
                    value = trial["expected_P"], trial["expected_backend_seed"]
                    if key in paired:
                        self.assertEqual(value, paired[key])
                    paired[key] = value
            before = (output / "summary.json").read_bytes()
            with self.assertRaises(FileExistsError):
                runner.prepare(output, runner.HERE / "config_phase2.json")
            self.assertEqual((output / "summary.json").read_bytes(), before)

    def synthetic_run(self, directory):
        """Tiny inventory for testing the supervisor; never an experiment input."""
        output = directory / "run"
        output.mkdir()
        plan = {"cases": [], "total_online_budget_seconds": 5,
                "supervisor_allowance_seconds": 2}
        config = output / "config.json"
        config.write_text(json.dumps(plan))
        record = {"case_id": "synthetic", "instance_id": "synthetic",
                  "public": str(output / "public.json"), "truth": str(output / "truth.json"),
                  "solver_seed": 1, "instance_budget_seconds": 5,
                  "online_wall_seconds": 0, "trials": []}
        for index in range(2):
            record["trials"].append({"trial_id": str(index), "choice_index": 0,
                                     "pattern_index": index, "expected_P": [0, 1],
                                     "expected_backend_seed": 17, "config": str(config),
                                     "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                                     "result": str(output / f"result_{index}.json"),
                                     "command_prefix": str(output / f"trial_{index}"),
                                     "trial_budget_seconds": 30, "status": "pending"})
        summary = {"schema": "synthetic", "status": "running", "config": str(config),
                   "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                   "source_sha256": {}, "budget_scope": "synthetic test only",
                   "total_online_budget_seconds": 5, "online_wall_seconds": 0,
                   "instances": [record], "generation": {}}
        return output, config, plan, summary

    @staticmethod
    def fake_placeholder(record, trial, status, reason):
        result = Path(trial["result"])
        if not result.exists():
            result.write_text(json.dumps({"status": status, "runner_placeholder": True}))

    def test_budget_reserves_cleanup_and_keeps_stable_inventory_for_evaluation(self):
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            output, config, plan, summary = self.synthetic_run(Path(temporary))

            def fake_capture(command, prefix, *, cwd, timeout):
                module = command[command.index("-m") + 1].rsplit(".", 1)[-1]
                calls.append(module)
                if module == "solve_phase2":
                    self.assertNotIn("--truth", command)
                    self.assertAlmostEqual(timeout, 2.9)
                    persisted = json.loads((output / "summary.json").read_text())
                    self.assertEqual(persisted["instances"][0]["trials"][0]["reserved_online_seconds"], 5)
                    Path(command[command.index("--output") + 1]).write_text(json.dumps({
                        "status": "running", "coordinates": {"P": [0, 1]}, "backend_seed": 17, "arms": {}}))
                    return {"status": "timeout", "exit_code": -15, "wall_seconds": 4}
                self.assertEqual(module, "evaluate_phase2")
                inventory = json.loads((output / "online_inventory.json").read_text())
                self.assertEqual(inventory["status"], "online_finished")
                self.assertEqual(inventory["online_wall_seconds"], 4)
                return {"status": "finished", "exit_code": 0, "wall_seconds": 0.01}

            with patch.object(runner, "prepare", return_value=(plan, summary)), \
                 patch.object(runner, "capture", side_effect=fake_capture), \
                 patch.object(runner, "placeholder", side_effect=self.fake_placeholder):
                result = runner.run(output, config)
            self.assertEqual(calls, ["solve_phase2", "evaluate_phase2"])
            trials = result["instances"][0]["trials"]
            self.assertEqual([trial["status"] for trial in trials], ["timeout", "not_run_budget"])
            self.assertTrue(trials[0]["pairing_verified"])
            self.assertEqual(trials[0]["result_status"], "running")
            self.assertEqual(trials[0]["instrumentation_version"], "v1")
            self.assertEqual(trials[0]["source_sha256"], result["source_sha256"])
            self.assertTrue(result["within_frozen_budgets"])
            self.assertEqual(json.loads((output / "online_inventory.json").read_text())["status"], "online_finished")
            self.assertEqual(json.loads((output / "summary.json").read_text())["status"], "finished")
            inventory_before = (output / "online_inventory.json").read_bytes()
            with patch.object(runner, "validate_plan"):
                runner.run(output, config, resume=True)
            self.assertEqual((output / "online_inventory.json").read_bytes(), inventory_before)
            tampered = json.loads(inventory_before)
            tampered["online_wall_seconds"] += 1
            (output / "online_inventory.json").write_text(json.dumps(tampered))
            with patch.object(runner, "validate_plan"), self.assertRaisesRegex(ValueError, "online inventory"):
                runner.run(output, config, resume=True)

    def test_resume_never_retries_interrupted_trial_and_charges_original_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, config, plan, summary = self.synthetic_run(Path(temporary))
            trial = summary["instances"][0]["trials"][0]
            Path(trial["command_prefix"]).with_suffix(".command.json").write_text(json.dumps({
                "status": "running", "timeout_seconds": 1.4}))
            partial = json.dumps({"status": "running", "coordinates": {"P": [0, 1]},
                                  "backend_seed": 17, "arms": {}}).encode()
            Path(trial["result"]).write_bytes(partial)
            (output / "summary.json").write_text(json.dumps(summary))

            def fake_capture(command, prefix, *, cwd, timeout):
                self.assertTrue(command[command.index("-m") + 1].endswith("evaluate_phase2"))
                return {"status": "finished", "exit_code": 0, "wall_seconds": 0.01}

            with patch.object(runner, "validate_plan"), \
                 patch.object(runner, "capture", side_effect=fake_capture), \
                 patch.object(runner, "placeholder", side_effect=self.fake_placeholder):
                result = runner.run(output, config, resume=True)
            trials = result["instances"][0]["trials"]
            self.assertEqual([trial["status"] for trial in trials], ["interrupted_censored", "not_run_budget"])
            self.assertEqual(result["online_wall_seconds"], 3.5)
            self.assertTrue(result["within_frozen_budgets"])
            self.assertTrue(trials[0]["pairing_verified"])
            self.assertEqual(Path(trial["result"]).read_bytes(), partial)

    def revision_fixture(self, directory):
        output, config, plan, summary = self.synthetic_run(directory)
        source = directory / "source"
        source.mkdir()
        for name in ("run_phase2.py", "solve_phase2.py", "algebra.py"):
            (source / name).write_text("original " + name)
        before = {path.name: runner.file_sha(path) for path in source.iterdir()}
        summary["source_sha256"] = deepcopy(before)
        schedule = output / "schedule_frozen.json"
        schedule.write_text(json.dumps({"source_sha256": before, "patterns": [0, 1]}))
        summary.update(schedule=str(schedule), schedule_sha256=runner.file_sha(schedule))
        for name in ("run_phase2.py", "solve_phase2.py"):
            (source / name).write_text("checkpoint instrumentation v2 " + name)
        after = {path.name: runner.file_sha(path) for path in source.iterdir()}
        trials = summary["instances"][0]["trials"]
        trials[0]["status"] = "finished"
        Path(trials[1]["command_prefix"]).with_suffix(".command.json").write_text('{"status": "running"}')
        fresh = deepcopy(trials[1])
        fresh.update(trial_id="2", command_prefix=str(output / "trial_2"), result=str(output / "result_2.json"))
        trials.append(fresh)
        revision = {"before_source_sha256": before, "after_source_sha256": after,
                    "from_instrumentation_version": "v1", "to_instrumentation_version": "v2",
                    "plan_sha256": summary["config_sha256"],
                    "schedule_sha256": summary["schedule_sha256"],
                    "online_config_sha256": {str(config): summary["config_sha256"]},
                    "revision_id": "compact-checkpoints"}
        ledger = output / "instrumentation_revision.json"
        ledger.write_text(json.dumps(revision))
        return output, source, summary, revision, ledger

    def test_instrumentation_migration_preserves_original_and_labels_attempted_trials(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, source, summary, revision, ledger = self.revision_fixture(Path(temporary))
            original = deepcopy(summary["source_sha256"])
            schedule_bytes = Path(summary["schedule"]).read_bytes()
            with patch.object(runner, "HERE", source):
                runner.validate_resume_sources(output, summary)
                trials = summary["instances"][0]["trials"]
                self.assertEqual([trial.get("instrumentation_version") for trial in trials], ["v1", "v1", None])
                self.assertEqual(trials[0]["source_sha256"], original)
                self.assertEqual(trials[1]["source_sha256"], original)
                runner.stamp_trial_source(trials[2], summary)
                self.assertEqual(trials[2]["instrumentation_version"], "v2")
                self.assertEqual(trials[2]["source_sha256"], revision["after_source_sha256"])
                trials[2]["status"] = "finished"
                runner.validate_resume_sources(output, summary)
                self.assertEqual(trials[2]["instrumentation_version"], "v2")
                self.assertEqual(trials[2]["source_sha256"], revision["after_source_sha256"])
            self.assertEqual(summary["source_sha256"], original)
            self.assertEqual(Path(summary["schedule"]).read_bytes(), schedule_bytes)
            self.assertEqual(summary["active_source_sha256"], revision["after_source_sha256"])
            self.assertEqual(len(summary["source_revisions"]), 1)
            self.assertEqual(summary["source_revisions"][0]["sha256"], runner.file_sha(ledger))
            ledger.write_text(ledger.read_text() + "\n")
            with patch.object(runner, "HERE", source), self.assertRaisesRegex(ValueError, "revision ledger changed"):
                runner.validate_resume_sources(output, summary)

    def test_instrumentation_migration_rejects_parameter_or_unrelated_source_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, source, summary, revision, ledger = self.revision_fixture(Path(temporary))
            for mutation in ("before", "unrelated_source", "plan", "schedule", "online_config", "version"):
                altered = deepcopy(revision)
                if mutation == "before":
                    altered["before_source_sha256"]["run_phase2.py"] = "wrong"
                elif mutation == "unrelated_source":
                    altered["after_source_sha256"]["algebra.py"] = "wrong"
                elif mutation == "plan":
                    altered["plan_sha256"] = "wrong"
                elif mutation == "schedule":
                    altered["schedule_sha256"] = "wrong"
                elif mutation == "online_config":
                    altered["online_config_sha256"] = {}
                else:
                    altered["to_instrumentation_version"] = "v3"
                ledger.write_text(json.dumps(altered))
                with self.subTest(mutation=mutation), patch.object(runner, "HERE", source), self.assertRaises(ValueError):
                    runner.validate_resume_sources(output, deepcopy(summary))
            ledger.unlink()
            with patch.object(runner, "HERE", source), self.assertRaisesRegex(ValueError, "Frozen online source changed"):
                runner.validate_resume_sources(output, summary)

    def test_interruption_without_measurement_charges_full_stored_reservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, config, plan, summary = self.synthetic_run(Path(temporary))
            plan["total_online_budget_seconds"] = 40
            config.write_text(json.dumps(plan))
            summary["instances"][0]["instance_budget_seconds"] = 40
            trial = summary["instances"][0]["trials"][0]
            trial["reserved_online_seconds"] = 30
            trial["instrumentation_version"] = "v1"
            trial["source_sha256"] = {}
            Path(trial["command_prefix"]).with_suffix(".command.json").write_text(json.dumps({
                "status": "running", "timeout_seconds": 27.9}))
            summary["instances"][0]["trials"][1]["status"] = "finished"
            (output / "summary.json").write_text(json.dumps(summary))
            with patch.object(runner, "validate_plan"), \
                 patch.object(runner, "capture", return_value={"status": "finished", "exit_code": 0, "wall_seconds": 0.01}), \
                 patch.object(runner, "placeholder", side_effect=self.fake_placeholder):
                result = runner.run(output, config, resume=True)
            recovered = result["instances"][0]["trials"][0]
            self.assertEqual(recovered["status"], "interrupted_censored")
            self.assertEqual(recovered["charged_online_seconds"], 30)
            self.assertEqual(result["online_wall_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
