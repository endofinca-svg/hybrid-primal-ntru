"""Import/version/hash probe only; no lattice construction or numerical job."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import platform
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from freeze_sources import digest, label, permitted, write_new
from experiments.ntru_hybrid_comparison.reduction import ReductionProtocol, backend_metadata


def main():
    backend = backend_metadata(ReductionProtocol())
    modules = {}
    for name in ("fpylll", "g6k", "g6k.siever", "g6k.slicer", "g6k.siever_params",
                 "numpy", "Cython", "cysignals"):
        module = import_module(name)
        path = Path(module.__file__).absolute()
        modules[name] = {"path": str(path), "sha256": digest(path)}
    loaded = {}
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and permitted(filename):
            path = Path(filename)
            if path.is_file() and ("site-packages" in str(path) or "g6k_hybrid" in str(path)):
                loaded[label(path)] = digest(path)
    prior_sources = json.loads((ROOT / "experiments/ntru_hybrid_comparison/main_feasibility_20261027/sources.json").read_text())["source_sha256"]
    comparisons = []
    for name, actual in loaded.items():
        if name in prior_sources:
            comparisons.append({"path": name, "actual_sha256": actual,
                                "historical_sha256": prior_sources[name], "match": actual == prior_sources[name]})
    prior_result = json.loads((ROOT / "experiments/ntru_hybrid_comparison/main_feasibility_20261027/run/D-main-0/owner/attempt_000/result.json").read_text())
    historical_backend = prior_result["reduction"]["backend"]
    checks = {
        "fpylll_version": backend["fpylll_version"] == historical_backend["fpylll_version"],
        "fplll_version": backend["fplll_version"] == historical_backend["fplll_version"],
        "python_version": backend["python"] == historical_backend["python"],
        "strategy_exact_metadata": backend["strategy"] == historical_backend["strategy"],
        "reduction_sources_exact_metadata": backend["source_hashes"] == historical_backend["source_hashes"],
        "all_compared_loaded_modules": all(row["match"] for row in comparisons),
    }
    frozen_original = json.loads((HERE / "preflight_original.json").read_text())
    document = {
        "schema": "ntru-stage3-environment-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Imports, versions and provenance only; no lattice construction, key generation, reduction or decoding.",
        "python": sys.version, "executable": sys.executable,
        "executable_realpath": str(Path(sys.executable).resolve()),
        "executable_sha256": digest(Path(sys.executable).resolve()),
        "platform": platform.platform(), "machine": platform.machine(),
        "versions": {name: metadata.version(name) for name in ("fpylll", "numpy", "Cython", "cysignals", "pytest")},
        "reduction_backend": backend, "modules": modules,
        "loaded_runtime_module_sha256": loaded,
        "runtime_config": json.loads((ROOT / "experiments/ntru_e2e/.deps/runtime.json").read_text()),
        "effective_thread_and_isolation_environment": {
            name: os.environ.get(name) for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH")},
        "historical_comparison": checks,
        "historical_loaded_module_comparisons": comparisons,
        "historical_compiled_projection_cap": prior_result["backend_metadata"]["compiled_projection_cap"],
        "compiled_projection_cap_evidence": "Historical running backend reported 64; original Siever/Slicer extension bytes exactly match the historical source freeze. Actual per-job constructor cap is recorded by the diagnostic backend.",
        "original_history_comparison": {
            name: {"mismatch_count": len(rows)} for name, rows in frozen_original["historical_digest_comparison"].items()},
        "private_contents_read": False,
    }
    write_new(HERE / "environment.json", document)
    print(json.dumps({"checks": checks, "compared_loaded_module_count": len(comparisons),
                      "versions": document["versions"], "strategy": backend["strategy"]}, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
