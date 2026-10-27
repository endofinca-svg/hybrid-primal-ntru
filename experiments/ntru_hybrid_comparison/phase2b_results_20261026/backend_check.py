"""One authorized tiny backend interface check, never imported by unit tests.

Run ONLY inside an external 30-second timeout.  Existing execution artifact is
an intentional no-rerun guard: post-processing can inspect it without another
LLL or BKZ invocation.
"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.inputs import (
    attach_reduction, build_input_bundle, create_working_copy,
    load_input_bundle, save_input_bundle,
)
from experiments.ntru_hybrid_comparison.reduction import (
    ReductionProtocol, ReductionStage, execute_reduction, protocol_manifest,
)


DIRECTORY = Path(__file__).resolve().parent


def write_new(name, value):
    with (DIRECTORY / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def main():
    guard = DIRECTORY / "backend_check.started.json"
    write_new(guard.name, {"purpose": "one tiny backend interface check, not a benchmark or phase3 acceptance",
                          "n": 4, "dimension": 6, "lll_calls_allowed": 1,
                          "bkz_tours_allowed": 1, "beta": 4,
                          "external_timeout_seconds": 30, "decoder_calls_allowed": 0})
    protocol = ReductionProtocol(schedule=(ReductionStage(4, 1),), seed=20261026)
    public = {"schema": "ntru-e2e-public-v1", "instance_id": "phase2b-hand-fixed-tiny-interface",
              "n": 4, "q": 17, "ring": "x^n+1", "h": [16, 1, 0, 0],
              "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 2}}
    bundle = build_input_bundle(public, [1], [2, 0], [3], CandidatePolicy("full_nonzero"),
                                reduction_protocol=protocol_manifest(protocol),
                                provenance={"kind": "test_fixture", "selection": "hand-fixed public algebra, no keygen or reference input"})
    assert public["n"] <= 8 and bundle["dimensions"]["d"] <= 16
    assert protocol.schedule == (ReductionStage(4, 1),)
    input_path = DIRECTORY / "backend_check.original_input.json"
    save_input_bundle(input_path, bundle)
    execution = execute_reduction(bundle["basis"]["original"], protocol)
    write_new("backend_check.execution.json", execution)
    result = {"purpose": "tiny real backend interface check only", "n": 4, "dimension": 6,
              "protocol": execution["protocol"], "metadata": execution["record"].get("backend"),
              "actual": execution["record"]["actual"], "status": "failed",
              "decoder_calls": 0, "main_n128_reduction_calls": 0,
              "record_status": execution["record"]["status"],
              "exact_verification": execution["record"]["exact_lattice_verification"]}
    if execution["record"]["status"] != "completed":
        result["blocking_error"] = execution["record"].get("error")
        write_new("backend_check.result.json", result)
        raise SystemExit(1)
    assert execution["record"]["actual"]["completed_lll_calls"] == 1
    assert execution["record"]["actual"]["completed_tours"] == 1
    assert execution["record"]["actual"]["precision_bits"] == 192
    assert execution["record"]["exact_lattice_verification"]["verified"]
    required = ("original_basis", "reduced_basis", "transform", "protocol", "record")
    reduced = attach_reduction(bundle, {k: execution[k] for k in required})
    reduced_path = DIRECTORY / "backend_check.reduced_input.json"
    save_input_bundle(reduced_path, reduced)
    disk_before = sha256(reduced_path.read_bytes()).hexdigest()
    first_input = load_input_bundle(reduced_path, require_reduced_verified=True)
    second_input = load_input_bundle(reduced_path, require_reduced_verified=True)
    first = create_working_copy(first_input, float_type="mpfr", precision_bits=192)
    second = create_working_copy(second_input, float_type="mpfr", precision_bits=192)
    second_rows = deepcopy(second["rows"])
    second_targets = deepcopy(second["targets"])
    second_ids = deepcopy(second["id_mapping"])
    second_gso_entry = int(second["gso"].B[0, 0])
    first["rows"][0][0] += 17
    first["targets"][0][0] += 17
    first["id_mapping"][0]["backend_integer_id"] += 1000
    first["gso"].B[0, 0] += 17
    checks = {"separate_gso_objects": first["gso"] is not second["gso"],
              "separate_gso_matrices": first["gso"].B is not second["gso"].B,
              "other_rows_unchanged": second["rows"] == second_rows,
              "other_targets_unchanged": second["targets"] == second_targets,
              "other_ids_unchanged": second["id_mapping"] == second_ids,
              "other_gso_matrix_unchanged": int(second["gso"].B[0, 0]) == second_gso_entry,
              "disk_unchanged": sha256(reduced_path.read_bytes()).hexdigest() == disk_before,
              "gso_creation_did_reduce": False}
    assert all(v for k, v in checks.items() if k != "gso_creation_did_reduce")
    result.update(status="passed", paired_gso_isolation=checks,
                  reduced_input_file=reduced_path.name, reduced_input_file_sha256=disk_before,
                  main_experiment_result=False, performance_benchmark=False,
                  submitted_count=0, completed_count=0, projection_group_count=None)
    write_new("backend_check.result.json", result)
    print(json.dumps({"status": "passed", "n": 4, "d": 6, "mpfr_bits": 192,
                      "lll_calls": 1, "bkz_tours": 1, "beta": 4,
                      "exact_lattice_verified": True, "independent_gso_copies": True}))


if __name__ == "__main__":
    main()
