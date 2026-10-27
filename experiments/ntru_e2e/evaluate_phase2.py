"""Offline, independent audit of the frozen phase-two comparison.

Run only after the online inventory is finished::

    python -m experiments.ntru_e2e.evaluate_phase2 --run-dir DIR

``summary.json`` is an inventory with ``instances`` containing ``public``,
``truth`` and ``trials`` (each trial has a ``result`` filename).  Neither the
inventory nor any online evidence is modified by this evaluator.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Callable, Sequence

from .evaluate import _equivalence, _integer, _multiply, validate_candidate

METHODS = ("direct_tail", "lll_rows", "lll_babai", "bkz_rows", "bkz_babai", "slicer")
BASELINES = METHODS[:-1]
ROW_METHODS = {"lll_rows", "bkz_rows"}


def _seconds(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _time_summary(values: list[float]) -> dict:
    return {"count": len(values), "sum": sum(values),
            "minimum": min(values) if values else None,
            "median": median(values) if values else None,
            "maximum": max(values) if values else None}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vector(value: object, dimension: int) -> bool:
    return isinstance(value, list) and len(value) == dimension and all(_integer(x) for x in value)


def _rotation(values: list[int], shift: int, sign: int) -> list[int]:
    """Signed negacyclic rotation, implemented separately from online algebra."""
    n = len(values)
    return [sign * (values[(i - shift) % n] if i >= shift else -values[(i - shift) % n])
            for i in range(n)]


def canonical_class(candidate: dict) -> tuple[int, ...]:
    """Canonicalize a pair under a *simultaneous* sign and ring rotation."""
    return min(tuple(_rotation(candidate["f"], shift, sign) +
                     _rotation(candidate["g"], shift, sign))
               for shift in range(len(candidate["f"])) for sign in (1, -1))


def _secret(candidate: dict) -> tuple[int, ...]:
    return tuple(candidate["f"] + candidate["g"])


class _CandidateCache:
    """Memoize only exact public-pair checks within one instance evaluation.

    Target, lattice, coefficient, radius and kernel provenance checks still run
    for every occurrence.  Malformed vectors bypass the cache, including bools
    which Python otherwise aliases with integers in dictionary keys.
    """

    def __init__(self, public: dict, truth: dict):
        self.public, self.truth = public, truth
        self.validations: dict[tuple, dict] = {}
        self.equivalences: dict[tuple, dict | None] = {}
        self.classes: dict[tuple, tuple[int, ...]] = {}

    def key(self, candidate: object) -> tuple | None:
        if (not isinstance(candidate, dict) or
                not all(_vector(candidate.get(name), self.public["n"]) for name in ("f", "g"))):
            return None
        return (tuple(candidate["f"]), tuple(candidate["g"]))

    def validate(self, candidate: object) -> dict:
        key = self.key(candidate)
        if key is None:
            return validate_candidate(self.public, candidate)
        if key not in self.validations:
            self.validations[key] = validate_candidate(self.public, candidate)
        return dict(self.validations[key])

    def equivalence(self, candidate: dict) -> dict | None:
        key = self.key(candidate)
        if key not in self.equivalences:
            self.equivalences[key] = _equivalence(self.truth, candidate)
        value = self.equivalences[key]
        return dict(value) if value is not None else None

    def canonical(self, candidate: dict) -> tuple[int, ...]:
        key = self.key(candidate)
        if key not in self.classes:
            self.classes[key] = canonical_class(candidate)
        return self.classes[key]


def _context(public: dict, result: dict) -> tuple[dict, list[str]]:
    n, q = public["n"], public["q"]
    coordinates = result.get("coordinates", {})
    if not isinstance(coordinates, dict):
        return {}, ["coordinates_not_an_object"]
    parts = [coordinates.get(name) for name in ("P", "C", "T")]
    if not all(isinstance(part, list) for part in parts):
        return {}, ["partition_missing"]
    flat = [i for part in parts for i in part]
    if not all(_integer(i) for i in flat) or sorted(flat) != list(range(n)):
        return {}, ["partition_not_a_disjoint_cover"]
    pattern, core, tail = parts
    dimension = n + len(core)
    guesses, targets = result.get("guesses"), result.get("targets")
    bounds = result.get("bounds_sq", result.get("public_bounds_sq"))
    context = {"P": pattern, "C": core, "T": tail, "dimension": dimension,
               "guesses": guesses, "targets": targets, "bounds": bounds}
    errors = []
    if not all(isinstance(value, list) for value in (guesses, targets, bounds)):
        return context, ["guesses_targets_or_bounds_missing"]
    if not len(guesses) == len(targets) == len(bounds):
        return context, ["guesses_targets_bounds_lengths_differ"]
    for target_id, (guess, target, bound) in enumerate(zip(guesses, targets, bounds)):
        if not _vector(guess, len(tail)) or any(value not in (-1, 0, 1) for value in guess):
            errors.append(f"guess_{target_id}_invalid")
            continue
        if not any(guess):
            errors.append(f"guess_{target_id}_zero_tail")
        if not _vector(target, dimension):
            errors.append(f"target_{target_id}_invalid")
            continue
        f_tail = [0] * n
        for position, value in zip(tail, guess):
            f_tail[position] = value
        product = _multiply(public["h"], f_tail, q)
        expected_target = [(-value + q // 2) % q - q // 2 for value in product] + [0] * len(core)
        if target != expected_target:
            errors.append(f"target_{target_id}_construction")
        expected_bound = public["distribution"]["w_f"] + public["distribution"]["w_g"] - sum(value != 0 for value in guess)
        if not _integer(bound) or bound != expected_bound or bound < 0:
            errors.append(f"bound_{target_id}_not_the_exact_public_bound")
    return context, errors


def _calls(result: dict) -> dict[int, dict]:
    """Accept an actual kernel call, never a target insertion or Babai event."""
    calls: dict[int, dict] = {}
    events = result.get("events", [])
    if not isinstance(events, list):
        return calls
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("function") != "bdgl_like_sieve":
            continue
        call_id = event.get("call_id")
        if not _integer(call_id) or call_id < 1:
            continue
        name = event.get("event")
        if name == "slicer_call_started":
            ids = event.get("external_ids")
            if isinstance(ids, list) and all(_integer(i) and i >= 0 for i in ids):
                calls.setdefault(call_id, {"start_index": index, "external_ids": ids,
                                           "completed": False})
        elif name in {"slicer_call_finished", "slicer_call_completed"}:
            if call_id in calls and calls[call_id]["start_index"] < index:
                calls[call_id].update(completed=True, finish_index=index)
    return calls


def _kernel_counts(calls: dict[int, dict]) -> dict:
    """Count actual kernel work, including retries, without consulting a secret."""
    completed = [call for call in calls.values() if call["completed"]]
    submitted_ids = {target_id for call in calls.values() for target_id in call["external_ids"]}
    completed_ids = {target_id for call in completed for target_id in call["external_ids"]}
    return {"started_calls": len(calls), "completed_calls": len(completed),
            "censored_calls": len(calls) - len(completed),
            "submitted_target_count": len(submitted_ids),
            "completed_target_count": len(completed_ids),
            "submitted_target_call_count": sum(len(set(call["external_ids"])) for call in calls.values()),
            "completed_target_call_count": sum(len(set(call["external_ids"])) for call in completed)}


def _trace(candidate: dict, result: dict, calls: dict) -> str | None:
    call_id, target_id = candidate.get("call_id"), candidate.get("target_id")
    call = calls.get(call_id) if _integer(call_id) else None
    if call is None or not call["completed"] or target_id not in call["external_ids"]:
        return "matching_completed_slicer_call_missing"
    # The pinned adapter emits this after lifting and exact radius checking.  It
    # has no vector payload, so the vector is independently checked below.
    for index, event in enumerate(result.get("events", [])):
        if (isinstance(event, dict) and index > call["finish_index"] and
                event.get("event") == "candidate_validated" and
                event.get("call_id") == call_id and event.get("target_id") == target_id and
                _integer(event.get("residual_sq")) and
                event["residual_sq"] == candidate.get("residual_sq")):
            return None
    return "matching_post_call_candidate_event_missing"


def _check_candidate(public: dict, truth: dict, result: dict, context: dict,
                     method: str, candidate: object, calls: dict,
                     cache: _CandidateCache, basis_validity: dict[str, bool]) -> dict:
    validation = cache.validate(candidate)
    report = {"valid": False, "reason": validation["reason"],
              "public_validation": validation}
    if not validation["valid"]:
        return report
    n, q = public["n"], public["q"]
    dimension = context["dimension"]
    pattern, core, tail = (context[key] for key in ("P", "C", "T"))

    def fail(reason: str) -> dict:
        report["reason"] = reason
        return report

    target_id = candidate.get("target_id")
    if method in ROW_METHODS:
        if target_id is not None:
            return fail("row_candidate_has_a_target")
        target, guess = [0] * dimension, [0] * len(tail)
        bound = public["distribution"]["w_f"] + public["distribution"]["w_g"]
    else:
        if not _integer(target_id) or not 0 <= target_id < len(context["targets"]):
            return fail("target_id_invalid")
        target, guess, bound = (context[key][target_id] for key in ("targets", "guesses", "bounds"))
    vector, coefficients = candidate.get("v"), candidate.get("coefficients")
    if not _vector(vector, dimension):
        return fail("lattice_vector_dimension_or_type")
    if not _vector(coefficients, dimension):
        return fail("coefficients_dimension_or_type")
    if method == "direct_tail":
        if any(vector) or any(coefficients):
            return fail("direct_tail_vector_or_coefficients_not_zero")
    else:
        basis_name = "slicer" if method == "slicer" else method.split("_")[0]
        bases = result.get("bases", {})
        basis = bases.get(basis_name) if isinstance(bases, dict) else None
        if basis_name not in basis_validity:
            basis_validity[basis_name] = (isinstance(basis, list) and len(basis) == dimension
                                          and all(_vector(row, dimension) for row in basis))
        if not basis_validity[basis_name]:
            return fail("basis_missing_or_invalid")
        # Signed-row baselines have one nonzero coefficient.  Skip exact zero
        # products without weakening the reconstruction check for any vector.
        nonzero = [(i, coefficient) for i, coefficient in enumerate(coefficients) if coefficient]
        actual = [sum(coefficient * basis[i][j] for i, coefficient in nonzero)
                  for j in range(dimension)]
        if actual != vector:
            return fail("coefficients_times_basis_mismatch")
        if method in ROW_METHODS:
            row_index, sign = candidate.get("row_index"), candidate.get("sign")
            if not _integer(row_index) or not 0 <= row_index < dimension or not _integer(sign) or sign not in (-1, 1):
                return fail("row_index_or_sign_invalid")
            expected = [0] * dimension
            expected[row_index] = sign
            if coefficients != expected:
                return fail("row_coefficients_not_signed_unit_vector")
    residual = [value - origin for value, origin in zip(vector, target)]
    if any(candidate["f"][i] != 0 for i in pattern):
        return fail("pattern_mismatch")
    if [candidate["f"][i] for i in tail] != guess:
        return fail("tail_mismatch")
    if residual != candidate["g"] + [candidate["f"][i] for i in core]:
        return fail("residual_reconstruction_mismatch")
    residual_sq = sum(value * value for value in residual)
    if not _integer(candidate.get("residual_sq")) or candidate["residual_sq"] != residual_sq:
        return fail("residual_sq_mismatch")
    if residual_sq != bound:
        return fail("residual_sq_not_the_exact_public_bound")
    f_core = [0] * n
    for position, value in zip(core, vector[n:]):
        f_core[position] = value
    if _multiply(public["h"], f_core, q) != [value % q for value in vector[:n]]:
        return fail("vector_not_in_public_lattice")
    if method == "slicer":
        reason = _trace(candidate, result, calls)
        if reason:
            return fail(reason)
    report.update(valid=True, reason=None, target_id=target_id,
                  residual_sq=residual_sq, equivalence=cache.equivalence(candidate),
                  canonical_class=list(cache.canonical(candidate)))
    return report


def _coverage(public: dict, truth: dict, context: dict, calls: dict) -> dict:
    started = {target_id for call in calls.values() for target_id in call["external_ids"]}
    completed = {target_id for call in calls.values() if call["completed"] for target_id in call["external_ids"]}
    guesses = context.get("guesses")
    if not isinstance(guesses, list) or "P" not in context:
        return {"available": False}
    compatible, nonzero, submitted, finished = [], [], [], []
    for shift in range(public["n"]):
        for sign in (1, -1):
            f = _rotation(truth["f"], shift, sign)
            if any(f[i] for i in context["P"]):
                continue
            tail = [f[i] for i in context["T"]]
            ids = [i for i, guess in enumerate(guesses) if guess == tail]
            entry = {"shift": shift, "sign": sign, "tail": tail, "target_ids": ids}
            compatible.append(entry)
            if not any(tail) or not ids:
                continue
            nonzero.append(entry)
            if started.intersection(ids):
                submitted.append(entry)
            if completed.intersection(ids):
                finished.append(entry)
    return {"available": True, "pattern_compatible_rotations": compatible,
            "compatible_nonzero_tail_rotations": nonzero,
            "submitted_compatible_nonzero_tail_rotations": submitted,
            "completed_compatible_nonzero_tail_rotations": finished,
            "pattern_compatible_rotation_count": len(compatible),
            "compatible_nonzero_tail_rotation_count": len(nonzero),
            "submitted_compatible_nonzero_tail_rotation_count": len(submitted),
            "completed_compatible_nonzero_tail_rotation_count": len(finished),
            "submitted_target_ids": sorted(started), "completed_target_ids": sorted(completed),
            "invalid_submitted_target_ids": sorted(i for i in started if i >= len(guesses))}


def evaluate_trial(public: dict, truth: dict, result: dict, *,
                   public_sha256: str, config_sha256: str | None = None,
                   _candidate_cache: _CandidateCache | None = None) -> dict:
    """Audit one pattern without giving any feedback to online search."""
    if _candidate_cache is not None and (_candidate_cache.public is not public or
                                         _candidate_cache.truth is not truth):
        raise ValueError("Candidate cache belongs to a different public instance or truth")
    context, errors = _context(public, result)
    if result.get("public_sha256") != public_sha256:
        errors.append("public_sha256_mismatch")
    if config_sha256 is not None and result.get("config_sha256") != config_sha256:
        errors.append("config_sha256_mismatch")
    calls = _calls(result)
    cache = _candidate_cache or _CandidateCache(public, truth)
    basis_validity: dict[str, bool] = {}
    arms, valid_candidates, valid_classes = {}, {}, {}
    for method in METHODS:
        source = result.get("arms", {}).get(method, {})
        if not isinstance(source, dict):
            source = {}
        candidates = source.get("candidates", [])
        if not isinstance(candidates, list):
            candidates = []
            errors.append(f"{method}_candidates_not_a_list")
        checks = []
        accepted = []
        for index, candidate in enumerate(candidates):
            check = ({"valid": False, "reason": "trial_integrity_errors"} if errors else
                     _check_candidate(public, truth, result, context, method, candidate, calls,
                                      cache, basis_validity))
            checks.append({"candidate_index": index, **check})
            if check["valid"]:
                accepted.append(candidate)
        valid_candidates[method] = accepted
        classes = {tuple(check["canonical_class"]) for check in checks if check["valid"]}
        valid_classes[method] = classes
        arms[method] = {"status": source.get("status", "missing"),
                        "seconds": source.get("seconds"),
                        "first_valid_seconds": source.get("first_valid_seconds") if accepted else None,
                        "total_first_valid_seconds": source.get("total_first_valid_seconds") if accepted else None,
                        "reported_first_valid_seconds": source.get("first_valid_seconds"),
                        "candidate_count": len(candidates), "verified_candidate_count": len(accepted),
                        "unique_secret_count": len({_secret(candidate) for candidate in accepted}),
                        "equivalence_class_count": len(classes),
                        "equivalent_truth_recovered": any(check["equivalence"] is not None
                                                           for check in checks if check["valid"]),
                        "candidate_checks": checks}
    baseline_raw = {_secret(candidate) for method in BASELINES for candidate in valid_candidates[method]}
    baseline_classes = {key for method in BASELINES for key in valid_classes[method]}
    slicer_raw = {_secret(candidate) for candidate in valid_candidates["slicer"]}
    slicer_classes = valid_classes["slicer"]
    incomplete = [method for method in BASELINES if arms[method]["status"] != "complete"]
    slicer_censored = arms["slicer"]["status"] != "complete"
    baseline_times = [arms[method]["first_valid_seconds"] for method in BASELINES
                      if _seconds(arms[method]["first_valid_seconds"])]
    slicer_time = arms["slicer"]["first_valid_seconds"]
    time_comparable = not incomplete and not slicer_censored and bool(baseline_times) and _seconds(slicer_time)
    novelty = {"baseline_methods": list(BASELINES),
               "comparison_censored": bool(incomplete), "incomplete_baseline_methods": incomplete,
               "slicer_censored": slicer_censored,
               "full_comparison_censored": bool(incomplete) or slicer_censored,
               "baseline_union_unique_secret_count": len(baseline_raw),
               "slicer_unique_secret_count": len(slicer_raw),
               "observed_slicer_only_raw_candidates": [list(secret) for secret in sorted(slicer_raw - baseline_raw)],
               "observed_slicer_only_raw_candidate_count": len(slicer_raw - baseline_raw),
               "observed_slicer_new_classes": [list(key) for key in sorted(slicer_classes - baseline_classes)],
               "observed_slicer_new_class_count": len(slicer_classes - baseline_classes),
               "conclusive_extra_candidate": bool(slicer_raw - baseline_raw) and not incomplete,
               "conclusive_extra_class": bool(slicer_classes - baseline_classes) and not incomplete,
               "time_comparison_available": time_comparable,
               "slicer_first_valid_faster_than_baseline_union": slicer_time < min(baseline_times) if time_comparable else None}
    return {"status": result.get("status", "unknown"), "integrity_errors": errors,
            "integrity_verified": not errors, "arms": arms,
            "coverage": _coverage(public, truth, context, calls), "kernel": _kernel_counts(calls),
            "comparison": novelty}


def _resolve(run_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else run_dir / path


def _rotation_union(trials: list[dict], key: str) -> int:
    return len({(item["shift"], item["sign"]) for trial in trials
                for item in trial["coverage"].get(key, [])})


def _instance_summary(trials: list[dict]) -> dict:
    """Recompute instance outcomes from exactly the selected trials."""
    arms = {}
    for method in METHODS:
        arms[method] = {
            "trials_recovered": sum(trial["arms"][method]["verified_candidate_count"] > 0 for trial in trials),
            "equivalent_truth_recovered": any(trial["arms"][method]["equivalent_truth_recovered"] for trial in trials),
            "verified_candidate_count": sum(trial["arms"][method]["verified_candidate_count"] for trial in trials),
            "completed_trials": sum(trial["arms"][method]["status"] == "complete" for trial in trials)}
    coverage = {key + "_count": _rotation_union(trials, key) for key in (
        "pattern_compatible_rotations", "compatible_nonzero_tail_rotations",
        "submitted_compatible_nonzero_tail_rotations", "completed_compatible_nonzero_tail_rotations")}
    return {"arms": arms, "coverage": coverage, "trials": trials}


def _version_groups(instances: list[dict]) -> list[dict]:
    """Partition trials while recomputing every version's instance-level counts."""
    versions = dict.fromkeys(trial["instrumentation_version"] for instance in instances for trial in instance["trials"])
    groups = []
    for version in versions:
        members = []
        for instance in instances:
            selected = [trial for trial in instance["trials"] if trial["instrumentation_version"] == version]
            if selected:
                members.append({**instance, **_instance_summary(selected)})
        groups.append({"instrumentation_version": version, **_group_summary(members)})
    return groups


