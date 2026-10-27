"""Checks that online candidate preparation and public validation stay isolated."""

import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

from experiments.ntru_hybrid_comparison import validation


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def public_fixture():
    return {
        "schema": "ntru-e2e-public-v1",
        "instance_id": "phase2a-public-only-interface-check",
        "n": 4,
        "q": 17,
        "ring": "x^n+1",
        "h": [1, 0, 0, 0],
        "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1},
    }


class UnreadableReference:
    """Any inspection of reference contents indicates an online data leak."""

    def __iter__(self):
        raise AssertionError("online validation inspected a reference array")

    def __getitem__(self, key):
        raise AssertionError("online validation read a reference coefficient")

    def __bool__(self):
        raise AssertionError("online validation consulted reference metadata")

    def __eq__(self, other):
        raise AssertionError("online validation compared against reference data")


class PublicInterfaceSeparationTests(unittest.TestCase):
    def test_public_validator_signature_has_no_reference_or_solver_flags(self):
        parameters = inspect.signature(validation.validate_public_candidate).parameters
        self.assertEqual(list(parameters), ["public", "f", "g", "constraints"])
        self.assertEqual(parameters["constraints"].kind, inspect.Parameter.KEYWORD_ONLY)
        public = public_fixture()
        for keyword in ("reference_f", "reference_g", "solver_success", "valid", "recovered"):
            with self.subTest(keyword=keyword), self.assertRaises(TypeError):
                validation.validate_public_candidate(public, [1, 0, 0, 0], [1, 0, 0, 0], **{keyword: True})

    def test_opaque_reference_metadata_cannot_change_public_validation(self):
        public = public_fixture()
        candidate = [1, 0, 0, 0]
        expected = validation.validate_public_candidate(public, candidate, candidate)
        self.assertTrue(expected["public_valid"], expected)
        enriched = {
            **public,
            "reference_f": UnreadableReference(),
            "reference_g": UnreadableReference(),
            "metadata": {"reference": UnreadableReference(), "label": "wrong-on-purpose"},
            "solver_success": False,
            "recovered": False,
        }
        self.assertEqual(validation.validate_public_candidate(enriched, candidate, candidate), expected)
        # Positive solver labels must equally fail to bless an invalid vector.
        enriched.update(solver_success=True, recovered=True, valid=True)
        invalid = validation.validate_public_candidate(enriched, [0] * 4, [0] * 4)
        self.assertFalse(invalid["public_valid"], invalid)
        self.assertTrue(invalid["rejection_reasons"])

    def test_fresh_online_imports_need_no_reference_or_lattice_backend(self):
        probe = r'''
import importlib
import importlib.abc
import json
import sys

blocked = (
    "experiments.ntru_hybrid_comparison.offline",
    "experiments.ntru_e2e.generate",
    "experiments.ntru_e2e.phase2_adapter",
    "experiments.ntru_e2e.slicer_adapter",
    "fpylll",
    "g6k",
)

class BlockReferenceAndBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("online module imported forbidden dependency: " + fullname)

sys.meta_path.insert(0, BlockReferenceAndBackend())
for name in ("experiments.ntru_hybrid_comparison.candidates", "experiments.ntru_hybrid_comparison.validation"):
    importlib.import_module(name)
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy, build_candidates, context_from_public
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate
public = {
    "schema": "ntru-e2e-public-v1", "instance_id": "fresh-import-probe",
    "n": 4, "q": 17, "ring": "x^n+1", "h": [1, 0, 0, 0],
    "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1},
}
batch = build_candidates(context_from_public(public, (3, 1)), CandidatePolicy("full_nonzero"))
assert len(batch.entries) == 8
assert batch.counts.submitted_count == batch.counts.completed_count == 0
assert validate_public_candidate(public, [1, 0, 0, 0], [1, 0, 0, 0])["public_valid"]
unexpected = [name for name in sys.modules if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked)]
assert not unexpected, unexpected
print(json.dumps({"online_imports_only": True, "forbidden_imports": unexpected}))
'''
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"online_imports_only": True, "forbidden_imports": []},
        )


if __name__ == "__main__":
    unittest.main()
