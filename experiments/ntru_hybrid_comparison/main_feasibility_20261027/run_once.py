"""Execute the single frozen B/E/D main-instance batch, without retuning."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time


HERE = Path(__file__).resolve().parent


def main():
    frozen = json.loads((HERE / "sources.json").read_text())
    for name, wanted in frozen["source_sha256"].items():
        assert sha256(Path(name).read_bytes()).hexdigest() == wanted, name
    argv = ["/usr/bin/python3", "-B", "-m", "experiments.ntru_hybrid_comparison.guarded",
            "--plan", str(HERE / "plan.json"), "--output", str(HERE / "run")]
    document = {"argv": argv, "started_utc": datetime.now(timezone.utc).isoformat(),
                "global_budget_seconds": 1920, "trial_budget_seconds": 600,
                "trial_cleanup_reserve_seconds": 5, "guard_cleanup_reserve_seconds": .2,
                "trial_order": ["B", "E", "D"], "max_patterns_per_trial": 3,
                "outer_safety_timeout_seconds": 1940, "source_snapshot": "sources.json",
                "scope": "exactly one frozen main-instance B/E/D trial each; all retries remain within original deadlines"}
    started = time.monotonic()
    with (HERE / "command.json").open("x") as stream:
        json.dump(document, stream, indent=2)
        stream.write("\n")
    with (HERE / "stdout.txt").open("x") as out, (HERE / "stderr.txt").open("x") as err:
        process = subprocess.Popen(argv, stdout=out, stderr=err)
        with (HERE / "started.json").open("x") as stream:
            json.dump({"pid": process.pid, "started_monotonic": started, **document}, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"event": "main_batch_started", "pid": process.pid, "output": str(HERE / "run")}), flush=True)
        returncode = process.wait(timeout=1940)
    result = {"completed_utc": datetime.now(timezone.utc).isoformat(), "exit_code": returncode,
              "external_wall_seconds": time.monotonic() - started,
              "no_automatic_reruns": True}
    with (HERE / "execution.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result), flush=True)
    raise SystemExit(returncode)


if __name__ == "__main__":
    main()
