"""Check small-target UID insertion in the actual, pinned Slicer extension.

Run through ``run_in_env.py`` after rebuilding the experiment dependency.  This
component regression uses an artificial public basis and never reads an NTRU
instance, solver result, or private reference.  It does not test BDD recovery.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
COMMIT = "bd72687b2ebbaff16fb692259f83b327bbcd9586"


def main() -> None:
    runtime = json.loads((HERE / ".deps" / "runtime.json").read_text())
    source = (HERE / ".deps" / "g6k_hybrid").resolve()
    assert Path(runtime["source"]).resolve() == source, "Unexpected dependency source"
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    assert head == COMMIT, f"Unexpected upstream HEAD: {head}"

    modules = {
        name: importlib.import_module(name)
        for name in ("fpylll", "g6k", "g6k.siever", "g6k.siever_params", "g6k.slicer")
    }
    origins = {name: str(Path(module.__file__).resolve()) for name, module in modules.items()}
    for name, origin in origins.items():
        if name.startswith("g6k"):
            assert Path(origin).is_relative_to(source), f"Wrong {name} import: {origin}"

    from fpylll import FPLLL, IntegerMatrix
    from g6k.siever import Siever
    from g6k.siever_params import SieverParams
    from g6k.slicer import RandomizedSlicer

    dimension, seed, n_per_target = 28, 67321, 1
    FPLLL.set_random_seed(seed)
    sieve = Siever(
        IntegerMatrix.identity(dimension),
        SieverParams(threads=1, otf_lift=False),
        seed=seed,
    )
    sieve.initialize_local(0, 0, dimension)
    # Real kernel sampling supplies a nonempty lattice database.  No randomized
    # target copies are requested: n_per_target=1 avoids the upstream sampler's
    # large-database assumption and isolates target UID behavior.
    sieve.grow_db(8)
    assert len(sieve) >= 2, "Expected at least two actual lattice database entries"
    slicer = RandomizedSlicer(sieve, seed=seed)
    slicer.set_nthreads(1)

    small = [1e-4] + [0.0] * (dimension - 1)
    probes = [
        ("small_nonzero", small, 1),
        ("negative_duplicate", [-value for value in small], 0),
        ("exact_zero", [0.0] * dimension, 0),
    ]
    checks = []
    for name, target, expected_growth in probes:
        before = int(slicer.stats["cdb_t-size:"])
        slicer.grow_db_with_target(target, n_per_target=n_per_target)
        after = int(slicer.stats["cdb_t-size:"])
        checks.append({
            "name": name,
            "target": target,
            "n_per_target": n_per_target,
            "before": before,
            "after": after,
            "expected_growth": expected_growth,
            "actual_growth": after - before,
            "passed": after - before == expected_growth,
        })

    actual_targets = [
        {"residual": list(residual), "internal_target_id": int(target_id)}
        for residual, target_id in slicer.itervalues_cdb_t(return_with_index=True)
    ]
    passed = all(check["passed"] for check in checks)
    result = {
        "schema": "ntru-e2e-slicer-uid-regression-v1",
        "status": "passed" if passed else "failed",
        "dimension": dimension,
        "seed": seed,
        "lattice_db_size": len(sieve),
        "n_per_target": n_per_target,
        "upstream_commit": head,
        "source": str(source),
        "python": sys.version,
        "executable": sys.executable,
        "versions": {name: importlib.metadata.version(name) for name in ("fpylll", "numpy", "Cython")},
        "module_origins": origins,
        "extension_sha256": {
            name: hashlib.sha256(Path(origins[name]).read_bytes()).hexdigest()
            for name in ("g6k.siever", "g6k.slicer")
        },
        "target_uid_source_sha256": hashlib.sha256(
            (source / "kernel" / "hash_table.inl").read_bytes()
        ).hexdigest(),
        "checks": checks,
        "target_database": actual_targets,
        "slicer_stats": dict(slicer.stats),
    }
    print(json.dumps(result, indent=2, allow_nan=False), flush=True)
    assert passed, "Slicer target UID insertion regression failed"


if __name__ == "__main__":
    main()
