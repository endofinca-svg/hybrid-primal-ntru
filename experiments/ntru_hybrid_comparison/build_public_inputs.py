"""Build an original-only paired public input example, without lattice reduction."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random
import subprocess
import time

from experiments.ntru_e2e.algebra import partition
from .candidates import CandidatePolicy
from .inputs import build_input_bundle, content_sha256, create_working_copy, load_input_bundle, save_input_bundle
from .reduction import ReductionProtocol, backend_metadata


def build_example(public_path, output_path, *, k=40, kappa=4, seed=10001, pattern_index=0):
    """Reuse the historical public random-position rule without truth filtering."""
    for label, value in (("k", k), ("kappa", kappa), ("seed", seed), ("pattern_index", pattern_index)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(label + " must be a nonnegative integer")
    if kappa == 0:
        raise ValueError("this example is hybrid; kappa=0 belongs to primal")
    public_path, output_path = Path(public_path), Path(output_path)
    if output_path.exists() or output_path.with_suffix(output_path.suffix + ".manifest.json").exists():
        raise FileExistsError("example output already exists; choose a new path")
    started = time.perf_counter()
    public_bytes = public_path.read_bytes()
    public = json.loads(public_bytes)
    if k + kappa > public["n"]:
        raise ValueError("pattern and tail exceed public dimension")
    rng = random.Random(seed)
    for _ in range(pattern_index + 1):
        coordinates = partition(public["n"], rng.sample(range(public["n"]), k), kappa)
        backend_seed = rng.randrange(1, 2 ** 31)
    # An empty BKZ schedule is an unfrozen placeholder. Building does not call
    # even the initial LLL of this executable protocol.
    protocol = ReductionProtocol(schedule=(), seed=backend_seed)
    runtime = backend_metadata(protocol)  # metadata/imports only, no reduction
    here = Path(__file__).parent
    modules = [here / name for name in ("build_public_inputs.py", "inputs.py", "reduction.py",
                                        "id_mapping.py", "exact_lattice.py", "candidates.py")]
    code_version = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    provenance = {
        "kind": "public_input_build_only", "code_version": code_version,
        "module_sha256": {str(path): sha256(path.read_bytes()).hexdigest() for path in modules},
        "runtime": runtime, "source_public_path": str(public_path),
        "source_public_file_sha256": sha256(public_bytes).hexdigest(),
        "selection": {"pattern_rule": "random.Random(seed); per index sample(range(n),k), then draw backend seed",
                      "pattern_type": "random_position_set", "seed": seed, "pattern_index": pattern_index,
                      "tail_rule": "largest kappa surviving original f coordinates, increasing order",
                      "backend_seed": backend_seed, "reference_used": False,
                      "compatibility_filtering": False, "formal_experiment_selection_frozen": False},
    }
    construction = time.perf_counter()
    bundle = build_input_bundle(public, coordinates["P"], coordinates["C"], coordinates["T"],
                                CandidatePolicy("full_nonzero"), reduction_protocol=protocol,
                                provenance=provenance)
    construction_seconds = time.perf_counter() - construction
    saving = time.perf_counter()
    package_sha = save_input_bundle(output_path, bundle)
    save_seconds = time.perf_counter() - saving
    loading = time.perf_counter()
    first, second = load_input_bundle(output_path), load_input_bundle(output_path)
    assert first == second == bundle
    # Exercise independent mutable D/E data, without building a GSO or decoder.
    work_a = create_working_copy(first, use_reduced=False)
    work_b = create_working_copy(second, use_reduced=False)
    work_a["rows"][0][0] += 1
    work_a["targets"][0][0] += 1
    assert work_b["rows"] == bundle["basis"]["original"]
    assert work_b["targets"] == [row["used"] for row in bundle["targets"]]
    assert first == second == bundle
    load_check_seconds = time.perf_counter() - loading
    result = {
        "schema": "ntru-public-input-build-check-v1", "kind": "public_input_build_only",
        "input_path": str(output_path), "dimensions": bundle["dimensions"],
        "basis_status": bundle["basis"]["status"], "reduction_executed": False,
        "candidate_count": len(bundle["candidate_batch"]["candidates"]),
        "original_basis_sha256": bundle["basis"]["original_sha256"],
        "ordered_candidates_sha256": bundle["candidate_batch"]["ordered_sha256"],
        "ordered_targets_sha256": bundle["ordered_targets_sha256"],
        "id_mapping_sha256": bundle["id_mapping_sha256"], "payload_sha256": package_sha,
        "input_file_sha256": sha256(output_path.read_bytes()).hexdigest(),
        "counts": bundle["candidate_batch"]["counts"], "roundtrip_equal": True,
        "independent_working_copies": True, "gso_constructed": False,
        "nonzero_target_translation_count": sum(any(row["used_minus_raw"]) for row in bundle["targets"]),
        "selection": provenance["selection"],
        "timing": {"construction_seconds": construction_seconds, "validated_atomic_save_seconds": save_seconds,
                   "two_loads_and_copy_check_seconds": load_check_seconds,
                   "total_wall_seconds": time.perf_counter() - started,
                   "scope": "public example build/import metadata/validation/serialization/load check only; no reduction or decoding"},
    }
    manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--k", type=int, default=40)
    parser.add_argument("--kappa", type=int, default=4)
    parser.add_argument("--seed", type=int, default=10001)
    parser.add_argument("--pattern-index", type=int, default=0)
    args = parser.parse_args()
    result = build_example(args.public, args.output, k=args.k, kappa=args.kappa,
                           seed=args.seed, pattern_index=args.pattern_index)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
