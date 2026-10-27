"""Capture a command, its exact argv, exit status, resource use and both streams."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time


def capture(command, prefix, *, cwd=None, timeout=None):
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = prefix.with_suffix(".command.json")
    if metadata_path.exists():
        raise FileExistsError(f"Preserving existing command evidence: {metadata_path}")
    stdout_path = prefix.with_suffix(".stdout.txt")
    stderr_path = prefix.with_suffix(".stderr.txt")
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    metadata = {"argv": [str(x) for x in command], "cwd": str(Path(cwd or Path.cwd()).resolve()),
                "started_utc": datetime.now(timezone.utc).isoformat(), "timeout_seconds": timeout,
                "stdout": stdout_path.name, "stderr": stderr_path.name, "status": "running"}
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                exit_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                exit_code = process.wait()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    metadata.update(status="timeout" if timed_out else "finished", exit_code=exit_code,
                    wall_seconds=time.perf_counter() - started,
                    cpu_seconds=(after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime),
                    peak_rss_kib=after.ru_maxrss, finished_utc=datetime.now(timezone.utc).isoformat())
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command after -- is required")
    metadata = capture(command, args.prefix, cwd=args.cwd, timeout=args.timeout)
    print(json.dumps(metadata, indent=2))
    raise SystemExit(metadata["exit_code"] if metadata["exit_code"] >= 0 else 1)


if __name__ == "__main__":
    main()
