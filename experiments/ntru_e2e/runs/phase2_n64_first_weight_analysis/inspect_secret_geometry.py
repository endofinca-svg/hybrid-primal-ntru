"""Recheck saved n=64 first-weight geometry with exact integer arithmetic.

This is offline analysis: it imports only the integer algebra module, reads
saved artifacts, and never performs lattice reduction, decoding, or sieving.
Output creation is exclusive so an earlier analysis cannot be overwritten.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[2]
ROOT = HERE.parents[1]
OUT = Path(__file__).resolve().parent
CASE = HERE / "runs/phase2/n64_w4_8"
sys.path.insert(0, str(ROOT))

from experiments.ntru_e2e.algebra import inverse_mod, negacyclic_mul, signed_rotate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pair(candidate):
    return tuple(candidate["f"]), tuple(candidate["g"])


def rank_mod_prime(rows, prime):
    """Integer elimination modulo the public prime; a lower bound over Q."""
    matrix = [[value % prime for value in row] for row in rows]
    rank = 0
    for column in range(len(matrix[0])):
        pivot = next((i for i in range(rank, len(matrix))
                      if matrix[i][column]), None)
        if pivot is None:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        inverse = pow(matrix[rank][column], -1, prime)
        matrix[rank] = [value * inverse % prime for value in matrix[rank]]
        for i in range(rank + 1, len(matrix)):
            factor = matrix[i][column]
            matrix[i] = [(value - factor * pivot_value) % prime
                         for value, pivot_value in zip(matrix[i], matrix[rank])]
        rank += 1
        if rank == len(matrix):
            break
    return rank


def main():
    input_hashes = {}
    source_hashes = {str(path): sha(path) for path in (
        Path(__file__), HERE / "algebra.py", HERE / "run_in_env.py")}

    def read(path):
        input_hashes[str(path)] = sha(path)
        return json.loads(path.read_text())

    instances = []
    completed_row_arms = Counter()
    completed_trials = 0
    compatible_nonzero_rotation_occurrences = 0
    slicer_candidate_occurrences = 0
    for instance_dir in sorted(CASE.glob("instance_*")):
        public = read(CASE / "public" / (instance_dir.name + ".json"))
        truth = read(CASE / "private" / (instance_dir.name + ".truth.json"))
        n, q = public["n"], public["q"]
        assert (n, q) == (64, 257)
        assert sum(value != 0 for value in truth["f"]) == 4
        assert sum(value != 0 for value in truth["g"]) == 8
        inverse = inverse_mod(truth["f"], q)
        assert negacyclic_mul(truth["f"], inverse, q) == [1] + [0] * (n - 1)
        assert negacyclic_mul(truth["f"], public["h"], q) == [
            value % q for value in truth["g"]]
        orbit = {(tuple(signed_rotate(truth["f"], shift)),
                  tuple(signed_rotate(truth["g"], shift))): shift
                 for shift in range(2 * n)}
        assert len(orbit) == 2 * n
        trials = []
        for path in sorted(instance_dir.glob("*.result.json")):
            result = read(path)
            coordinates = result["coordinates"]
            pattern, core, tail = (coordinates[key] for key in ("P", "C", "T"))
            core_size = len(core)
            assert (len(pattern), core_size, len(tail)) == (2, 58, 4)
            compatible = {shift for (f, _), shift in orbit.items()
                          if all(f[index] == 0 for index in pattern)}
            zero_tail = {shift for (f, _), shift in orbit.items()
                         if all(f[index] == 0 for index in pattern + tail)}
            golden_tails = Counter(tuple(f[index] for index in tail)
                                   for (f, _), shift in orbit.items()
                                   if shift in compatible - zero_tail)
            lower_bound_unsigned = n - 4 * (len(pattern) + len(tail))
            assert len(zero_tail) >= 2 * lower_bound_unsigned == 80
            arms = {}
            pairs_by_method = {}
            for method, arm in result["arms"].items():
                observed = {pair(candidate) for candidate in arm["candidates"]}
                assert observed <= orbit.keys()
                shifts = {orbit[candidate] for candidate in observed}
                pairs_by_method[method] = observed
                if method.endswith("_rows"):
                    assert shifts <= zero_tail
                    if arm["status"] == "complete":
                        assert shifts == zero_tail
                        completed_row_arms[method] += 1
                arms[method] = {
                    "status": arm["status"],
                    "saved_candidate_occurrences": len(arm["candidates"]),
                    "distinct_raw_candidates": len(observed),
                    "all_candidates_in_planted_signed_rotation_orbit": True,
                    "observed_orbit_shifts": sorted(shifts),
                    "equals_full_zero_tail_orbit": shifts == zero_tail,
                }
            subspaces = {}
            if result["status"] == "complete":
                completed_trials += 1
                compatible_nonzero_rotation_occurrences += len(compatible - zero_tail)
                slicer_candidate_occurrences += len(pairs_by_method["slicer"])
                assert len(golden_tails) == 8
                assert len(pairs_by_method["slicer"]) == 8
                assert pairs_by_method["slicer"] == pairs_by_method["bkz_babai"]
                for stage in ("lll", "bkz"):
                    basis = result["bases"][stage]
                    identities = []
                    norms_sq = []
                    for row in basis:
                        z = [0] * n
                        for index, value in zip(core, row[n:]):
                            z[index] = value
                        identities.append(
                            negacyclic_mul(truth["f"], row[:n])
                            == negacyclic_mul(truth["g"], z))
                        norms_sq.append(sum(value * value for value in row))
                    assert identities == [True] * core_size + [False] * n
                    # Core projections have full rank modulo q, hence over Q.
                    rank = rank_mod_prime([row[n:] for row in basis[:core_size]], q)
                    assert rank == core_size
                    subspaces[stage] = {
                        "identity": "f*y = g*z over Z[x]/(x^64+1), without coefficient reduction mod q",
                        "basis_row_identity_flags": identities,
                        "exactly_prefix_rows_satisfy_identity": True,
                        "prefix_row_count": core_size,
                        "prefix_core_rank_mod_q": rank,
                        "prefix_core_rank_over_Q": core_size,
                        "basis_row_norms_sq": norms_sq,
                        "prefix_min_norm_sq": min(norms_sq[:core_size]),
                        "prefix_max_norm_sq": max(norms_sq[:core_size]),
                        "first_nonprefix_norm_sq": norms_sq[core_size],
                        "slicer_projection_start_row_zero_based":
                            result["dimension"] - result["configuration"]["sieve_dim"],
                    }
            trials.append({
                "trial_id": path.name.removesuffix(".result.json"),
                "worker_status": result["status"],
                "coordinates": coordinates,
                "dimension": result["dimension"],
                "unsigned_zero_tail_rotation_lower_bound": lower_bound_unsigned,
                "compatible_signed_rotations": len(compatible),
                "zero_tail_signed_rotations": len(zero_tail),
                "compatible_nonzero_tail_signed_rotations": len(compatible - zero_tail),
                "unique_golden_nonzero_tails": len(golden_tails),
                "golden_tail_rotation_multiplicities": [
                    {"tail": list(tail_values), "signed_rotation_count": count}
                    for tail_values, count in sorted(golden_tails.items())],
                "arms": arms,
                "exact_secret_subspace_checks": subspaces,
            })
        instances.append({"instance_id": instance_dir.name,
                          "truth_f_invertible_mod_q_verified": True,
                          "public_key_relation_verified": True,
                          "signed_rotation_orbit_size": len(orbit), "trials": trials})
    assert dict(completed_row_arms) == {"lll_rows": 11, "bkz_rows": 11}
    assert completed_trials == 4
    assert compatible_nonzero_rotation_occurrences == 116
    assert slicer_candidate_occurrences == 32
    assert all(sha(path) == digest for path, digest in input_hashes.items())
    assert all(sha(path) == digest for path, digest in source_hashes.items())
    report = {
        "schema": "ntru-phase2-n64-saved-secret-geometry-v1",
        "scope": "saved n64_w4_8 artifacts only; offline integer algebra; no solver/reduction/kernel",
        "input_sha256": input_hashes, "source_sha256": source_hashes,
        "inputs_unchanged_after_analysis": True,
        "completed_row_arms_equal_entire_zero_tail_orbit": dict(completed_row_arms),
        "complete_trial_count": completed_trials,
        "complete_trial_compatible_nonzero_rotation_occurrences":
            compatible_nonzero_rotation_occurrences,
        "complete_trial_slicer_candidate_occurrences": slicer_candidate_occurrences,
        "interpretation": {
            "short_subspace_dimension": 58,
            "dimension_reason": "f invertible mod q implies invertible over Q; y=f^-1*g*z with z restricted to 58 core coordinates",
            "prefix_spans_short_subspace": "58 exact-identity rows have core rank 58 mod q, hence are independent over Q and span this graph",
            "same_tail_rotation_differences": "differences of two correct same-tail rotations satisfy f*y=g*z and z_P=z_T=0; they lie in this prefix span",
            "tail_projection_consequence": "same-tail correct rotations differ only within the first-58-row span and have the same orthogonal projection onto the final 32 or 40 GSO dimensions",
            "candidate_count_caution": "kernel target submission does not enumerate all golden rotations; these data store one raw candidate for each of 8 golden tails per complete trial; Slicer can in principle return multiple candidates per target",
        },
        "instances": instances,
    }
    destination = OUT / "secret_geometry.json"
    with destination.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(destination),
                      "completed_row_arms": dict(completed_row_arms),
                      "complete_trials_with_exact_58_row_secret_subspace": completed_trials,
                      "compatible_nonzero_rotation_occurrences": 116,
                      "slicer_candidate_occurrences": 32,
                      "input_files_unchanged": True}, indent=2))


if __name__ == "__main__":
    main()
