"""Budget and evidence wiring; these tests never import or run the decoder."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e.evaluate import evaluate_files
from experiments.ntru_e2e.generate import generate_instances
from experiments.ntru_e2e.run_phase1 import HERE, run_phase1, validate_config


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((HERE / "config_phase1.json").read_text())

    def test_phase_one_limits_and_seeds_cannot_be_expanded(self):
        validate_config(self.config)
        for key, value in (("instance_timeout_seconds", 301),
                           ("total_timeout_seconds", 1801),
                           ("total_timeout_seconds", float("nan")),
                           ("experiment_seeds", [1, 2, 3]), ("n", 64)):
            changed = deepcopy(self.config)
            changed[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_config(changed)

    def test_budget_exhaustion_retains_all_seeds_and_evaluates_after_online(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "new_run"
            config_path = root / "config.json"
            self.config["total_timeout_seconds"] = 12
            config_path.write_text(json.dumps(self.config))

            def fake_capture(command, prefix, *, cwd, timeout):
                module = command[command.index("-m") + 1].rsplit(".", 1)[-1]
                calls.append((module, command, timeout))

                def argument(name):
                    return command[command.index(name) + 1]

                if module == "generate":
                    self.assertEqual(command[-4:], ["--seeds", "1", "2", "3"])
                    generate_instances(argument("--output-dir"))
                elif module == "attack":
                    self.assertNotIn("--truth", command)
                    self.assertEqual(float(argument("--time-limit")), 7)
                    self.assertEqual(timeout, 12)
                    return {"exit_code": -15, "status": "timeout", "wall_seconds": 9}
                elif module == "evaluate":
                    report = evaluate_files(argument("--public"), argument("--truth"),
                                            argument("--result"))
                    Path(argument("--output")).write_text(json.dumps(report))
                else:
                    self.fail(f"unexpected command {command}")
                return {"exit_code": 0, "status": "finished", "wall_seconds": 0.01}

            with patch("experiments.ntru_e2e.run_phase1.capture", side_effect=fake_capture):
                summary = run_phase1(output, config_path)
            self.assertEqual([call[0] for call in calls],
                             ["generate", "attack", "evaluate", "evaluate", "evaluate"])
            self.assertEqual(summary["online_wall_seconds"], 9)
            self.assertTrue(summary["workflow_complete"])
            self.assertFalse(summary["online_acceptance_passed"])
            self.assertEqual([entry["status"] for entry in summary["instances"]],
                             ["timeout", "not_run_budget", "not_run_budget"])
            for record in summary["instances"]:
                result = json.loads(Path(record["result"]).read_text())
                self.assertTrue(result["runner_placeholder"])
                self.assertIsNone(result["recovered"])
                self.assertEqual(result["trials"], [])
                self.assertEqual(record["classification"], "no_recovery")
            self.assertEqual((output / "config_phase1.json").read_bytes(), config_path.read_bytes())
            before = (output / "summary.json").read_bytes()
            with self.assertRaises(FileExistsError):
                run_phase1(output, config_path)
            self.assertEqual((output / "summary.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