def _group_summary(instances: list[dict]) -> dict:
    """Aggregate explicit counts without interpreting censored trials as failures."""
    trials = [trial for instance in instances for trial in instance["trials"]]
    arms = {}
    for method in METHODS:
        reports = [trial["arms"][method] for trial in trials]
        arms[method] = {
            "recovered_instances": sum(instance["arms"][method]["trials_recovered"] > 0 for instance in instances),
            "trials_recovered": sum(report["verified_candidate_count"] > 0 for report in reports),
            "completed_trials": sum(report["status"] == "complete" for report in reports),
            "censored_trials": sum(report["status"] != "complete" for report in reports),
            "trial_status_counts": dict(Counter(report["status"] for report in reports)),
            "verified_candidates": sum(report["verified_candidate_count"] for report in reports),
            "arm_seconds": _time_summary([report["seconds"] for report in reports if _seconds(report["seconds"])]),
            "first_valid_seconds": _time_summary([report["first_valid_seconds"] for report in reports if _seconds(report["first_valid_seconds"])]),
            "total_first_valid_seconds": _time_summary([report["total_first_valid_seconds"] for report in reports if _seconds(report["total_first_valid_seconds"])])}
    coverage_keys = ("pattern_compatible_rotations", "compatible_nonzero_tail_rotations",
                     "submitted_compatible_nonzero_tail_rotations", "completed_compatible_nonzero_tail_rotations")
    coverage = {
        "instance_union_rotation_counts": {
            key: sum(instance["coverage"][key + "_count"] for instance in instances)
            for key in coverage_keys},
        "summed_trial_rotation_counts": {
            key: sum(len(trial["coverage"].get(key, [])) for trial in trials)
            for key in coverage_keys},
        "summed_trial_submitted_target_count": sum(len(trial["coverage"].get("submitted_target_ids", [])) for trial in trials),
        "summed_trial_completed_target_count": sum(len(trial["coverage"].get("completed_target_ids", [])) for trial in trials)}
    comparisons = [trial["comparison"] for trial in trials]
    kernel = {key: sum(trial["kernel"][key] for trial in trials)
              for key in _kernel_counts({})}
    kernel["target_count_scope"] = "target_count fields sum unique target IDs within each trial; target_call_count fields include resubmissions across calls"
    return {"instance_count": len(instances), "trial_count": len(trials),
            "trial_status_counts": dict(Counter(trial["status"] for trial in trials)),
            "integrity_verified_trials": sum(trial["integrity_verified"] for trial in trials),
            "arms": arms, "coverage": coverage, "kernel": kernel,
            "comparison": {
                "censored_trials": sum(comparison["comparison_censored"] for comparison in comparisons),
                "baseline_censored_trials": sum(comparison["comparison_censored"] for comparison in comparisons),
                "slicer_censored_trials": sum(comparison["slicer_censored"] for comparison in comparisons),
                "full_comparison_censored_trials": sum(comparison["full_comparison_censored"] for comparison in comparisons),
                "summed_trial_slicer_only_raw_candidates": sum(comparison["observed_slicer_only_raw_candidate_count"] for comparison in comparisons),
                "summed_trial_slicer_new_classes": sum(comparison["observed_slicer_new_class_count"] for comparison in comparisons),
                "trials_with_conclusive_extra_candidate": sum(comparison["conclusive_extra_candidate"] for comparison in comparisons),
                "trials_with_conclusive_extra_class": sum(comparison["conclusive_extra_class"] for comparison in comparisons),
                "time_comparable_trials": sum(comparison["time_comparison_available"] for comparison in comparisons),
                "slicer_faster_trials": sum(comparison["slicer_first_valid_faster_than_baseline_union"] is True for comparison in comparisons)},
            "online_wall_seconds": _time_summary([trial["online_wall_seconds"] for trial in trials if _seconds(trial.get("online_wall_seconds"))]),
            "charged_online_seconds": _time_summary([trial["charged_online_seconds"] for trial in trials if _seconds(trial.get("charged_online_seconds"))])}


