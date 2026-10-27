"""Independent, offline audit of the unpatterned primal baseline.

The online solver never imports this module.  All polynomial operations and
the exact transformation determinant are implemented here independently.
Run from the repository root with ``python -m
experiments.ntru_compare_n128_w12_w24_q97.evaluate_baseline --run-dir DIR
--exact-determinant``.  Audit time is deliberately separate from attack time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mul(a: list[int], b: list[int]) -> list[int]:
    result = [0] * len(a)
    for i, left in enumerate(a):
        for j, right in enumerate(b):
            degree = i + j
            result[degree % len(a)] += left * right * (-1 if degree >= len(a) else 1)
    return result


def _rotate(value: list[int], shift: int, sign: int = 1) -> list[int]:
    result = [0] * len(value)
    for i, coefficient in enumerate(value):
        degree = i + shift
        result[degree % len(value)] = sign * coefficient * (-1 if degree >= len(value) else 1)
    return result


def _invertible(value: list[int], q: int) -> bool:
    def trim(poly: list[int]) -> list[int]:
        while poly and poly[-1] == 0:
            poly.pop()
        return poly

    left = [1] + [0] * (len(value) - 1) + [1]
    right = trim([x % q for x in value])
    while right:
        try:
            inverse = pow(right[-1], -1, q)
        except ValueError:
            return False
        remainder = list(left)
        while len(remainder) >= len(right):
            offset = len(remainder) - len(right)
            scale = remainder[-1] * inverse % q
            for i, coefficient in enumerate(right):
                remainder[offset + i] = (remainder[offset + i] - scale * coefficient) % q
            trim(remainder)
        left, right = right, remainder
    return len(left) == 1 and math.gcd(left[0], q) == 1


def validate_candidate(public: dict, candidate: object) -> dict:
    if not isinstance(candidate, dict):
        return {"valid": False, "reason": "missing_candidate"}
    n, q = public["n"], public["q"]
    for name in ("f", "g"):
        values = candidate.get(name)
        if not isinstance(values, list) or len(values) != n:
            return {"valid": False, "reason": f"{name}_dimension"}
        if any(not _integer(v) or v not in (-1, 0, 1) for v in values):
            return {"valid": False, "reason": f"{name}_not_ternary"}
        if sum(v != 0 for v in values) != public["distribution"][f"w_{name}"]:
            return {"valid": False, "reason": f"{name}_weight"}
    if [v % q for v in _mul(candidate["f"], public["h"])] != [v % q for v in candidate["g"]]:
        return {"valid": False, "reason": "public_relation"}
    if not _invertible(candidate["f"], q):
        return {"valid": False, "reason": "f_not_invertible"}
    return {"valid": True, "reason": None,
            "norm_sq": sum(v * v for v in candidate["g"] + candidate["f"])}


def initial_basis(public: dict) -> list[list[int]]:
    n, q = public["n"], public["q"]
    result = [[q * int(i == j) for j in range(2 * n)] for i in range(n)]
    for i in range(n):
        result.append([v % q for v in _rotate(public["h"], i)]
                      + [int(i == j) for j in range(n)])
    return result


def determinant_exact(rows: list[list[int]]) -> int:
    """Integer Bareiss elimination, including exact divisibility checks."""
    matrix = [list(row) for row in rows]
    size = len(matrix)
    if size == 0:
        return 1
    previous, sign = 1, 1
    for k in range(size - 1):
        pivot_row = next((i for i in range(k, size) if matrix[i][k]), None)
        if pivot_row is None:
            return 0
        if pivot_row != k:
            matrix[k], matrix[pivot_row] = matrix[pivot_row], matrix[k]
            sign = -sign
        pivot = matrix[k][k]
        pivot_values = matrix[k]
        for i in range(k + 1, size):
            row = matrix[i]
            left = row[k]
            for j in range(k + 1, size):
                numerator = row[j] * pivot - left * pivot_values[j]
                quotient, remainder = divmod(numerator, previous)
                if remainder:
                    raise ArithmeticError("Bareiss division was not exact")
                row[j] = quotient
            row[k] = 0
        previous = pivot
    return sign * matrix[-1][-1]


def determinant_mod(rows: list[list[int]], prime: int) -> int:
    matrix = [[v % prime for v in row] for row in rows]
    result = 1
    for k in range(len(matrix)):
        pivot_row = next((i for i in range(k, len(matrix)) if matrix[i][k]), None)
        if pivot_row is None:
            return 0
        if pivot_row != k:
            matrix[k], matrix[pivot_row] = matrix[pivot_row], matrix[k]
            result = -result
        pivot = matrix[k][k]
        result = result * pivot % prime
        inverse = pow(pivot, -1, prime)
        pivot_values = matrix[k]
        for i in range(k + 1, len(matrix)):
            row = matrix[i]
            scale = row[k] * inverse % prime
            if scale:
                for j in range(k + 1, len(matrix)):
                    row[j] = (row[j] - scale * pivot_values[j]) % prime
            row[k] = 0
    return result % prime


def _matrix(path: Path, dimension: int) -> list[list[int]]:
    loaded = json.loads(path.read_text())
    rows = loaded.get("rows") if isinstance(loaded, dict) else loaded
    if not isinstance(rows, list) or len(rows) != dimension:
        raise ValueError(f"wrong row count in {path}")
    if any(not isinstance(row, list) or len(row) != dimension
           or any(not _integer(v) for v in row) for row in rows):
        raise ValueError(f"noninteger or incorrectly sized matrix in {path}")
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve(path: str, run_dir: Path, result_path: Path) -> Path:
    value = Path(path)
    if value.is_absolute():
        return value
    candidates = [run_dir / value, result_path.parent / value, value]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def evaluate_files(run_dir: str | Path, *, public_path: str | Path | None = None,
                   truth_path: str | Path | None = None,
                   config_path: str | Path | None = None,
                   result_path: str | Path | None = None,
                   exact_determinant: bool = False) -> dict:
    started = time.perf_counter()
    run_dir = Path(run_dir).resolve()
    paths = {
        "public": Path(public_path) if public_path else run_dir / "public/instance_01.json",
        "truth": Path(truth_path) if truth_path else run_dir / "private/instance_01.truth.json",
        "config": Path(config_path) if config_path else run_dir / "baseline/config.json",
        "result": Path(result_path) if result_path else run_dir / "baseline/result.json",
    }
    records = {name: json.loads(path.read_text()) for name, path in paths.items()}
    public, truth, config, result = (records[name] for name in ("public", "truth", "config", "result"))
    errors: list[str] = []
    checks: dict[str, bool] = {}

    def check(name: str, passed: bool) -> bool:
        checks[name] = bool(passed)
        if not passed:
            errors.append(name)
        return bool(passed)

    n, q = public["n"], public["q"]
    check("public_schema", public.get("schema") == "ntru-e2e-public-v1"
          and public.get("ring") == "x^n+1" and _integer(n) and n > 0
          and _integer(q) and q > 2 and isinstance(public.get("h"), list)
          and len(public["h"]) == n and all(_integer(v) and 0 <= v < q for v in public["h"])
          and public.get("distribution", {}).get("kind") == "fixed_weight_ternary")
    for name in ("n", "q", "w_f", "w_g"):
        if name in config:
            expected = public[name] if name in ("n", "q") else public["distribution"][name]
            check(f"config_{name}", config[name] == expected)
    for name, expected in (("k", 0), ("kappa", 0), ("bkz_start", 30), ("bkz_jump", 1)):
        if name in config:
            check(f"config_{name}", config[name] == expected)
    check("truth_instance_id", truth.get("instance_id") == public.get("instance_id"))
    check("truth_public_hash", truth.get("public_sha256") == _sha(paths["public"]))
    check("result_public_hash", result.get("public_sha256") == _sha(paths["public"]))
    check("result_config_hash", result.get("config_sha256") == _sha(paths["config"]))
    check("dimension", result.get("dimension") == 2 * n)
    check("result_status", result.get("status") in {"success", "exhausted", "error", "interrupted"})
    truth_validation = validate_candidate(public, truth)
    check("truth_validation", truth_validation["valid"])
    candidate = result.get("found_candidate")
    candidate_validation = validate_candidate(public, candidate)
    equivalent = None
    if candidate_validation["valid"]:
        for shift in range(n):
            for sign in (1, -1):
                if candidate["f"] == _rotate(truth["f"], shift, sign) and candidate["g"] == _rotate(truth["g"], shift, sign):
                    equivalent = {"shift": shift, "sign": sign}
                    break
            if equivalent:
                break
    if result.get("status") == "success":
        check("candidate_valid", candidate_validation["valid"])
        check("candidate_equivalent_to_truth", equivalent is not None)
    else:
        check("unsuccessful_result_has_no_candidate", candidate is None)

    stages = result.get("stages", [])
    check("stages_nonempty", isinstance(stages, list) and bool(stages))
    if not isinstance(stages, list):
        stages = []
    previous_cumulative = -1.0
    previous_beta = None
    previous_tour = 0
    stage_ids: list[object] = []
    for index, stage in enumerate(stages):
        stage_ids.append(stage.get("stage_id"))
        kind, beta, tour = stage.get("kind"), stage.get("beta"), stage.get("tour", 0 if index == 0 else 1)
        valid_bkz_progression = (
            _integer(beta) and _integer(tour) and beta <= 2 * n
            and ((previous_beta is None and beta == 30 and tour == 1)
                 or (previous_beta is not None and beta == previous_beta and tour == previous_tour + 1)
                 or (previous_beta is not None and beta == previous_beta + 1 and tour == 1)))
        check(f"stage_{index}_kind_and_beta", (index == 0 and kind == "lll" and beta is None)
              or (index > 0 and kind == "bkz" and valid_bkz_progression))
        if kind == "bkz":
            previous_beta = beta
            previous_tour = tour
        for field in ("stage_wall_seconds", "scan_wall_seconds", "cumulative_wall_seconds"):
            value = stage.get(field)
            check(f"stage_{index}_{field}", isinstance(value, (int, float))
                  and not isinstance(value, bool) and math.isfinite(value) and value >= 0)
        cumulative = stage.get("cumulative_wall_seconds", -1)
        check(f"stage_{index}_time_order", isinstance(cumulative, (float, int)) and cumulative >= previous_cumulative)
        previous_cumulative = cumulative
        check(f"stage_{index}_minimum_norm", _integer(stage.get("minimum_norm_sq")) and stage["minimum_norm_sq"] > 0)
        check(f"stage_{index}_candidate_count", _integer(stage.get("candidate_count")) and stage["candidate_count"] >= 0)
    check("unique_stage_ids", len(set(stage_ids)) == len(stage_ids))

    basis_report: dict = {}
    artifact_hashes = {name: {"path": str(path.resolve()), "sha256": _sha(path)} for name, path in paths.items()}
    basis_names = ("initial_basis_path", "stage_basis_path")
    matrices = {}
    for name in basis_names:
        raw = result.get(name)
        if not check(f"{name}_present", isinstance(raw, str) and bool(raw)):
            continue
        path = _resolve(raw, run_dir, paths["result"])
        if not check(f"{name}_exists", path.is_file()):
            continue
        matrices[name] = _matrix(path, 2 * n)
        artifact_hashes[name] = {"path": str(path.resolve()), "sha256": _sha(path)}
    expected_initial = initial_basis(public)
    if "initial_basis_path" in matrices:
        check("initial_basis_exact_construction", matrices["initial_basis_path"] == expected_initial)
    if "stage_basis_path" in matrices:
        final_rows = matrices["stage_basis_path"]
        coefficient_rows = []
        invalid_rows = []
        for index, row in enumerate(final_rows):
            f = row[n:]
            exact_product = [sum(f[i] * expected_initial[n + i][j] for i in range(n)) for j in range(n)]
            residual = [left - right for left, right in zip(row[:n], exact_product)]
            if any(value % q for value in residual):
                invalid_rows.append(index)
            coefficient_rows.append([value // q for value in residual] + f)
        check("all_final_rows_lattice_members", not invalid_rows)
        basis_report["invalid_lattice_rows"] = invalid_rows
        # The row coefficients are unique because B0 is nonsingular.  The two
        # block identities below verify C*B0=B over the integers without a dense
        # dimension-cubed matrix multiplication.
        relation_verified = not invalid_rows
        check("exact_coefficient_relation", relation_verified)
        transform_path = result.get("transformation_path")
        if transform_path:
            path = _resolve(transform_path, run_dir, paths["result"])
            if check("transformation_exists", path.is_file()):
                transformation = _matrix(path, 2 * n)
                artifact_hashes["transformation_path"] = {"path": str(path.resolve()), "sha256": _sha(path)}
                check("saved_transformation_matches_unique_coefficients", transformation == coefficient_rows)
        if relation_verified:
            primes = (1000000007, 1000000009, 998244353)
            residues = {str(prime): determinant_mod(coefficient_rows, prime) for prime in primes}
            modular_passed = all(residues[str(p)] == 1 for p in primes) or all(residues[str(p)] == p - 1 for p in primes)
            check("transformation_modular_determinants", modular_passed)
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
        minimum = min(sum(v * v for v in row) for row in final_rows)
        basis_report["minimum_norm_sq"] = minimum
        if stages:
            check("last_stage_minimum_matches_saved_basis", stages[-1].get("minimum_norm_sq") == minimum)
        if result.get("status") == "success" and isinstance(candidate, dict):
            vector = candidate.get("vector")
            check("candidate_vector_reconstruction", vector == candidate.get("g", []) + candidate.get("f", []))
            check("candidate_reported_norm", candidate.get("norm_sq") == candidate_validation.get("norm_sq"))
            row_index, sign = candidate.get("row_index"), candidate.get("sign")
            source_valid = _integer(row_index) and 0 <= row_index < 2 * n and sign in (-1, 1) and _integer(sign)
            check("candidate_saved_basis_row", source_valid and vector == [sign * v for v in final_rows[row_index]])
            check("candidate_final_stage", bool(stages) and candidate.get("stage_id") == stages[-1].get("stage_id")
                  and candidate.get("beta") == stages[-1].get("beta"))
    classification = ("equivalent_secret" if equivalent else "other_valid_short_secret") if candidate_validation["valid"] else "no_valid_recovery"
    return {
        "schema": "ntru-primal-baseline-offline-audit-v1",
        "audit_passed": not errors,
        "strict_lattice_equivalence_certified": bool(checks.get("transformation_exact_unimodular")
                                                     and checks.get("exact_coefficient_relation")
                                                     and checks.get("initial_basis_exact_construction")),
        "instance_id": public.get("instance_id"), "online_status": result.get("status"),
        "parameters": {"n": n, "q": q, "dimension": 2 * n,
                       "w_f": public["distribution"]["w_f"], "w_g": public["distribution"]["w_g"]},
        "classification": classification, "equivalence": equivalent,
        "candidate_validation": candidate_validation, "truth_validation": truth_validation,
        "checks": checks, "failed_checks": errors, "basis": basis_report,
        "artifact_hashes": artifact_hashes,
        "audit_wall_seconds": time.perf_counter() - started,
        "timing_note": "Offline audit time is excluded from attack time.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    for name in ("public", "truth", "config", "result", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--exact-determinant", action="store_true")
    args = parser.parse_args(argv)
    report = evaluate_files(args.run_dir, public_path=args.public, truth_path=args.truth,
                            config_path=args.config, result_path=args.result,
                            exact_determinant=args.exact_determinant)
    output = args.output or args.run_dir / "baseline/audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"audit_passed": report["audit_passed"],
                      "classification": report["classification"], "output": str(output)}))
    return 0 if report["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
