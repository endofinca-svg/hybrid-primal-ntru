"""Offline counterfactual replay of saved Slicer initial projections.

Only the four completed n64_w4_8 records are read.  No LLL, BKZ, lattice
database, RandomizedSlicer, or stochastic kernel is constructed or invoked.
GSO is recomputed from the recorded, already reduced row basis.  The frozen
adapter's initial projection, signed float32 conversion, and exact lift are
then replayed before any target randomization or Slicer iteration.

This diagnostic establishes candidate availability from initial projections;
it does not establish the provenance of the historical kernel's returned
entries, because historical pre-kernel entries were not recorded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from fpylll import GSO, IntegerMatrix
from fpylll.util import gaussian_heuristic

from experiments.ntru_e2e.algebra import reconstruct, validate_candidate
from experiments.ntru_e2e.slicer_adapter import SlicerDecoder, _signed_float32_key


HERE = Path(__file__).resolve().parent
E2E = HERE.parent.parent
ROOT = E2E.parent.parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vector_sha(vector: list[int]) -> str:
    return hashlib.sha256(json.dumps(vector, separators=(",", ":")).encode()).hexdigest()


def identity(candidate: dict) -> tuple:
    return (candidate["target_id"], tuple(candidate["v"]),
            tuple(candidate["coefficients"]), candidate["residual_sq"])


def replay(path: Path, case: Path) -> dict:
    result = json.loads(path.read_text())
    assert result["status"] == "complete"
    assert result["parameters"]["n"] == 64
    assert result["parameters"]["distribution"] == {
        "kind": "fixed_weight_ternary", "w_f": 4, "w_g": 8}
    assert result["bases"]["slicer"] == result["bases"]["bkz"]
    public_path = case / "public" / (path.parent.name + ".json")
    public = json.loads(public_path.read_text())

    # Deliberately bypass the adapter initializer, which would run reductions
    # and construct G6K.  GSO.Mat only orthogonalizes the saved integer basis.
    decoder = SlicerDecoder.__new__(SlicerDecoder)
    decoder.rows = result["bases"]["slicer"]
    decoder.dim = result["dimension"]
    decoder.sieve_dim = result["configuration"]["sieve_dim"]
    decoder.G = GSO.Mat(IntegerMatrix.from_matrix(decoder.rows, int_type="mpz"),
                        float_type="long double")
    decoder.G.update_gso()
    gh = next(event["gh_sub"] for event in result["events"]
              if event["event"] == "sieve_complete")
    recomputed_gh = float(gaussian_heuristic(decoder.G.r()[-decoder.sieve_dim:]))
    assert math.isclose(gh, recomputed_gh, rel_tol=1e-12)
    decoder._sqrt_r_over_gh = [math.sqrt(float(r) / gh)
                              for r in decoder.G.r()[-decoder.sieve_dim:]]
    decoder._sqrt_gh_over_r = [1.0 / x for x in decoder._sqrt_r_over_gh]

    initial_candidates, targets = [], []
    coords = result["coordinates"]
    for target_id, (target, bound) in enumerate(zip(result["targets"], result["bounds_sq"])):
        projection, sign = _signed_float32_key(decoder._project_reduced(target))
        coefficients, vector, distance = decoder._lift(
            target, [sign * value for value in projection])
        within_bound = distance <= bound
        targets.append({"target_id": target_id, "bound_sq": bound,
                        "initial_normalized_projection_sq": sum(x*x for x in projection),
                        "initial_lift_residual_sq": distance,
                        "initial_lift_within_bound": within_bound})
        if not within_bound:
            continue
        secret = reconstruct(vector, target, coords["P"], coords["C"], coords["T"],
                             result["guesses"][target_id], public["n"])
        validation = validate_candidate(public, secret["f"], secret["g"])
        assert validation["valid"] and distance == bound
        initial_candidates.append({"target_id": target_id, "v": vector,
                                   "coefficients": coefficients, "residual_sq": distance,
                                   "f": secret["f"], "g": secret["g"]})

    historical = result["arms"]["slicer"]["candidates"]
    initial_ids = {identity(candidate) for candidate in initial_candidates}
    historical_ids = {identity(candidate) for candidate in historical}
    bkz_babai_ids = {identity(candidate) for candidate in result["arms"]["bkz_babai"]["candidates"]}
    # Compare exact target ID, vector, integer basis coefficients and squared
    # distance, not just rotation/sign equivalence or raw secret identity.
    assert len(initial_candidates) == len(historical) == 8
    assert initial_ids == historical_ids == bkz_babai_ids
    successful = [{"target_id": item["target_id"], "residual_sq": item["residual_sq"],
                   "v_sha256": vector_sha(item["v"]),
                   "coefficients_sha256": vector_sha(item["coefficients"]),
                   "f": item["f"], "g": item["g"]} for item in initial_candidates]
    return {"instance_id": path.parent.name, "trial_id": path.name.removesuffix(".result.json"),
            "historical_result_path": str(path), "historical_result_sha256": sha(path),
            "public_path": str(public_path), "public_sha256": sha(public_path),
            "saved_basis_rows": decoder.dim, "sieve_dim": decoder.sieve_dim,
            "saved_gh_sub": gh, "recomputed_gh_sub": recomputed_gh,
            "target_count": len(targets), "initial_lift_valid_count": len(initial_candidates),
            "historical_slicer_valid_count": len(historical),
            "exact_initial_historical_match_count": len(initial_ids & historical_ids),
            "exact_initial_bkz_babai_match_count": len(initial_ids & bkz_babai_ids),
            "exact_sets_equal": True, "successful_initial_lifts": successful,
            "targets": targets}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "initial_projection_replay.json")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite diagnostic evidence: {args.output}")
    case = E2E / "runs" / "phase2" / "n64_w4_8"
    paths = sorted((case / "instance_03").glob("*.result.json"))
    assert len(paths) == 4
    source_paths = [Path(__file__).resolve(), E2E / "slicer_adapter.py", E2E / "algebra.py",
                    E2E / ".deps" / "runtime.json",
                    E2E / ".deps" / "g6k_hybrid" / "kernel" / "slicer.cpp"]
    frozen_sources = {str(path): sha(path) for path in source_paths}
    records = [replay(path, case) for path in paths]
    assert frozen_sources == {str(path): sha(path) for path in source_paths}
    payload = {
        "schema": "ntru-e2e-offline-initial-projection-replay-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic_kind": "offline_counterfactual_replay",
        "claim": "All historical Slicer candidates in four completed trials are exactly obtainable by lifting their initial signed-float32 projected targets.",
        "limitation": "Historical pre-kernel entries were not recorded; this diagnostic does not identify which internal entries produced the historical outputs.",
        "operations": ["read_saved_public_and_trial_records", "GSO_of_saved_basis",
                       "existing_adapter_projection_and_signed_float32_conversion",
                       "existing_adapter_Babai_lift", "exact_public_validation_and_comparison"],
        "new_online_trials": 0, "reductions_invoked": 0, "g6k_databases_constructed": 0,
        "stochastic_kernels_invoked": 0, "source_sha256": frozen_sources,
        "trial_count": len(records), "all_exact_sets_equal": True,
        "trials": records}
    with args.output.open("x") as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output.resolve()), "trial_count": len(records),
                      "all_exact_sets_equal": True,
                      "initial_valid_counts": [item["initial_lift_valid_count"] for item in records],
                      "exact_historical_match_counts": [item["exact_initial_historical_match_count"] for item in records]},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
