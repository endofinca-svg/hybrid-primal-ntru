"""Finish independent evaluation if the original output channel disappears."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from experiments.ntru_e2e.record_command import capture

HERE = Path(__file__).resolve().parent
RUN = HERE.parent / "runs/fresh_473s_per_pattern"
IDENTITIES = json.loads((HERE / "background_guard_inputs.json").read_text())["process_start_ticks"]


def alive(pid, ticks):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        return fields[21] == ticks and fields[2] != "Z"
    except FileNotFoundError:
        return False


def status(phase, **details):
    value = {"phase": phase, "utc": datetime.now(timezone.utc).isoformat(), **details}
    temporary = HERE / "background_guard_status.json.tmp"
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(HERE / "background_guard_status.json")
    print(json.dumps(value), flush=True)


status("watching_existing_processes")
while any(alive(pid, ticks) for pid, ticks in IDENTITIES.items()):
    if (RUN / "output_manifest.json").exists():
        status("complete", reason="Original wrapper produced final audited outputs")
        raise SystemExit(0)
    time.sleep(15)
state = json.loads((RUN / "run_manifest.json").read_text())
if (RUN / "output_manifest.json").exists():
    status("complete", reason="Original wrapper produced final audited outputs")
    raise SystemExit(0)
if state["status"] != "online_finished":
    status("needs_attention", reason="Online run did not finish; no solver restart or budget change", online_status=state["status"])
    raise SystemExit(1)
status("independent_evaluate_running", reason="Original wrapper ended without final output manifest")
metadata = capture([sys.executable, "-m", "experiments.ntru_compare_n128_w12_w24_q97.method2_pattern.summarize_budgeted", "--run-dir", str(RUN)], HERE / "evaluate_background_guard")
status("complete" if metadata["exit_code"] == 0 else "needs_attention", exit_code=metadata["exit_code"])
raise SystemExit(metadata["exit_code"])
