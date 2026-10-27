"""Offline audit of the public, zero-pattern primal lattice.

This module is deliberately independent of the online pattern solver.  It
reuses only the earlier offline auditor's polynomial and integer determinant
implementations, and the fixed two-tour schedule auditor.  Truth is used to
classify the recovery and report orbit coverage after an attack has finished;
neither coverage nor the private key is supplied to the online solver.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

from ..evaluate_baseline import (
    _integer, _matrix, _rotate, determinant_exact, determinant_mod,
    validate_candidate,
)
from ..evaluate_baseline_tour2 import audit_protocol


def initial_basis(public: dict, pattern: list[int]) -> list[list[int]]:
    """Construct rows of [[q I_n, 0], [A_C^T, I_C]] independently."""
    n, q = public["n"], public["q"]
    pattern_set = set(pattern)
    complement = [i for i in range(n) if i not in pattern_set]
    dimension = n + len(complement)
    rows = [[q * int(i == j) for j in range(dimension)] for i in range(n)]
    for index, coordinate in enumerate(complement):
        rows.append([v % q for v in _rotate(public["h"], coordinate)]
                    + [int(index == j) for j in range(len(complement))])
    return rows


def orbit_coverage(truth: dict, pattern: list[int]) -> dict:
    """Post-hoc availability of simultaneous signed negacyclic rotations."""
    n = len(truth["f"])
    shifts, unique = [], set()
    for shift in range(n):
        rotated_f = _rotate(truth["f"], shift)
        if any(rotated_f[i] for i in pattern):
            continue
        shifts.append(shift)
        rotated_g = _rotate(truth["g"], shift)
        for sign in (1, -1):
            unique.add(tuple(sign * v for v in rotated_g + rotated_f))
    return {"compatible_shifts": shifts, "compatible_shift_count": len(shifts),
            "pattern_compatible_truth_rotation_count": len(shifts),
            "compatible_signed_representatives": 2 * len(shifts),
            "compatible_unique_orbit_vectors": len(unique),
            "total_shift_representatives": n, "total_signed_representatives": 2 * n,
            "secret_orbit_available": bool(shifts),
            "scope": "Offline diagnostic only; not an input to pattern selection or reduction."}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_nonnegative(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def audit_trial(public_path: str | Path, truth_path: str | Path,
                config_path: str | Path, result_path: str | Path,
                exact_determinant: bool = True) -> dict:
    """Audit one saved run without invoking any reduction or online code.

    Matrix artifact paths are resolved relative to ``result_path.parent``.
    A completed last stage with no recovered candidate is a valid audit input,
    including an interrupted or failed run that has completed only LLL.
    """
    started = time.perf_counter()
    paths = {"public": Path(public_path).resolve(), "truth": Path(truth_path).resolve(),
             "config": Path(config_path).resolve(), "result": Path(result_path).resolve()}
    records = {name: json.loads(path.read_text()) for name, path in paths.items()}
    public, truth, config, result = (records[name] for name in ("public", "truth", "config", "result"))
    checks: dict[str, bool] = {}

    def check(name: str, passed: object) -> bool:
        checks[name] = bool(passed)
        return bool(passed)

    n, q = public["n"], public["q"]
    public_valid = check(
        "public_schema", public.get("schema") == "ntru-e2e-public-v1"
        and public.get("ring") == "x^n+1" and _integer(n) and n > 0
        and _integer(q) and q > 2 and isinstance(public.get("h"), list)
        and len(public["h"]) == n and all(_integer(v) and 0 <= v < q for v in public["h"])
        and public.get("distribution", {}).get("kind") == "fixed_weight_ternary"
        and all(_integer(public["distribution"].get(name))
                and 0 <= public["distribution"][name] <= n for name in ("w_f", "w_g")))
    for name in ("n", "q", "w_f", "w_g"):
        expected = public.get(name) if name in ("n", "q") else public.get("distribution", {}).get(name)
        if name in config:
            check(f"config_{name}", _integer(config[name]) and config[name] == expected)
    k = config.get("k")
    check("config_k", _integer(k) and 0 <= k <= n)
    if "kappa" in config:
        check("config_kappa", _integer(config["kappa"]) and config["kappa"] == 0)
    coordinates = result.get("coordinates")
    coordinates = coordinates if isinstance(coordinates, dict) else {}
    pattern, complement = coordinates.get("P"), coordinates.get("C")
    pattern_valid = check(
        "pattern_coordinates", isinstance(pattern, list)
        and all(_integer(v) and 0 <= v < n for v in pattern)
        and len(set(pattern)) == len(pattern) and pattern == sorted(pattern))
    check("pattern_size_matches_k", pattern_valid and len(pattern) == k)
    complement_valid = check(
        "complement_coordinates", pattern_valid and isinstance(complement, list)
        and complement == [i for i in range(n) if i not in set(pattern)])
    check("no_tail_coordinates", coordinates.get("T") == [])
    dimension = n + len(complement) if complement_valid else None
    check("dimension", _integer(result.get("dimension")) and result["dimension"] == dimension)
    check("truth_instance_id", truth.get("instance_id") == public.get("instance_id"))
    check("truth_public_hash", truth.get("public_sha256") == _sha(paths["public"]))
    check("result_public_hash", result.get("public_sha256") == _sha(paths["public"]))
    check("result_config_hash", result.get("config_sha256") == _sha(paths["config"]))
    check("result_status", result.get("status") in {"success", "exhausted", "error", "interrupted", "running"})
    censored = result.get("status") in {"error", "interrupted", "running"}
    truth_validation = validate_candidate(public, truth) if public_valid else {"valid": False, "reason": "invalid_public"}
    check("truth_validation", truth_validation["valid"])
    candidate = result.get("found_candidate")
    candidate_validation = validate_candidate(public, candidate) if public_valid else {"valid": False, "reason": "invalid_public"}
    equivalent = None
    if truth_validation["valid"] and candidate_validation["valid"]:
        for shift in range(n):
            for sign in (1, -1):
                if (candidate["f"] == _rotate(truth["f"], shift, sign)
                        and candidate["g"] == _rotate(truth["g"], shift, sign)):
                    equivalent = {"shift": shift, "sign": sign}
                    break
            if equivalent is not None:
                break
    if result.get("status") == "success":
        check("candidate_valid", candidate_validation["valid"])
        check("candidate_obeys_pattern", pattern_valid and candidate_validation["valid"]
              and all(candidate["f"][i] == 0 for i in pattern))
    else:
        check("unsuccessful_result_has_no_candidate", candidate is None)
    coverage = (orbit_coverage(truth, pattern) if truth_validation["valid"] and pattern_valid else None)

    raw_stages = result.get("stages")
    stages = raw_stages if isinstance(raw_stages, list) else []
    check("stages_list", isinstance(raw_stages, list))
    missing_evidence: list[str] = []
    protocol = audit_protocol(config, result)
    if censored and not stages:
        # An online worker can die during initial LLL.  The initial lattice
        # can still be checked, but no completed reduction is being claimed.
        protocol["checks"].pop("tour2_stages_nonempty", None)
        protocol["failed_checks"] = [name for name, passed in protocol["checks"].items() if not passed]
        protocol["protocol_passed"] = None
        protocol["evidence_status"] = "no_completed_stage_to_audit"
        missing_evidence.append("completed_reduction_stage")
    checks.update(protocol["checks"])
    stage_ids, previous_time = [], -1.0
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            continue
        stage_ids.append(stage.get("stage_id"))
        for name in ("stage_wall_seconds", "scan_wall_seconds", "cumulative_wall_seconds"):
            check(f"stage_{index}_{name}", _finite_nonnegative(stage.get(name)))
        cumulative = stage.get("cumulative_wall_seconds")
        if check(f"stage_{index}_time_order", _finite_nonnegative(cumulative) and cumulative >= previous_time):
            previous_time = cumulative
        check(f"stage_{index}_minimum_norm", _integer(stage.get("minimum_norm_sq")) and stage["minimum_norm_sq"] > 0)
    check("unique_stage_ids", all(isinstance(v, str) and v for v in stage_ids)
          and len(set(stage_ids)) == len(stage_ids))

    artifact_hashes = {name: {"path": str(path), "sha256": _sha(path)} for name, path in paths.items()}
    matrices: dict[str, list[list[int]]] = {}
    for name in ("initial_basis_path", "stage_basis_path", "transformation_path"):
        raw = result.get(name)
        if not raw and name == "stage_basis_path":
            raw = result.get("latest_complete_basis_path")
        if not raw and name == "transformation_path":
            raw = result.get("latest_complete_transform_path")
        if not raw and name != "initial_basis_path" and censored and not stages:
            missing_evidence.append(name)
            continue
        if not check(f"{name}_present", isinstance(raw, str) and bool(raw)):
            continue
        artifact_path = Path(raw)
        if not artifact_path.is_absolute():
            artifact_path = paths["result"].parent / artifact_path
        if not check(f"{name}_exists", artifact_path.is_file()):
            continue
        artifact_hashes[name] = {"path": str(artifact_path.resolve()), "sha256": _sha(artifact_path)}
        if dimension is None:
            continue
        try:
            matrices[name] = _matrix(artifact_path, dimension)
        except (ValueError, TypeError, json.JSONDecodeError):
            check(f"{name}_matrix", False)
        else:
            check(f"{name}_matrix", True)
        hash_field = name.removesuffix("_path") + "_sha256"
        if hash_field in result:
            check(f"{name}_hash", result[hash_field] == _sha(artifact_path))
    basis_report: dict = {}
    expected_initial = initial_basis(public, pattern) if public_valid and complement_valid else None
    if "initial_basis_path" in matrices and expected_initial is not None:
        check("initial_basis_exact_construction", matrices["initial_basis_path"] == expected_initial)
    if "stage_basis_path" in matrices and expected_initial is not None:
        rows = matrices["stage_basis_path"]
        coefficient_rows, invalid = [], []
        for row_index, row in enumerate(rows):
            f_complement = row[n:]
            exact_product = [sum(f_complement[i] * expected_initial[n + i][j]
                                 for i in range(len(complement))) for j in range(n)]
            residual = [left - right for left, right in zip(row[:n], exact_product)]
            if any(value % q for value in residual):
                invalid.append(row_index)
            coefficient_rows.append([value // q for value in residual] + f_complement)
        relation_verified = check("all_final_rows_lattice_members", not invalid)
        check("exact_coefficient_relation", relation_verified)
        basis_report["invalid_lattice_rows"] = invalid
        if "transformation_path" in matrices:
            # B0 has invertible diagonal blocks.  The coefficients above are
            # its unique integer row coordinates, so this is a complete exact
            # B = U B0 check, not a modular or sampled product check.
            check("saved_transformation_matches_unique_coefficients",
                  relation_verified and matrices["transformation_path"] == coefficient_rows)
            check("exact_saved_transformation_relation",
                  relation_verified and matrices["transformation_path"] == coefficient_rows)
        if relation_verified:
            primes = (1000000007, 1000000009, 998244353)
            residues = {str(p): determinant_mod(coefficient_rows, p) for p in primes}
            modular_unimodular = (all(residues[str(p)] == 1 for p in primes)
                                  or all(residues[str(p)] == p - 1 for p in primes))
            check("transformation_modular_determinants", modular_unimodular)
            basis_report["determinant_mod_primes"] = residues
            if exact_determinant:
                determinant_started = time.perf_counter()
                exact = determinant_exact(coefficient_rows)
                basis_report.update(exact_transformation_determinant=exact,
                                    exact_determinant_wall_seconds=time.perf_counter() - determinant_started,
                                    unimodularity_evidence="exact_integer_determinant")
                check("transformation_exact_unimodular", abs(exact) == 1)
            else:
                basis_report["unimodularity_evidence"] = "modular_only_not_an_integer_proof"
        minimum = min(sum(v * v for v in row) for row in rows)
        basis_report["minimum_norm_sq"] = minimum
        final = stages[-1] if stages and isinstance(stages[-1], dict) else {}
        check("last_stage_minimum_matches_saved_basis", final.get("minimum_norm_sq") == minimum)
        if result.get("status") == "success" and isinstance(candidate, dict):
            vector = candidate.get("vector")
            reconstructed = (candidate.get("g", []) + [candidate["f"][i] for i in complement]
                             if candidate_validation["valid"] else None)
            check("candidate_vector_reconstruction", isinstance(vector, list) and vector == reconstructed)
            check("candidate_reported_norm", _integer(candidate.get("norm_sq"))
                  and candidate["norm_sq"] == candidate_validation.get("norm_sq"))
            row_index, sign = candidate.get("row_index"), candidate.get("sign")
            source_valid = (_integer(row_index) and 0 <= row_index < dimension
                            and _integer(sign) and sign in (-1, 1))
            check("candidate_saved_basis_row", source_valid and vector == [sign * v for v in rows[row_index]])
        # Independently confirm the final scan outcome using public data only.
        # A success record stores just the first row, rather than every valid
        # rotated representative, so compare existence rather than row counts.
        public_rows = []
        target_norm = public["distribution"]["w_f"] + public["distribution"]["w_g"]
        for row_index, row in enumerate(rows):
            if sum(v * v for v in row) != target_norm or any(v not in (-1, 0, 1) for v in row):
                continue
            f = [0] * n
            for i, coordinate in enumerate(complement):
                f[coordinate] = row[n + i]
            if validate_candidate(public, {"f": f, "g": row[:n]})["valid"]:
                public_rows.append(row_index)
        basis_report["public_valid_final_basis_rows"] = public_rows
        check("last_stage_public_scan_outcome", bool(public_rows) == (result.get("status") == "success"))
    failed = [name for name, passed in checks.items() if not passed]
    classification = (("equivalent_secret" if equivalent is not None else "other_valid_short_secret")
                      if candidate_validation["valid"] else
                      ("censored_no_recovery_observed" if censored else "no_valid_recovery"))
    completed_evidence = bool(stages and "stage_basis_path" in matrices and "transformation_path" in matrices)
    strict = completed_evidence and all(checks.get(name, False) for name in (
        "initial_basis_exact_construction", "exact_saved_transformation_relation",
        "transformation_exact_unimodular"))
    return {"schema": "ntru-pattern-primal-tour2-offline-audit-v1",
            "audit_passed": not failed, "integrity_passed": not failed,
            "checks": checks, "failed_checks": failed, "missing_evidence": missing_evidence,
            "censored": censored, "trial_complete": result.get("status") in {"success", "exhausted"},
            "audit_complete": completed_evidence and strict,
            "completed_stage_evidence": completed_evidence,
            "evidence_status": "completed_stage_artifacts_present" if completed_evidence else "no_completed_reduction_stage",
            "strict_lattice_equivalence_certified": strict,
            "instance_id": public.get("instance_id"), "online_status": result.get("status"),
            "parameters": {"n": n, "q": q, "k": k, "dimension": dimension,
                           "w_f": public["distribution"]["w_f"], "w_g": public["distribution"]["w_g"]},
            "coordinates": coordinates, "classification": classification, "equivalence": equivalent,
            "candidate_validation": candidate_validation, "truth_validation": truth_validation,
            "pattern_coverage": coverage, "tour2_protocol": protocol, "basis": basis_report,
            "pattern_compatible_truth_rotation_count": coverage["compatible_shift_count"] if coverage else None,
            "artifact_hashes": artifact_hashes, "audit_wall_seconds": time.perf_counter() - started,
            "timing_note": "Offline audit time is excluded from attack time."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("public", "truth", "config", "result"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--modular-only", action="store_true",
                        help="Skip the exact determinant; does not certify strict equivalence.")
    args = parser.parse_args(argv)
    report = audit_trial(args.public, args.truth, args.config, args.result,
                         exact_determinant=not args.modular_only)
    output = args.output or args.result.parent / "audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"audit_passed": report["audit_passed"], "classification": report["classification"],
                      "output": str(output)}))
    return 0 if report["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
