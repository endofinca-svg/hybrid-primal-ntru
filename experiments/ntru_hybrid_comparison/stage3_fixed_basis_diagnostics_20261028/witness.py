"""Offline witness consistency, using only the previously published B output.

Never imported by a decoder. The command reads the fixed public input and the
historical B result, then checks public patterns 0 and 2 using the unchanged
public builders. It does not create GSO objects, reduce, or decode.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import time
import traceback

from experiments.ntru_e2e.algebra import (
    inverse_mod, make_target, multiplication_matrix, partition, reconstruct,
)
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy, context_from_public
from experiments.ntru_hybrid_comparison.inputs import build_input_bundle, content_sha256
from experiments.ntru_hybrid_comparison.patterns import select_pattern


ROOT = Path(__file__).resolve().parents[3]
PUBLIC = ROOT / "experiments/ntru_compare_n128_w12_w24_q97/public/instance_01.json"
ANCHOR = ROOT / "experiments/ntru_hybrid_comparison/main_feasibility_20261027/run/B-main-0/owner/attempt_000/result.json"
EXPECTED_FILE = "8c03b57a732fb8114d900ff47c2b04f93ca257bef5066db0988e74a10d89baf9"
EXPECTED_PUBLIC = "8e2e7cd27fdbd595d58a4baefb5326a4b04d05d0804962395ab9f74b26146bc7"


def ring_product(left, right, q=None):
    """Independent ordinary convolution followed by x**n=-1 folding."""
    n = len(left)
    if len(right) != n:
        raise ValueError("unequal polynomial dimensions")
    convolution = [0] * (2 * n - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            convolution[i + j] += a * b
    for degree in range(2 * n - 2, n - 1, -1):
        convolution[degree - n] -= convolution[degree]
    folded = convolution[:n]
    return folded if q is None else [x % q for x in folded]


def negacyclic_rotate(vector, power):
    """Slice implementation independent of the online index/divmod helper."""
    if not isinstance(power, int) or isinstance(power, bool):
        raise ValueError("integer rotation required")
    n = len(vector)
    if not n:
        raise ValueError("nonempty vector required")
    turns, offset = divmod(power, n)
    sign = -1 if turns % 2 else 1
    if not offset:
        return [sign * value for value in vector]
    return ([-sign * value for value in vector[n - offset:]]
            + [sign * value for value in vector[:n - offset]])


def independently_validate(public, f, g):
    """Recompute checks; no solver success field is inspected.

    Invertibility uses the existing exact polynomial Euclid helper, followed by
    this module's independent convolution to verify the resulting certificate.
    """
    n, q = public["n"], public["q"]
    checks = {"dimensions": isinstance(f, (list, tuple)) and isinstance(g, (list, tuple))
              and len(f) == n and len(g) == n}
    if not checks["dimensions"]:
        return {"public_valid": False, "checks": checks, "rejection_reasons": ["dimensions"]}
    checks["integers"] = all(isinstance(v, int) and not isinstance(v, bool) for v in (*f, *g))
    if not checks["integers"]:
        return {"public_valid": False, "checks": checks, "rejection_reasons": ["integers"]}
    weights = {"f": sum(v != 0 for v in f), "g": sum(v != 0 for v in g)}
    norm = sum(v * v for v in (*f, *g))
    distribution = public["distribution"]
    checks.update(nonzero=any(f) or any(g), ternary=all(v in (-1, 0, 1) for v in (*f, *g)),
                  weights=weights == {"f": distribution["w_f"], "g": distribution["w_g"]},
                  norm_sq=norm == distribution["w_f"] + distribution["w_g"],
                  public_key_relation=ring_product(public["h"], f, q) == [v % q for v in g])
    inverse = None
    if all(checks.values()):
        try:
            inverse = inverse_mod(f, q)
        except ValueError:
            pass
        checks["invertible"] = inverse is not None and ring_product(f, inverse, q) == [1] + [0] * (n - 1)
    else:
        checks["invertible"] = None
    return {"public_valid": all(value is True for value in checks.values()), "checks": checks,
            "rejection_reasons": [name for name, valid in checks.items() if valid is False],
            "invertibility_status": ("not_evaluated_due_to_prior_rejection" if checks["invertible"] is None
                                     else "invertible" if checks["invertible"] else "not_invertible"),
            "norm_sq": norm, "weights": weights,
            "inverse_certificate_sha256": content_sha256(inverse) if inverse is not None else None}


def full_norm_record(bundle, vector, target, tail):
    """The 212-D residual omits guessed tail and fixed-zero coordinates."""
    n = bundle["dimensions"]["n"]
    coordinates = bundle["coordinates"]
    pair = reconstruct(vector, target, coordinates["P"], coordinates["C"], coordinates["T"], tail, n)
    residual = [a - b for a, b in zip(vector, target)]
    ambient = sum(v * v for v in residual)
    tail_norm = sum(v * v for v in tail)
    pattern_norm = sum(pair["f"][i] ** 2 for i in coordinates["P"])
    complete = sum(v * v for v in pair["f"] + pair["g"])
    if complete != ambient + tail_norm + pattern_norm:
        raise ValueError("full norm decomposition failed")
    return {"ambient_residual_norm_sq": ambient, "tail_norm_sq": tail_norm,
            "fixed_pattern_norm_sq": pattern_norm, "full_pair_norm_sq": complete,
            "formula": "||g||^2+||f_C||^2+||f_T||^2+||f_P||^2; f_P=0",
            "pair": pair, "residual": residual}


def check_representative(bundle, f, g, power):
    public = bundle["public_instance"]
    n, q = public["n"], public["q"]
    coordinates = bundle["coordinates"]
    P, C, T = (coordinates[name] for name in ("P", "C", "T"))
    if any(f[position] for position in P):
        raise ValueError("representative violates zero pattern")
    tail = [f[position] for position in T]
    matches = [row for row in bundle["id_mapping"] if row["u"] == tail]
    if len(matches) > 1:
        raise ValueError("nonunique external tail identity")
    A = multiplication_matrix(public["h"], q)
    actual_target = make_target(A, T, tail, len(C), q)
    raw_target = make_target(A, T, tail, len(C))
    if matches:
        mapping = matches[0]
        target_row = bundle["targets"][mapping["target_index"]]
        if target_row["used"] != actual_target or target_row["raw"] != raw_target:
            raise ValueError("actual target does not match public tail identity")
    else:
        mapping = None
    core = [f[position] for position in C]
    vector = [a + b for a, b in zip(g, actual_target[:n])] + core
    numerators = [vector[row] - sum(A[row][position] * z for position, z in zip(C, core))
                  for row in range(n)]
    if any(value % q for value in numerators):
        raise ValueError("known witness has no integer original-basis coefficients")
    coefficients = [value // q for value in numerators] + core
    rows = bundle["basis"]["original"]
    recombined = [sum(coefficient * row[column] for coefficient, row in zip(coefficients, rows))
                  for column in range(len(vector))]
    if recombined != vector:
        raise ValueError("explicit original-basis combination failed")
    # Membership is independently checked as a polynomial congruence.
    core_polynomial = [f[position] if position in C else 0 for position in range(n)]
    if ring_product(public["h"], core_polynomial, q) != [v % q for v in vector[:n]]:
        raise ValueError("independent lattice membership failed")
    norm_record = full_norm_record(bundle, vector, actual_target, tail)
    if norm_record["pair"] != {"f": f, "g": g}:
        raise ValueError("full reconstruction did not reproduce witness")
    validation = independently_validate(public, **norm_record["pair"])
    if not validation["public_valid"]:
        raise ValueError("witness reconstruction is publicly invalid")
    translated_vector = [a + raw - used for a, raw, used in zip(vector, raw_target, actual_target)]
    raw_pair = reconstruct(translated_vector, raw_target, P, C, T, tail, n)
    if raw_pair != norm_record["pair"]:
        raise ValueError("raw/centered target translation is inconsistent")
    return {"negacyclic_power": power, "zero_constraint_satisfied": True,
            "tail": tail, "zero_tail": not any(tail),
            "full80_covered": bool(matches), "external_id_mapping": mapping,
            "target_used": actual_target, "target_raw": raw_target,
            "lattice_vector": vector, "original_basis_coefficients": coefficients,
            "explicit_original_lattice_combination_verified": True,
            "independent_polynomial_lattice_membership_verified": True,
            "raw_centered_reconstruction_identical": True,
            "norm_record": norm_record, "public_validation": validation,
            "algorithm_recovery_success": False}


def check_pattern(bundle, anchor_f, anchor_g, index):
    public = bundle["public_instance"]
    n = public["n"]
    seen, representatives = set(), []
    for power in range(2 * n):
        f, g = negacyclic_rotate(anchor_f, power), negacyclic_rotate(anchor_g, power)
        key = tuple(f), tuple(g)
        if key in seen:
            continue
        seen.add(key)
        if all(f[position] == 0 for position in bundle["coordinates"]["P"]):
            representatives.append(check_representative(bundle, f, g, power))
    return {"pattern_index": index, "coordinates": bundle["coordinates"],
            "original_basis_sha256": bundle["basis"]["original_sha256"],
            "candidate_ordered_sha256": bundle["candidate_batch"]["ordered_sha256"],
            "id_mapping_sha256": bundle["id_mapping_sha256"],
            "compatible_representatives": representatives,
            "compatible_count": len(representatives),
            "zero_tail_count": sum(row["zero_tail"] for row in representatives),
            "full80_covered_count": sum(row["full80_covered"] for row in representatives),
            "distinct_joint_orbit_size": len(seen)}


def run(stage3_path=None, work_deadline=None):
    started = time.monotonic()
    raw = PUBLIC.read_bytes()
    if sha256(raw).hexdigest() != EXPECTED_FILE:
        raise ValueError("public file digest mismatch")
    public = json.loads(raw)
    if context_from_public(public, [0]).public_sha256 != EXPECTED_PUBLIC:
        raise ValueError("public content digest mismatch")
    historical = json.loads(ANCHOR.read_text())
    candidates = [candidate for candidate in historical["candidates"]
                  if candidate.get("source") == "basis_row"
                  and independently_validate(public, candidate.get("f"), candidate.get("g"))["public_valid"]]
    if len(candidates) != 1:
        raise ValueError("expected exactly one freshly validated published B basis-row anchor")
    anchor = candidates[0]
    anchor_validation = independently_validate(public, anchor["f"], anchor["g"])
    patterns = []
    for index in (0, 2):
        if work_deadline is not None and time.monotonic() >= work_deadline:
            raise TimeoutError("inherited witness work deadline reached")
        P = select_pattern(public["n"], 40, 10001, index, "historical_sha256_shuffle_v1")
        coordinates = partition(public["n"], P, 4)
        bundle = build_input_bundle(public, **coordinates, policy=CandidatePolicy("full_nonzero"))
        expected_tail = {0: [121, 124, 125, 126], 2: [123, 124, 126, 127]}[index]
        if coordinates["T"] != expected_tail or len(bundle["id_mapping"]) != 80:
            raise ValueError("public pattern/full80 invariant failed")
        result = check_pattern(bundle, anchor["f"], anchor["g"], index)
        if stage3_path is not None:
            actual_path = Path(stage3_path) / f"pattern_{index}" / "input.json"
            if actual_path.exists():
                envelope = json.loads(actual_path.read_text())
                actual = envelope["payload"]
                if envelope["payload_sha256"] != content_sha256(actual):
                    raise ValueError("saved input payload digest mismatch")
                for field in ("public_instance", "dimensions", "coordinates", "candidate_batch", "targets", "id_mapping"):
                    if actual[field] != bundle[field]:
                        raise ValueError("actual saved input differs from regenerated public " + field)
                if actual["basis"]["original"] != bundle["basis"]["original"]:
                    raise ValueError("actual original lattice differs from public construction")
                result["actual_saved_input_public_mapping_matches"] = True
                result["actual_saved_input_file_sha256"] = sha256(actual_path.read_bytes()).hexdigest()
                result["actual_saved_reduced_basis_sha256"] = actual["basis"]["reduced_sha256"]
            else:
                # A timed-out or blocked reduction does not undo this independent
                # public consistency check, but no actual matrix is inferred.
                result["actual_saved_input_public_mapping_matches"] = None
                result["actual_saved_input_missing_reason"] = "reduction_not_completed_or_saved"
        if result["compatible_count"] != 2:
            raise ValueError("known compatible representative count changed")
        if index == 0 and (result["zero_tail_count"] != 2 or result["full80_covered_count"] != 0):
            raise ValueError("index 0 known zero-tail consistency failed")
        if index == 2 and (result["zero_tail_count"] != 0 or result["full80_covered_count"] != 2):
            raise ValueError("index 2 known nonzero coverage consistency failed")
        patterns.append(result)
    return {"schema": "ntru-stage3-witness-consistency-v1", "status": "passed", "all_checks_passed": True,
            "scope": "offline_witness_consistency_only_not_algorithm_recovery",
            "anchor_source": str(ANCHOR.relative_to(ROOT)),
            "anchor_source_file_sha256": sha256(ANCHOR.read_bytes()).hexdigest(),
            "anchor_source_branch": "basis_row", "anchor_pair": {"f": anchor["f"], "g": anchor["g"]},
            "anchor_public_validation": anchor_validation,
            "public_file_sha256": EXPECTED_FILE, "public_content_sha256": EXPECTED_PUBLIC,
            "patterns": patterns, "decoder_receives_witness_or_correct_tail_labels": False,
            "private_reference_content_read": False, "zero_target_decode_executed": False,
            "elapsed_seconds": time.monotonic() - started}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.request:
        from .common import write
        request = json.loads(arguments.request.read_text())
        started = time.monotonic()
        try:
            result = run(request["stage3_path"], request["work_deadline"])
            write(Path(request["stage3_path"]) / "witness_consistency.json", result)
            task_result = {"status": "completed", "all_checks_passed": True,
                           "elapsed_seconds": time.monotonic() - started,
                           "output": str(Path(request["stage3_path"]) / "witness_consistency.json")}
        except BaseException as exc:
            result = {"status": "blocked", "all_checks_passed": False,
                      "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
            write(Path(request["stage3_path"]) / "witness_consistency.json", result)
            task_result = {**result, "elapsed_seconds": time.monotonic() - started}
        write(arguments.output / "result.json", task_result)
        print(json.dumps({"status": task_result["status"], "elapsed_seconds": task_result["elapsed_seconds"]}))
        if task_result["status"] != "completed":
            raise SystemExit(2)
    else:
        result = run()
        with arguments.output.open("x") as stream:
            json.dump(result, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
        print(json.dumps({"status": result["status"], "elapsed_seconds": result["elapsed_seconds"]}))
