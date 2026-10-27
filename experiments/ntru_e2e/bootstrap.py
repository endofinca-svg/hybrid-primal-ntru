"""Build a fresh, pinned G6K source tree; never modify the supplied source or base env."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

COMMIT = "bd72687b2ebbaff16fb692259f83b327bbcd9586"
URL = "https://github.com/ElenaKirshanova/g6k_hybrid.git"
HERE = Path(__file__).resolve().parent


def apply_recorded_patch(source, patch, run):
    """Accept exactly the recorded patch or a pristine checkout, preserving other edits.

    The temporary Git index computes the complete expected tracked diff without
    changing the checkout's real index or working files.  A reverse-apply check
    alone would also accept unrelated edits elsewhere in a patched file.
    """
    source, patch = Path(source).resolve(), Path(patch).resolve()
    actual = run("patch_existing_diff", ["git", "diff", "--binary", "--full-index", "HEAD"], source)
    patch_bytes = patch.read_bytes()
    if not patch_bytes.strip():
        if actual:
            raise SystemExit("Refusing tracked upstream edits: the recorded patch is empty")
        return "not_required"
    with tempfile.TemporaryDirectory(prefix="ntru-bootstrap-index-") as temporary:
        index_env = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
        run("patch_expected_base", ["git", "read-tree", "HEAD"], source, env_update=index_env)
        run("patch_expected_apply", ["git", "apply", "--cached", str(patch)], source, env_update=index_env)
        expected = run("patch_expected_diff", ["git", "diff", "--binary", "--full-index", "--cached", "HEAD"],
                       source, env_update=index_env)
    if not expected:
        raise SystemExit("Recorded upstream patch does not change the pinned source")
    if actual == expected:
        return "already_applied"
    if actual:
        raise SystemExit("Refusing upstream tracked edits that differ from the recorded patch")
    run("patch_check", ["git", "apply", "--check", str(patch)], source)
    run("patch_apply", ["git", "apply", str(patch)], source)
    applied = run("patch_applied_diff", ["git", "diff", "--binary", "--full-index", "HEAD"], source)
    if applied != expected:
        raise SystemExit("Applied upstream patch differs from the recorded patch")
    return "applied"


def create_record_directory(provenance):
    """Allocate a new evidence directory so reruns never overwrite old logs."""
    provenance = Path(provenance).resolve()
    provenance.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return Path(tempfile.mkdtemp(prefix=f"bootstrap_{stamp}_", dir=provenance))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-python", default="/home/inca/miniforge3/envs/g6k/bin/python")
    parser.add_argument("--source", default="/home/inca/g6k_hybrid")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--deps", type=Path, default=HERE / ".deps",
                        help="Isolated dependency/build directory; use a new path for a clean rebuild")
    parser.add_argument("--patch", type=Path, default=HERE / "provenance" / "upstream_source.patch",
                        help="Recorded minimal patch against the pinned upstream commit")
    parser.add_argument("--provenance", type=Path, default=HERE / "provenance")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if not args.patch.is_file():
        parser.error(f"Recorded upstream patch is missing: {args.patch}")
    deps = args.deps.expanduser().resolve()
    deps.mkdir(parents=True, exist_ok=True)
    records = create_record_directory(args.provenance)
    patch = records / "upstream_source.patch"
    shutil.copyfile(args.patch, patch)
    patch_sha256 = hashlib.sha256(patch.read_bytes()).hexdigest()
    print(f"Build evidence: {records}", flush=True)
    entries = []
    base = Path(args.base_python).expanduser().absolute()
    venv = deps / "venv"
    source = deps / "g6k_hybrid"
    base_prefix = Path(subprocess.check_output([str(base), "-c", "import sys;print(sys.prefix)"], text=True).strip())
    env = dict(os.environ)
    env.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "BLIS_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
                "PATH": str(venv / "bin") + os.pathsep + str(base_prefix / "bin") + os.pathsep + env.get("PATH", ""),
                "PYTHONPATH": str(source) + os.pathsep + str(HERE.parents[1]),
                "CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++"})

    def run(label, command, cwd=None, timeout=None, env_update=None):
        print(f"[{label}] {command}", flush=True)
        started = time.perf_counter()
        stdout = records / f"{label}.stdout.txt"
        stderr = records / f"{label}.stderr.txt"
        command_env = dict(env, **(env_update or {}))
        with stdout.open("x") as out, stderr.open("x") as err:
            result = subprocess.run(command, cwd=cwd, env=command_env, stdout=out, stderr=err, timeout=timeout)
        entry = {"label": label, "argv": command, "cwd": str(cwd or Path.cwd()),
                 "exit_code": result.returncode, "wall_seconds": time.perf_counter() - started,
                 "stdout": stdout.name, "stderr": stderr.name,
                 "finished_utc": datetime.now(timezone.utc).isoformat()}
        entries.append(entry)
        (records / "bootstrap_commands.json").write_text(json.dumps(entries, indent=2) + "\n")
        print(f"[{label}] exit={result.returncode}, seconds={entry['wall_seconds']:.3f}", flush=True)
        if result.returncode:
            print(stderr.read_text()[-8000:], file=sys.stderr)
            raise SystemExit(result.returncode)
        return stdout.read_text()

    if not source.exists():
        origin = args.source if Path(args.source).exists() else URL
        run("clone", ["git", "clone", "--no-hardlinks", "--no-checkout", origin, str(source)])
        run("checkout", ["git", "checkout", "--detach", COMMIT], source)
    head = run("upstream_head", ["git", "rev-parse", "HEAD"], source).strip()
    if head != COMMIT:
        raise SystemExit(f"Refusing unexpected upstream HEAD {head}")
    patch_status = apply_recorded_patch(source, patch, run)
    patched_diff = run("upstream_patched_diff", ["git", "diff", "--binary", "--full-index", "HEAD"], source)
    if not venv.exists():
        run("create_venv", [str(base), "-m", "venv", "--system-site-packages", str(venv)])
    python = str(venv / "bin" / "python")
    runtime = {"python": python, "base_python": str(base), "source": str(source),
               "upstream_url": URL, "upstream_commit": head,
               "patch": {"recorded_path": str(args.patch.resolve()), "snapshot_path": str(patch),
                         "sha256": patch_sha256, "status": patch_status},
               "provenance": str(records), "environment": {key: env[key] for key in [
                   "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE", "OMP_NUM_THREADS",
                   "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                   "PATH", "PYTHONPATH"]},
               "isolation": "Independent venv, read-only system-site-packages from base conda env; local source rebuilt."}
    (records / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")
    run("dependencies", [python, "-c", "import fpylll,Cython,numpy,cysignals,pytest;print(fpylll.__version__,Cython.__version__,numpy.__version__)"])
    run("autoreconf", ["autoreconf", "-i"], source)
    run("configure", ["./configure", "--with-max-sieving-dim=64", "--with-stats=1", "--with-stats-slicer=1"], source)
    # Header-only upstream patches may not trigger distutils' dependency scan.
    run("build", [python, "setup.py", "build_ext", "--inplace", "--force", f"-j{args.jobs}"], source)
    inspect_code = '''import importlib,importlib.metadata,json,platform,sys,sysconfig
from pathlib import Path
modules={name:importlib.import_module(name) for name in ['fpylll','g6k','g6k.siever','g6k.slicer','numpy','Cython','cysignals']}
from g6k.slicer import RandomizedSlicer
expected=Path(sys.argv[1]).resolve()
assert all(Path(modules[name].__file__).resolve().is_relative_to(expected) for name in ['g6k','g6k.siever','g6k.slicer'])
print(json.dumps({'python':sys.version,'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,'platform':platform.platform(),'compiler':sysconfig.get_config_var('CC'),'modules':{k:v.__file__ for k,v in modules.items()},'versions':{k:importlib.metadata.version(k) for k in ['fpylll','numpy','Cython','cysignals','pytest','setuptools']}},indent=2))
'''
    inspection = json.loads(run("imports", [python, "-c", inspect_code, str(source)]))
    inspection.update({"runtime": runtime, "upstream_commit": head, "max_sieving_dimension": 64,
                       "compiler_version": run("compiler", ["/usr/bin/g++", "--version"]).strip()})
    final_diff = run("upstream_diff", ["git", "diff", "--binary", "--full-index", "HEAD"], source)
    if final_diff != patched_diff:
        raise SystemExit("Upstream tracked source changed during the build; runtime was not published")
    inspection["source_files_sha256"] = {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in [
        "README.rst", "setup.py", "configure.ac", "hybrid_attack.py", "run_prog_hyb.py", "tailBDD.py",
        "g6k/slicer.pyx", "kernel/slicer.cpp", "kernel/hash_table.inl", "utils.py", "LICENSE"]}
    shutil.copyfile(source / "LICENSE", records / "upstream_LICENSE")
    inspection["upstream_license"] = {"source_path": str(source / "LICENSE"),
                                      "recorded_copy": str(records / "upstream_LICENSE"),
                                      "sha256": inspection["source_files_sha256"]["LICENSE"]}
    (records / "environment.json").write_text(json.dumps(inspection, indent=2) + "\n")
    # Publish a usable runtime only after the rebuild and import-path checks pass.
    runtime_path = deps / "runtime.json"
    temporary_runtime = deps / "runtime.json.tmp"
    temporary_runtime.write_text(json.dumps(runtime, indent=2) + "\n")
    temporary_runtime.replace(runtime_path)
    print("Pinned source built and import paths verified. Upstream smoke is a separate recorded step.", flush=True)


if __name__ == "__main__":
    main()
