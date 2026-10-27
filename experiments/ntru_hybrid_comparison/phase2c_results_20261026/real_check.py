"""One externally timed, explicitly bounded stage2C backend interface check.

No lattice reduction is called. The main n128 package is never loaded here.
The 28-dimensional hand fixture is an interface test, not an attack benchmark.
"""

from hashlib import sha256
import json
from pathlib import Path
import sys
import time

from experiments.ntru_hybrid_comparison.inputs import load_input_bundle
from experiments.ntru_hybrid_comparison.paired_decode import run_fixed_basis


HERE = Path(__file__).resolve().parent


def main():
    attempt = sys.argv[1]
    snapshot = json.loads((HERE / (attempt + ".sources.json")).read_text())
    for name, wanted in snapshot["source_sha256"].items():
        assert sha256(Path(name).read_bytes()).hexdigest() == wanted, name
    with (HERE / (attempt + ".started.json")).open("x") as stream:
        json.dump({"scope": "small real fixed-basis interfaces only", "hard_timeout_seconds": 30,
                   "max_dimension": 28, "max_external_targets_per_method": 2,
                   "new_LLL_BKZ_calls": 0, "main_n128_compute_calls": 0}, stream, indent=2)
    legacy = HERE.parent / "phase2b_results_20261026/backend_check.reduced_input.json"
    micro = HERE / "micro_n15_d28.public_input.json"
    for path in (legacy, micro):
        package = load_input_bundle(path, require_reduced_verified=True)
        assert package["dimensions"]["d"] <= 32 and len(package["targets"]) <= 16
        assert package["public_instance"]["n"] != 128
    common = {"float_type": "mpfr", "precision_bits": 192}
    slicer = {**common, "sieve_dim": 28, "seed": 20261026, "db_size": 128, "nrand": 2,
              "max_iterations": 1, "preprocessing": "sample_database_only", "buckets": 4,
              "blocks": 1, "multi_hash": 1, "interface_test_mode": True,
              "force_kernel_iterations": True}
    cases = [("legacy_E_full", legacy, "E", "fixed_basis_full_batch", common, 2),
             ("micro_E_full", micro, "E", "fixed_basis_full_batch", common, 2),
             ("micro_D_full", micro, "D", "fixed_basis_full_batch", slicer, 2),
             ("micro_E_first", micro, "E", "fixed_basis_first_public_valid", common, 1),
             ("micro_D_first", micro, "D", "fixed_basis_first_public_valid", slicer, 1)]
    summary = {"scope": "real small backend interface tests; forced Slicer work is not a performance result",
               "attempt": attempt, "rows": [], "status": "running"}
    all_results = {}
    for name, path, method, mode, config, batch_size in cases:
        result = run_fixed_basis(path, method, mode=mode, batch_size=batch_size,
                                 backend_config=config, output_path=HERE / (attempt + "." + name + ".json"))
        all_results[name] = result
        row = {"case": name, "method": method, "mode": mode, "input_path": str(path),
               "basis_sha256": result["pairing"]["starting_basis_sha256"],
               "progress": result["progress"], "metrics": result["metrics"],
               "timings": result["timings"], "status": result["status"], "stop_reason": result["stop_reason"]}
        summary["rows"].append(row)
        (HERE / (attempt + ".summary.json")).write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps({"case": name, "status": result["status"], "completed": result["progress"]["completed_count"],
                          "valid_targets": result["progress"]["public_valid_target_count"],
                          "kernel_calls": result["metrics"].get("slicer_kernel_calls", 0),
                          "stop_reason": result["stop_reason"]}), flush=True)
        assert result["status"] == "completed", result.get("backend_error", result["protocol_errors"])
        assert result["progress"]["conservation_holds"]
    e, d = all_results["micro_E_full"], all_results["micro_D_full"]
    assert e["pairing"] == d["pairing"]
    assert e["progress"]["submitted_count"] == d["progress"]["submitted_count"] == 2
    assert e["progress"]["completed_count"] == d["progress"]["completed_count"] == 2
    assert d["metrics"]["preprocess_count"] == 1 and d["metrics"]["slicer_kernel_calls"] >= 1
    assert any(batch.get("call_evidence") for batch in e["backend_batches"])
    assert all_results["micro_E_first"]["stop_reason"] == "first_public_valid"
    assert all_results["micro_E_first"]["progress"]["unsubmitted_count"] == 1
    summary.update(status="passed", pair_input_equal=True, real_Babai_executed=True,
                   real_Slicer_preprocessing_and_kernel_executed=True, new_LLL_BKZ_calls=0,
                   main_n128_compute_calls=0)
    (HERE / (attempt + ".summary.json")).write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