def evaluate_run(run_dir: str | Path, *, case_ids: Sequence[str] | None = None,
                 progress: Callable[[dict[str, Any]], None] | None = None) -> dict:
    """Read an inventory and return the report; callers choose when to save it."""
    run_dir = Path(run_dir).resolve()
    inventory_path = run_dir / "online_inventory.json"
    if not inventory_path.exists():
        inventory_path = run_dir / "summary.json"
    inventory = json.loads(inventory_path.read_text())
    entries = inventory["instances"]
    selected_cases = None
    requested = None
    available_cases = list(dict.fromkeys(entry.get("case_id") for entry in entries))
    if case_ids is not None:
        if isinstance(case_ids, str) or not case_ids or any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
            raise ValueError("case_ids must contain at least one nonempty case name")
        requested = list(dict.fromkeys(case_ids))
        unknown = set(requested) - set(available_cases)
        if unknown:
            raise ValueError("Unknown selected cases: " + ", ".join(sorted(unknown)))
        selected_cases = [case_id for case_id in available_cases if case_id in requested]
        entries = [entry for entry in entries if entry.get("case_id") in selected_cases]
        # Validate completion before reading any public key, private truth or
        # trial evidence, including those from other cases in a broad inventory.
        for entry in entries:
            if not entry.get("trials"):
                raise ValueError(f"Selected case has no completed trials: {entry.get('case_id')}")
            for trial in entry["trials"]:
                status = trial.get("status", "")
                if not isinstance(status, str):
                    raise ValueError("Selected trial status must be a string")
                if status in {"pending", "running", "prepared"} or status.startswith("not_run"):
                    raise ValueError(f"Selected trial has not been attempted: {entry.get('case_id')}/{trial.get('trial_id', '?')}: {status}")
    config_sha = inventory.get("config_sha256")
    config_path = inventory.get("config")
    if isinstance(config_path, str):
        actual = _sha(_resolve(run_dir, config_path))
        if config_sha is not None and config_sha != actual:
            raise ValueError("inventory config SHA does not match its snapshot")
        config_sha = actual
    elif (run_dir / "config_phase2.json").exists():
        actual = _sha(run_dir / "config_phase2.json")
        if config_sha is not None and config_sha != actual:
            raise ValueError("inventory config SHA does not match config_phase2.json")
        config_sha = actual
    instances = []
    total_trials = sum(len(entry.get("trials", [])) for entry in entries)
    completed_trials = 0
    for entry in entries:
        public_path = _resolve(run_dir, entry["public"])
        truth_path = _resolve(run_dir, entry["truth"])
        public, truth = json.loads(public_path.read_text()), json.loads(truth_path.read_text())
        public_sha = _sha(public_path)
        if truth.get("instance_id") != public.get("instance_id") or not validate_candidate(public, truth)["valid"]:
            raise ValueError(f"invalid independent truth for {public_path}")
        if truth.get("public_sha256", public_sha) != public_sha:
            raise ValueError(f"truth public SHA mismatch for {public_path}")
        candidate_cache = _CandidateCache(public, truth)
        trials = []
        for trial_index, trial_entry in enumerate(entry.get("trials", [])):
            progress_info = {"case_id": entry.get("case_id"), "instance_id": public["instance_id"],
                             "trial_id": trial_entry.get("trial_id", str(trial_index)),
                             "trial_number": completed_trials + 1, "trial_count": total_trials}
            if progress is not None:
                progress({"event": "trial_started", **progress_info})
            trial_config_sha = config_sha
            trial_config_path = trial_entry.get("config")
            if isinstance(trial_config_path, str):
                trial_config_sha = _sha(_resolve(run_dir, trial_config_path))
                declared_sha = trial_entry.get("config_sha256")
                if declared_sha is not None and declared_sha != trial_config_sha:
                    raise ValueError(f"trial config SHA does not match its snapshot: {trial_config_path}")
            elif trial_entry.get("config_sha256") is not None:
                raise ValueError("trial config_sha256 requires an actual config snapshot")
            path = _resolve(run_dir, trial_entry["result"])
            result = json.loads(path.read_text())
            trial = evaluate_trial(public, truth, result, public_sha256=public_sha,
                                   config_sha256=trial_config_sha, _candidate_cache=candidate_cache)
            command = trial_entry.get("online_command", trial_entry.get("command", {}))
            if not isinstance(command, dict):
                command = {}
            instrumentation_version = trial_entry.get("instrumentation_version", "v1/unknown")
            if not isinstance(instrumentation_version, str) or not instrumentation_version:
                raise ValueError("trial instrumentation_version must be a nonempty string")
            trial.update(trial_index=trial_index, result=str(path), result_sha256=_sha(path),
                         instrumentation_version=instrumentation_version,
                         config_sha256=trial_config_sha,
                         config_hash_verified=trial_config_sha is not None,
                         online_wall_seconds=command.get("wall_seconds", trial_entry.get("online_wall_seconds")),
                         charged_online_seconds=trial_entry.get("charged_online_seconds"),
                         command_status=command.get("status"),
                         inventory_status=trial_entry.get("status"),
                         timings=result.get("timings", {}), backend_metrics=result.get("backend_metrics", {}),
                         timing_scope=result.get("timing_scope"))
            trials.append(trial)
            completed_trials += 1
            if progress is not None:
                progress({"event": "trial_finished", **progress_info,
                          "integrity_verified": trial["integrity_verified"],
                          "verified_candidates": sum(arm["verified_candidate_count"] for arm in trial["arms"].values())})
        instances.append({"case_id": entry.get("case_id"), "instance_id": public["instance_id"],
                          "n": public["n"], "distribution": public["distribution"],
                          "public_sha256": public_sha, "truth_sha256": _sha(truth_path),
                          **_instance_summary(trials)})
    all_trials = [trial for instance in instances for trial in instance["trials"]]
    failures = Counter(check["reason"] for trial in all_trials for arm in trial["arms"].values()
                       for check in arm["candidate_checks"] if not check["valid"])
    metrics = {"instance_count": len(instances), "trial_count": len(all_trials),
               "integrity_verified_trials": sum(trial["integrity_verified"] for trial in all_trials),
               "invalid_candidate_count": sum(failures.values()), "invalid_candidate_reasons": dict(failures),
               "recovered_instances_by_arm": {method: sum(instance["arms"][method]["trials_recovered"] > 0 for instance in instances) for method in METHODS},
               "verified_candidates_by_arm": {method: sum(instance["arms"][method]["verified_candidate_count"] for instance in instances) for method in METHODS},
               "censored_trial_comparisons": sum(trial["comparison"]["comparison_censored"] for trial in all_trials),
               "slicer_censored_trial_comparisons": sum(trial["comparison"]["slicer_censored"] for trial in all_trials),
               "full_censored_trial_comparisons": sum(trial["comparison"]["full_comparison_censored"] for trial in all_trials),
               "trials_with_observed_slicer_only_raw_candidate": sum(trial["comparison"]["observed_slicer_only_raw_candidate_count"] > 0 for trial in all_trials),
               "trials_with_observed_slicer_new_class": sum(trial["comparison"]["observed_slicer_new_class_count"] > 0 for trial in all_trials),
               "trials_with_conclusive_extra_candidate": sum(trial["comparison"]["conclusive_extra_candidate"] for trial in all_trials),
               "trials_with_conclusive_extra_class": sum(trial["comparison"]["conclusive_extra_class"] for trial in all_trials),
               "time_comparable_trials": sum(trial["comparison"]["time_comparison_available"] for trial in all_trials),
               "slicer_faster_trials": sum(trial["comparison"]["slicer_first_valid_faster_than_baseline_union"] is True for trial in all_trials)}
    cases = []
    for case_id in dict.fromkeys(instance["case_id"] for instance in instances):
        members = [instance for instance in instances if instance["case_id"] == case_id]
        cases.append({"case_id": case_id, "n": members[0]["n"],
                      "distribution": members[0]["distribution"], **_group_summary(members),
                      "instrumentation_versions": _version_groups(members)})
    report = {"schema": "ntru-e2e-phase2-evaluation-v1", "run_dir": str(run_dir),
            "inventory_path": str(inventory_path), "inventory_sha256": _sha(inventory_path), "config_sha256": config_sha,
            "config_hash_verified": config_sha is not None,
            "timing_scope": "arm seconds and first_valid_seconds include method-required preprocessing as recorded by the worker; total_first_valid_seconds is observed cumulative worker time and is not used for speed comparisons; no preprocessing is added implicitly",
            "metrics": metrics, "aggregate": _group_summary(instances), "cases": cases,
            "instrumentation_versions": _version_groups(instances), "instances": instances}
    if selected_cases is not None:
        report.update(selected_case_ids=selected_cases, selection={
            "requested_case_ids": requested,
            "excluded_case_ids": [case_id for case_id in available_cases if case_id not in selected_cases],
            "source_instance_count": len(inventory["instances"]),
            "source_trial_count": sum(len(entry.get("trials", [])) for entry in inventory["instances"]),
            "selected_instance_count": len(instances), "selected_trial_count": len(all_trials)})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--case", dest="case_ids", action="append",
                        help="Evaluate only this case; may be repeated; performs no online work")
    parser.add_argument("--output", type=Path,
                        help="New report path; refuses to overwrite existing evidence")
    args = parser.parse_args(argv)
    output = args.output or args.run_dir / ("evaluation_phase2_selected.json" if args.case_ids else "evaluation_phase2.json")
    if output.exists():
        raise FileExistsError(f"Preserving prior evaluation: {output}")
    report = evaluate_run(args.run_dir, case_ids=args.case_ids,
                          progress=lambda event: print(json.dumps(event, sort_keys=True), flush=True))
    with output.open("x") as handle:
        handle.write(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output.resolve()), **report["metrics"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
