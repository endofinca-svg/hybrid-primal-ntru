"""Artifact binding and scope boundaries for the read-only phase-two audit."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
from itertools import product
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e import audit_phase2


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


class PhaseTwoArtifactAuditTests(unittest.TestCase):
    def fixture(self, directory, *, excluded=True):
        here = directory / "module"
        run = here / "runs/phase2"
        run.mkdir(parents=True)
        public, online = run / "public.json", run / "online.json"
        result_path, evaluation_path = run / "result.json", directory / "offline/evaluation.json"
        public.write_text('{"instance_id":"instance_01"}\n')
        online.write_text('{"budget":5}\n')
        (here / "worker.py").write_text("# frozen worker\n")
        source = {"worker.py": digest(here / "worker.py")}
        preserved = here / "runs/phase1/untouched.json"
        write_json(preserved, {"original": True})
        write_json(here / "provenance/phase2/phase1_preservation_before.json",
                   {"files_sha256": {str(preserved.relative_to(here)): digest(preserved)}})
        cases = [{"name": "small", "keygen_seeds": [1], "instance_budget_seconds": 10}]
        if excluded:
            cases.append({"name": "pending", "keygen_seeds": [1], "instance_budget_seconds": 10})
        plan = {"cases": cases, "choices": [{"name": "choice"}],
                "patterns_per_instance": 1, "total_online_budget_seconds": 20}
        write_json(run / "config_phase2.json", plan)
        plan_sha = digest(run / "config_phase2.json")
        schedule = {"plan_sha256": plan_sha, "source_sha256": source, "schedules": [
            {"case_id": c["name"], "instance_id": "instance_01", "solver_seed": 10001,
             "patterns": [{"pattern_index": 0, "P": [0, 1], "backend_seed": 17}]}
            for c in cases]}
        write_json(run / "schedule_frozen.json", schedule)
        guesses = [list(g) for g in product((-1, 0, 1), repeat=4) if any(g)]
        result = {"coordinates": {"P": [0, 1]}, "backend_seed": 17,
                  "choice_index": 0, "pattern_index": 0,
                  "config_sha256": digest(online), "public_sha256": digest(public),
                  "plan_sha256": plan_sha, "code_version": {"source_sha256": source},
                  "guesses": guesses, "targets": [[0] for _ in guesses],
                  "bounds_sq": [1] * len(guesses), "arms": {}, "events": []}
        write_json(result_path, result)
        trial = {"choice_index": 0, "pattern_index": 0, "expected_P": [0, 1],
                 "expected_backend_seed": 17, "status": "finished", "result": result_path.name,
                 "config": online.name, "config_sha256": digest(online), "trial_budget_seconds": 5,
                 "charged_online_seconds": 4, "online_command": {"wall_seconds": 4},
                 "source_sha256": source}
        instance = {"case_id": "small", "instance_id": "instance_01", "solver_seed": 10001,
                    "public": public.name, "truth": "private/NEVER_OPEN_THIS.json",
                    "online_wall_seconds": 4, "trials": [trial]}
        inventory = {"config": "config_phase2.json", "config_sha256": plan_sha,
                     "schedule": "schedule_frozen.json", "schedule_sha256": digest(run / "schedule_frozen.json"),
                     "source_sha256": source, "active_source_sha256": source,
                     "within_frozen_budgets": True, "online_wall_seconds": 4,
                     "instances": [instance]}
        if excluded:
            pending = deepcopy(instance)
            pending.update(case_id="pending", public="pending/MISSING_PUBLIC.json", truth="pending/MISSING_TRUTH.json",
                           online_wall_seconds=0,
                           trials=[{"result": "pending/MISSING_RESULT.json", "config": "pending/MISSING_CONFIG.json"}])
            inventory["instances"].append(pending)
        write_json(run / "online_inventory.json", inventory)
        evaluation = {"inventory_path": str(run / "online_inventory.json"),
                      "inventory_sha256": digest(run / "online_inventory.json"), "config_sha256": plan_sha,
                      "selected_case_ids": ["small"],
                      "metrics": {"invalid_candidate_count": 0, "integrity_verified_trials": 1},
                      "instances": [{"case_id": "small", "instance_id": "instance_01",
                                     "public_sha256": digest(public), "trials": [{
                                         "result": str(result_path), "result_sha256": digest(result_path),
                                         "config_sha256": digest(online)}]}]}
        write_json(evaluation_path, evaluation)
        return here, run, evaluation_path, result_path

    def test_scope_filters_pending_paths_and_never_opens_private_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            here, run, evaluation, _ = self.fixture(Path(temporary))
            before = {p: p.read_bytes() for p in run.iterdir() if p.is_file()}
            with patch.object(audit_phase2, "HERE", here):
                report = audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation)
            self.assertTrue(report["passed"], report["issues"])
            self.assertEqual(report["scheduled_trials"], 1)
            self.assertEqual(report["recorded_trials"], 1)
            self.assertEqual(report["selected_case_ids"], ["small"])
            self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_same_count_evaluation_with_a_different_trial_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            here, run, evaluation_path, _ = self.fixture(Path(temporary))
            evaluation = json.loads(evaluation_path.read_text())
            evaluation["instances"][0]["trials"][0]["result"] = "different-result.json"
            write_json(evaluation_path, evaluation)
            with patch.object(audit_phase2, "HERE", here):
                report = audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation_path)
            self.assertFalse(report["passed"])
            self.assertIn("evaluation selected instance/trial set or artifact hashes mismatch", report["issues"])

    def test_changed_result_after_evaluation_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            here, run, evaluation, result = self.fixture(Path(temporary))
            result.write_text(result.read_text() + "\n")
            with patch.object(audit_phase2, "HERE", here):
                report = audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation)
            self.assertFalse(report["passed"])
            self.assertIn("evaluation selected instance/trial set or artifact hashes mismatch", report["issues"])

    def test_wrong_evaluation_scope_is_rejected_even_when_trials_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            here, run, evaluation_path, _ = self.fixture(Path(temporary))
            evaluation = json.loads(evaluation_path.read_text())
            evaluation["selected_case_ids"] = ["pending"]
            write_json(evaluation_path, evaluation)
            with patch.object(audit_phase2, "HERE", here):
                report = audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation_path)
            self.assertIn("evaluation selected case scope mismatch", report["issues"])
            self.assertFalse(report["passed"])

    def test_budget_plan_and_phase1_mutations_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            here, run, evaluation, _ = self.fixture(Path(temporary))
            inventory_path = run / "online_inventory.json"
            inventory = json.loads(inventory_path.read_text())
            inventory["instances"][0]["trials"][0]["charged_online_seconds"] = 6
            write_json(inventory_path, inventory)
            (run / "config_phase2.json").write_text((run / "config_phase2.json").read_text() + "\n")
            (here / "runs/phase1/untouched.json").write_text("{}")
            with patch.object(audit_phase2, "HERE", here):
                report = audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation)
            self.assertFalse(report["passed"])
            self.assertFalse(report["phase1_preservation"]["passed"])
            self.assertIn("frozen plan hash mismatch", report["issues"])
            self.assertTrue(any("trial budget mismatch" in issue for issue in report["issues"]))

    def test_default_scope_and_cli_remain_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            here, run, evaluation, _ = self.fixture(directory, excluded=False)
            output = directory / "audit.json"
            with patch.object(audit_phase2, "HERE", here), redirect_stdout(io.StringIO()):
                report = audit_phase2.audit(run, evaluation_path=evaluation)
                exit_code = audit_phase2.main(["--run-dir", str(run), "--evaluation", str(evaluation),
                                              "--case", "small", "--output", str(output)])
            self.assertTrue(report["passed"], report["issues"])
            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output.read_text())["selected_case_ids"], ["small"])

    def test_revision_ledger_checks_selected_config_and_preserved_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            here, run, evaluation, _ = self.fixture(Path(temporary))
            inventory_path = run / "online_inventory.json"
            inventory = json.loads(inventory_path.read_text())
            archived = here / "provenance/phase2/instrumentation_v1/worker.py"
            archived.parent.mkdir(parents=True)
            archived.write_bytes((here / "worker.py").read_bytes())
            ledger_path = run / "revision.json"
            ledger = {"before_source_sha256": inventory["source_sha256"],
                      "after_source_sha256": inventory["active_source_sha256"],
                      "plan_sha256": inventory["config_sha256"],
                      "schedule_sha256": inventory["schedule_sha256"],
                      "online_config_sha256": {str(run / "online.json"): digest(run / "online.json"),
                                                str(run / "pending/MISSING_CONFIG.json"): "unused"}}
            write_json(ledger_path, ledger)
            inventory["source_revisions"] = [{"path": str(ledger_path), "sha256": digest(ledger_path)}]
            write_json(inventory_path, inventory)
            evaluation_report = json.loads(evaluation.read_text())
            evaluation_report["inventory_sha256"] = digest(inventory_path)
            write_json(evaluation, evaluation_report)
            with patch.object(audit_phase2, "HERE", here):
                self.assertTrue(audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation)["passed"])
                archived.write_text("# tampered archival source\n")
                report = audit_phase2.audit(run, case_ids=["small"], evaluation_path=evaluation)
            self.assertFalse(report["passed"])
            self.assertIn("preserved v1 source changed: worker.py", report["issues"])


if __name__ == "__main__":
    unittest.main()
