"""Run the new frozen baseline once, without changing historical experiments."""
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
    manifest = HERE / "run_manifest.json"
    if manifest.exists():
        raise FileExistsError("Preserving prior run; inspect the saved run manifest")
    plan_path = HERE / "plan.json"
    plan = json.loads(plan_path.read_text())
    baseline = HERE / "baseline"
    baseline.mkdir(exist_ok=False)
    provenance = HERE / "provenance"
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
    source_paths = list(HERE.glob("*.py")) + [ROOT / "experiments/ntru_e2e" / name for name in (
        "algebra.py", "generate.py", "evaluate.py", "record_command.py", "run_in_env.py")]
    sources = {str(path.relative_to(ROOT)): sha(path) for path in source_paths}
    for path in source_paths:
        target = provenance / "sources" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    state = {"schema": "ntru-plain-primal-run-v1", "status": "prepared",
             "started_utc": datetime.now(timezone.utc).isoformat(),
             "plan_sha256": sha(plan_path), "config_sha256": sha(config_path),
             "source_sha256": sources,
             "scope": "Only plain primal baseline, one fixed new key; later three methods not run"}

    def save():
        manifest.write_text(json.dumps(state, indent=2) + "\n")

    save()
    python = [sys.executable]
    instance = plan["instance"]
    generation = capture(python + ["-m", "experiments.ntru_e2e.generate", "--output-dir", str(HERE),
        "--n", str(instance["n"]), "--q", str(instance["q"]), "--wf", str(instance["w_f"]),
        "--wg", str(instance["w_g"]), "--seeds", str(instance["keygen_seed"])],
        provenance / "generate", cwd=ROOT, timeout=60)
    state["generation"] = generation
    if generation["exit_code"]:
        state["status"] = "generation_failed"
        save()
        return 1
    state["public_sha256"] = sha(HERE / "public/instance_01.json")
    state["truth_sha256"] = sha(HERE / "private/instance_01.truth.json")
    state["status"] = "online_running"
    save()
    online = capture(python + ["-m", "experiments.ntru_compare_n128_w12_w24_q97.solve_baseline",
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
