"""Offline checks for the small NTRU secret-recovery experiment.

This module is the only recovery-side consumer of private truth files.  Its
polynomial arithmetic is deliberately independent of the online implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from math import gcd
from pathlib import Path


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _multiply(a: list[int], b: list[int], q: int) -> list[int]:
    """Multiply directly in Z_q[x]/(x^n + 1), without online helpers."""
    n = len(a)
    result = [0] * n
    for i, left in enumerate(a):
        for j, right in enumerate(b):
            degree = i + j
            if degree < n:
                result[degree] += left * right
            else:
                result[degree - n] -= left * right
    return [value % q for value in result]


def _rotate(value: list[int], shift: int, sign: int = 1) -> list[int]:
    n = len(value)
    rotated = [0] * n
    for index, coefficient in enumerate(value):
        exponent = index + shift
        rotated[exponent % n] = sign * coefficient * (-1 if exponent >= n else 1)
    return rotated


def _invertible(f: list[int], q: int) -> bool:
    """Independent polynomial Euclidean gcd check over the prime field."""
    def trim(poly: list[int]) -> list[int]:
        while poly and poly[-1] == 0:
            poly.pop()
        return poly

    left = [1] + [0] * (len(f) - 1) + [1]
    right = trim([value % q for value in f])
    while right:
        remainder = list(left)
        try:
            inverse_leading = pow(right[-1], -1, q)
        except ValueError:
            return False
        while len(remainder) >= len(right):
            offset = len(remainder) - len(right)
            scale = remainder[-1] * inverse_leading % q
            for index, value in enumerate(right):
                remainder[index + offset] = (remainder[index + offset] - scale * value) % q
            trim(remainder)
        left, right = right, remainder
    return len(left) == 1 and gcd(left[0], q) == 1


def validate_candidate(public: dict, candidate: object) -> dict:
    """Check the public relation and the declared, exact ternary weights."""
    if not isinstance(candidate, dict):
        return {"valid": False, "reason": "candidate_missing_or_not_an_object"}
    n, q = public["n"], public["q"]
    for name in ("f", "g"):
        value = candidate.get(name)
        if not isinstance(value, list) or len(value) != n:
            return {"valid": False, "reason": f"{name}_dimension"}
        if not all(_integer(item) and item in (-1, 0, 1) for item in value):
            return {"valid": False, "reason": f"{name}_not_ternary"}
        if sum(item != 0 for item in value) != public["distribution"][f"w_{name}"]:
            return {"valid": False, "reason": f"{name}_weight"}
    relation = _multiply(candidate["f"], public["h"], q)
    if relation != [value % q for value in candidate["g"]]:
        return {"valid": False, "reason": "public_relation"}
    if not _invertible(candidate["f"], q):
        return {"valid": False, "reason": "f_not_invertible"}
    f_norm_sq = sum(value * value for value in candidate["f"])
    g_norm_sq = sum(value * value for value in candidate["g"])
    return {
        "valid": True,
        "reason": None,
        "f_norm_sq": f_norm_sq,
        "g_norm_sq": g_norm_sq,
        "combined_norm_sq": f_norm_sq + g_norm_sq,
    }


def _equivalence(truth: dict, candidate: dict) -> dict | None:
    for shift in range(len(truth["f"])):
        for sign in (1, -1):
            if candidate["f"] == _rotate(truth["f"], shift, sign) and candidate[
                "g"
            ] == _rotate(truth["g"], shift, sign):
                return {"shift": shift, "sign": sign}
    return None


def _coverage(public: dict, truth: dict, trials: object) -> dict:
    n = public["n"]
    trial_reports = []
    for trial_id, trial in enumerate(trials if isinstance(trials, list) else []):
        report = {
            "trial_id": trial_id,
            "valid_partition": False,
            "pattern_compatible_rotations": [],
            "compatible_rotations": [],
            "compatible_nonzero_tail_rotations": [],
            "processed_compatible_rotations": [],
            "processed_compatible_nonzero_tail_rotations": [],
        }
        if not isinstance(trial, dict):
            report["error"] = "trial_not_an_object"
            trial_reports.append(report)
            continue
        partition = [trial.get(name) for name in ("P", "C", "T")]
        if not all(isinstance(part, list) for part in partition):
            report["error"] = "missing_partition"
            trial_reports.append(report)
            continue
        positions = [item for part in partition for item in part]
        if not all(_integer(item) for item in positions) or sorted(positions) != list(
            range(n)
        ):
            report["error"] = "partition_is_not_a_disjoint_cover"
            trial_reports.append(report)
            continue
        report["valid_partition"] = True
        pattern, _, tail_positions = partition
        guesses = trial.get("guesses", [])
        if not isinstance(guesses, list):
            guesses = []
        processed = trial.get("processed_guess_ids", [])
        if not isinstance(processed, list):
            processed = []
        processed = {index for index in processed if _integer(index)}
        invalid_processed = sorted(index for index in processed if not 0 <= index < len(guesses))
        report["invalid_processed_guess_ids"] = invalid_processed
        for shift in range(n):
            for sign in (1, -1):
                f = _rotate(truth["f"], shift, sign)
                if any(f[position] != 0 for position in pattern):
                    continue
                rotation = {"shift": shift, "sign": sign}
                report["pattern_compatible_rotations"].append(rotation)
                tail = [f[position] for position in tail_positions]
                guess_ids = [
                    index
                    for index, guess in enumerate(guesses)
                    if isinstance(guess, list)
                    and len(guess) == len(tail)
                    and all(_integer(value) for value in guess)
                    and guess == tail
                ]
                if not guess_ids:
                    continue
                match = {**rotation, "tail": tail, "guess_ids": guess_ids}
                report["compatible_rotations"].append(match)
                if any(tail):
                    report["compatible_nonzero_tail_rotations"].append(match)
                processed_ids = [index for index in guess_ids if index in processed]
                if processed_ids:
                    processed_match = {**match, "processed_guess_ids": processed_ids}
                    report["processed_compatible_rotations"].append(processed_match)
                    if any(tail):
                        report["processed_compatible_nonzero_tail_rotations"].append(
                            processed_match
                        )
        for name in (
            "pattern_compatible_rotations",
            "compatible_rotations",
            "compatible_nonzero_tail_rotations",
            "processed_compatible_rotations",
            "processed_compatible_nonzero_tail_rotations",
        ):
            report[f"{name}_count"] = len(report[name])
        trial_reports.append(report)
    return {
        "trials": trial_reports,
        "has_pattern_compatible_rotation": any(
            trial["pattern_compatible_rotations"] for trial in trial_reports
        ),
        "has_compatible_rotation": any(
            trial["compatible_rotations"] for trial in trial_reports
        ),
        "has_compatible_nonzero_tail": any(
            trial["compatible_nonzero_tail_rotations"] for trial in trial_reports
        ),
        "has_processed_compatible_rotation": any(
            trial["processed_compatible_rotations"] for trial in trial_reports
        ),
        "has_processed_compatible_nonzero_tail": any(
            trial["processed_compatible_nonzero_tail_rotations"] for trial in trial_reports
        ),
    }


def _trace_provenance(public: dict, recovered: object, result: dict) -> dict:
    report = {"source_is_slicer": False, "trace_verified": False, "reason": None}
    if not isinstance(recovered, dict):
        report["reason"] = "no_recovered_candidate"
        return report
    source = recovered.get("source")
    if not isinstance(source, dict) or source.get("stage") != "slicer":
        report["reason"] = "source_is_not_slicer"
        return report
    report["source_is_slicer"] = True
    trial_id = source.get("trial_id")
    trials = result.get("trials", [])
    if not isinstance(trials, list) or not _integer(trial_id) or not 0 <= trial_id < len(trials):
        report["reason"] = "source_trial_missing"
        return report
    trial = trials[trial_id]
    if not isinstance(trial, dict):
        report["reason"] = "source_trial_invalid"
        return report
    call_id, target_id = source.get("call_id"), source.get("target_id")
    if not _integer(call_id) or call_id < 1 or not _integer(target_id) or target_id < 0:
        report["reason"] = "source_call_or_target_missing"
        return report
    n, q = public["n"], public["q"]
    if trial.get("trial_id", trial_id) != trial_id:
        report["reason"] = "source_trial_id_mismatch"
        return report
    partition = [trial.get(name) for name in ("P", "C", "T")]
    if not all(isinstance(part, list) for part in partition):
        report["reason"] = "source_partition_missing"
        return report
    positions = [position for part in partition for position in part]
    if not all(_integer(position) for position in positions) or sorted(positions) != list(range(n)):
        report["reason"] = "source_partition_invalid"
        return report
    pattern, core, tail = partition
    dimension = n + len(core)
    guesses, targets = trial.get("guesses"), trial.get("targets")
    if (
        not isinstance(guesses, list)
        or not isinstance(targets, list)
        or len(guesses) != len(targets)
        or target_id >= len(guesses)
    ):
        report["reason"] = "source_target_or_guess_missing"
        return report
    guess, target = guesses[target_id], targets[target_id]
    if (
        not isinstance(guess, list)
        or len(guess) != len(tail)
        or not all(_integer(value) and value in (-1, 0, 1) for value in guess)
    ):
        report["reason"] = "source_guess_invalid"
        return report
    if not any(guess):
        report["reason"] = "source_zero_tail"
        return report
    if (
        not isinstance(target, list)
        or len(target) != dimension
        or not all(_integer(value) for value in target)
    ):
        report["reason"] = "source_target_invalid"
        return report
    f, g = recovered.get("f"), recovered.get("g")
    if not all(
        isinstance(value, list) and len(value) == n and all(_integer(item) for item in value)
        for value in (f, g)
    ):
        report["reason"] = "source_secret_dimensions"
        return report
    if any(f[position] for position in pattern):
        report["reason"] = "source_pattern_mismatch"
        return report
    if [f[position] for position in tail] != guess:
        report["reason"] = "source_tail_mismatch"
        return report
    f_tail = [0] * n
    for position, value in zip(tail, guess):
        f_tail[position] = value
    tail_product = _multiply(public["h"], f_tail, q)
    expected_target = [(-value + q // 2) % q - q // 2 for value in tail_product]
    expected_target += [0] * len(core)
    if target != expected_target:
        report["reason"] = "source_target_construction_mismatch"
        return report
    processed = trial.get("processed_guess_ids", [])
    if not isinstance(processed, list) or not any(
        _integer(value) and value == target_id for value in processed
    ):
        report["reason"] = "source_guess_not_processed"
        return report
    events = trial.get("events", [])
    if not isinstance(events, list):
        events = []
    starts = []
    finishes = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("call_id") != call_id:
            continue
        if event.get("trial_id", trial_id) != trial_id:
            continue
        if event.get("function") != "bdgl_like_sieve":
            continue
        if event.get("event") == "slicer_call_started":
            external_ids = event.get("external_ids", [])
            if isinstance(external_ids, list) and any(
                _integer(value) and value == target_id for value in external_ids
            ):
                starts.append(index)
        elif event.get("event") == "slicer_call_finished":
            finishes.append(index)
    if not any(start < finish for start in starts for finish in finishes):
        report["reason"] = "matching_completed_slicer_call_missing"
        return report
    candidates = trial.get("slicer_candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    for candidate_index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        if (
            _integer(candidate.get("call_id"))
            and candidate["call_id"] == call_id
            and _integer(candidate.get("target_id"))
            and candidate["target_id"] == target_id
            and candidate.get("f") == recovered.get("f")
            and candidate.get("g") == recovered.get("g")
            and isinstance(candidate.get("v"), list)
            and all(_integer(value) for value in candidate["v"])
            and candidate.get("method", "slicer") == "slicer"
        ):
            if "v" in recovered and recovered["v"] != candidate["v"]:
                continue
            if "v" in source and source["v"] != candidate["v"]:
                continue
            vector = candidate["v"]
            if len(vector) != dimension:
                report["reason"] = "source_vector_dimension"
                continue
            residual = [value - origin for value, origin in zip(vector, target)]
            if residual != g + [f[position] for position in core]:
                report["reason"] = "source_residual_reconstruction_mismatch"
                continue
            f_core = [0] * n
            for position, value in zip(core, vector[n:]):
                f_core[position] = value
            if _multiply(public["h"], f_core, q) != [value % q for value in vector[:n]]:
                report["reason"] = "source_vector_not_in_lattice"
                continue
            report.update(
                trace_verified=True,
                reason=None,
                trial_id=trial_id,
                candidate_index=candidate_index,
                call_id=call_id,
                target_id=target_id,
                v=candidate["v"],
                target=target,
                guess=guess,
                residual_norm_sq=sum(value * value for value in residual),
                partition_verified=True,
                target_verified=True,
                reconstruction_verified=True,
                lattice_membership_verified=True,
                completed_call_verified=True,
            )
            return report
    if report["reason"] is None:
        report["reason"] = "matching_slicer_candidate_missing"
    return report


def evaluate_records(public: dict, truth: dict, result: dict) -> dict:
    """Evaluate recovery and coverage after the online process has finished."""
    if truth.get("instance_id") != public.get("instance_id"):
        raise ValueError("public and truth instance IDs differ")
    truth_validation = validate_candidate(public, truth)
    if not truth_validation["valid"]:
        raise ValueError(f"truth fails independent validation: {truth_validation['reason']}")
    recovered = result.get("recovered")
    validation = validate_candidate(public, recovered)
    equivalence = None
    if recovered is None:
        classification = "no_recovery"
    elif not validation["valid"]:
        classification = "invalid_candidate"
    else:
        equivalence = _equivalence(truth, recovered)
        classification = "equivalent_secret" if equivalence else "other_valid_short_secret"
    coverage = _coverage(public, truth, result.get("trials", []))
    provenance = _trace_provenance(public, recovered, result)
    candidate_valid = validation["valid"]
    end_to_end_passed = candidate_valid and provenance["trace_verified"]
    status = str(result.get("status", "unknown"))
    if end_to_end_passed:
        failure_category = None
    elif status.lower() in {"timeout", "timed_out", "time_limit", "time_budget_exhausted"}:
        failure_category = "timeout"
    elif recovered is not None:
        failure_category = "validation"
    elif not coverage["has_processed_compatible_nonzero_tail"]:
        failure_category = "coverage"
    else:
        golden_candidate_returned = False
        for trial_coverage in coverage["trials"]:
            golden_ids = {
                guess_id
                for rotation in trial_coverage["processed_compatible_nonzero_tail_rotations"]
                for guess_id in rotation["processed_guess_ids"]
            }
            if not golden_ids:
                continue
            trial = result["trials"][trial_coverage["trial_id"]]
            candidates = trial.get("slicer_candidates", [])
            if not isinstance(candidates, list):
                continue
            if any(
                isinstance(candidate, dict)
                and _integer(candidate.get("target_id"))
                and candidate["target_id"] in golden_ids
                for candidate in candidates
            ):
                golden_candidate_returned = True
                break
        failure_category = "validation" if golden_candidate_returned else "decoding"
    return {
        "schema": "ntru-e2e-evaluation-v1",
        "instance_id": public["instance_id"],
        "online_status": status,
        "classification": classification,
        "success": candidate_valid,
        "candidate_valid": candidate_valid,
        "end_to_end_passed": end_to_end_passed,
        "failure_category": failure_category,
        "candidate_validation": validation,
        "equivalence": equivalence,
        "source_is_slicer": provenance["source_is_slicer"],
        "provenance": provenance,
        "coverage": coverage,
    }


def evaluate_files(
    public_path: str | Path, truth_path: str | Path, result_path: str | Path
) -> dict:
    public_bytes = Path(public_path).read_bytes()
    public = json.loads(public_bytes)
    truth = json.loads(Path(truth_path).read_text())
    result = json.loads(Path(result_path).read_text())
    public_sha256 = hashlib.sha256(public_bytes).hexdigest()
    if truth.get("public_sha256") != public_sha256:
        raise ValueError("truth public_sha256 does not match the public file")
    if not isinstance(result, dict):
        raise ValueError("result must be a JSON object")
    if result.get("instance_id") != public["instance_id"]:
        raise ValueError("result instance_id does not match the public file")
    if result.get("public_sha256") != public_sha256:
        raise ValueError("result public_sha256 does not match the public file")
    report = evaluate_records(public, truth, result)
    report["public_sha256"] = public_sha256
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = evaluate_files(args.public, args.truth, args.result)
    except (ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"classification": report["classification"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
