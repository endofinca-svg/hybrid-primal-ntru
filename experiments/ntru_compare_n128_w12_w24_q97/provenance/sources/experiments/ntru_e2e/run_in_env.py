"""Run Python in the experiment venv with the pinned source ahead of other G6K copies."""
import json
import os
from pathlib import Path
import sys


def main():
    here = Path(__file__).resolve().parent
    configuration = here / ".deps" / "runtime.json"
    if not configuration.exists():
        raise SystemExit("Run bootstrap.py first: experiment runtime.json is missing")
    runtime = json.loads(configuration.read_text())
    env = dict(os.environ)
    env.update(runtime["environment"])
    os.execve(runtime["python"], [runtime["python"], *sys.argv[1:]], env)


if __name__ == "__main__":
    main()
