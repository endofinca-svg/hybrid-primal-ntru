"""Exercise zero-coordinate reconstruction and a real tiny LLL recovery."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from experiments.ntru_e2e.generate import generate_instance
from .solve import scan_rows, solve


class PatternSolverTests(unittest.TestCase):
    def test_scan_reconstructs_full_f_in_noncontiguous_coordinates(self):
        public, truth = generate_instance(8, 257, 2, 4, 1, "toy")
        pattern = [i for i, value in enumerate(truth["f"]) if value == 0][:3]
        core = [i for i in range(8) if i not in pattern]
        vector = truth["g"] + [truth["f"][i] for i in core]
        found, minimum, _ = scan_rows([[0] * len(vector), vector], public, core, pattern)
        self.assertEqual(found["f"], truth["f"])
        self.assertEqual(found["g"], truth["g"])
        self.assertEqual(found["vector"], vector)
        self.assertEqual(found["row_index"], 1)
        self.assertEqual(minimum, 0)

    def test_pattern_restriction_does_not_inject_missing_secret_coefficient(self):
        public, truth = generate_instance(8, 257, 2, 4, 1, "toy")
        pattern = [next(i for i, value in enumerate(truth["f"]) if value)]
        core = [i for i in range(8) if i not in pattern]
        vector = truth["g"] + [truth["f"][i] for i in core]
        found, _, _ = scan_rows([vector], public, core, pattern)
        self.assertIsNone(found)

    def test_real_small_lll_keeps_pattern_and_rectangular_secret_coordinates(self):
        public, truth = generate_instance(4, 17, 1, 1, 1, "small")
        pattern = [next(i for i, value in enumerate(truth["f"]) if value == 0)]
        config = {"n": 4, "q": 17, "w_f": 1, "w_g": 1, "k": 1, "kappa": 0,
                  "pattern": pattern, "pattern_index": 0, "threads": 1,
                  "solver_seed": 10001, "precision_bits": 192, "lll_delta": .99,
                  "lll_eta": .51, "bkz_max_tours_per_beta": 2,
                  "bkz_clean_stops_current_beta": False, "bkz_start": 30,
                  "bkz_stop": 7, "bkz_jump": 1}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, config_path, result_path = (root / name for name in (
                "public.json", "config.json", "trial/result.json"))
            public_path.write_text(json.dumps(public))
            config_path.write_text(json.dumps(config))
            with contextlib.redirect_stdout(io.StringIO()):
                code = solve(public_path, config_path, result_path)
            result = json.loads(result_path.read_text())
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["dimension"], 7)
            self.assertEqual(result["coordinates"]["P"], pattern)
            self.assertEqual(result["found_candidate"]["norm_sq"], 2)
            self.assertEqual(result["found_candidate"]["f"][pattern[0]], 0)
            self.assertEqual(len(result["found_candidate"]["vector"]), 7)


if __name__ == "__main__":
    unittest.main()
