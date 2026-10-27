"""Run the strict two-tour baseline on the existing fixed public instance."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys

from experiments.ntru_e2e.record_command import capture


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    manifest = HERE / "run_manifest_tour2.json"
    if manifest.exists():
        raise FileExistsError("Preserving prior run; inspect the saved run manifest")
    plan_path = HERE / "plan_tour2.json"
    plan = json.loads(plan_path.read_text())
    config = plan["baseline"]
    if (config["bkz_max_tours_per_beta"] != 2 or config["bkz_clean_stops_current_beta"]
            or config["bkz_start"] != 30 or config["bkz_jump"] != 1):
        raise ValueError("This run requires beta 30 onward, exactly two unsuccessful tours per beta, unit jump, and no clean early exit")
    public_path = HERE / "public/instance_01.json"
    truth_path = HERE / "private/instance_01.truth.json"
    historical_manifest = json.loads((HERE / "run_manifest.json").read_text())
    public = json.loads(public_path.read_text())
    if (sha(public_path) != historical_manifest["public_sha256"]
            or sha(truth_path) != historical_manifest["truth_sha256"]):
        raise ValueError("The reused public instance or truth differs from the original frozen instance")
    if any(public[name] != plan["instance"][name] or public[name] != config[name]
           for name in ("n", "q")):
        raise ValueError("Public instance parameters differ from the new frozen plan")
    if any(public["distribution"][name] != plan["instance"][name]
           or public["distribution"][name] != config[name] for name in ("w_f", "w_g")):
        raise ValueError("Public instance weights differ from the new frozen plan")
    baseline = HERE / "baseline_tour2"
    baseline.mkdir(exist_ok=False)
    provenance = HERE / "provenance_tour2"
    provenance.mkdir(exist_ok=False)
    config_path = baseline / "config.json"
    config_path.write_text(json.dumps(plan["baseline"], indent=2) + "\n")
    import fpylll
    from fpylll import BKZ
    strategy_path = Path(BKZ.DEFAULT_STRATEGY.decode())
    shutil.copyfile(strategy_path, provenance / "fplll_default_strategy.json")
    environment = {"python": sys.version, "executable": sys.executable,
                   "fpylll_version": fpylll.__version__, "platform": platform.platform(),
                   "strategy_sha256": sha(strategy_path),
                   "runtime_configuration_sha256": sha(ROOT / "experiments/ntru_e2e/.deps/runtime.json")}
    cpu_info = Path("/proc/cpuinfo")
    if cpu_info.exists():
        environment["cpu_model"] = next((line.split(":", 1)[1].strip() for line in cpu_info.read_text().splitlines()
                                         if line.startswith("model name")), "unknown")
    (provenance / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    # Freeze the complete online implementation and stable reporting helpers.
    # The independent offline protocol auditor is recorded separately before
    # its later execution and is never an online dependency.
    source_paths = [HERE / name for name in (
        "__init__.py", "solve_baseline.py", "run_baseline.py", "summarize_baseline.py",
        "evaluate_baseline.py", "test_solver.py", "test_evaluate_baseline.py",
        "solve_baseline_tour2.py", "run_baseline_tour2.py", "summarize_baseline_tour2.py")]
    source_paths += [ROOT / "experiments/ntru_e2e" / name for name in (
        "algebra.py", "generate.py", "evaluate.py", "record_command.py", "run_in_env.py")]
    sources = {str(path.relative_to(ROOT)): sha(path) for path in source_paths}
    shutil.copyfile(plan_path, provenance / "plan_tour2.frozen.json")
    for path in source_paths:
        target = provenance / "sources" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    state = {"schema": "ntru-plain-primal-tour2-run-v1", "status": "prepared",
             "started_utc": datetime.now(timezone.utc).isoformat(),
             "plan_sha256": sha(plan_path), "config_sha256": sha(config_path),
             "source_sha256": sources,
             "public_sha256": sha(public_path), "truth_sha256": sha(truth_path),
             "instance_reused_from": "run_manifest.json",
             "offline_protocol_auditor_recorded_separately": True,
             "historical_run_status": "The prior eight-tour run is retained only as history and excluded from this baseline and its timing",
             "scope": "Only plain primal baseline, reused fixed public instance, fresh LLL and strictly two unsuccessful BKZ tours per beta; later three methods not run"}

    def save():
        manifest.write_text(json.dumps(state, indent=2) + "\n")

    save()
    python = [sys.executable]
    state["status"] = "online_running"
    save()
    online = capture(python + ["-m", "experiments.ntru_compare_n128_w12_w24_q97.solve_baseline_tour2",
        "--public", str(HERE / "public/instance_01.json"), "--config", str(config_path),
        "--output", str(baseline / "result.json")], provenance / "baseline_online", cwd=ROOT)
    state["online_command"] = online
    state["status"] = "online_finished" if not online["exit_code"] else "online_failed"
    state["finished_utc"] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({"status": state["status"], "online_wall_seconds": online["wall_seconds"],
                      "exit_code": online["exit_code"], "result": str(baseline / "result.json")}), flush=True)
    return 0 if not online["exit_code"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
