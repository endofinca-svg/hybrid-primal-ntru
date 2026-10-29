"""Read-only provenance capture; never open private/reference/truth paths.

This helper captures originals separately from the final numerical freeze.  It
does not construct lattices, decode targets, or import the numerical backend.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MAIN = ROOT / "experiments/ntru_hybrid_comparison/main_feasibility_20261027"
FORBIDDEN = ("private", "truth", "reference")


def permitted(path):
    return not any(word in str(path).lower() for word in FORBIDDEN)


def digest(path):
    path = Path(path)
    if not permitted(path):
        raise ValueError("forbidden content path")
    return sha256(path.read_bytes()).hexdigest()


def label(path):
    path = Path(path).absolute()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def resolve(name):
    path = Path(name)
    return path if path.is_absolute() else ROOT / path


def write_new(path, document):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def prior_maps():
    preflight = json.loads((MAIN / "preflight.json").read_text())
    sources = json.loads((MAIN / "sources.json").read_text())["source_sha256"]
    return preflight["protected_sha256"], sources


def protected_paths():
    protected, sources = prior_maps()
    paths = {resolve(name) for name in {*protected, *sources} if permitted(name)}
    paths.update(path for path in MAIN.rglob("*")
                 if path.is_file() and permitted(path) and "__pycache__" not in path.parts)
    # Protect the original backend in its entirety.  No build/cache/venv files
    # are read here; the relevant native extensions are included explicitly.
    backend = ROOT / "experiments/ntru_e2e/.deps/g6k_hybrid"
    for subdirectory in ("g6k", "kernel"):
        paths.update(path for path in (backend / subdirectory).rglob("*")
                     if path.is_file() and permitted(path)
                     and path.suffix in {".py", ".pyx", ".pxd", ".cpp", ".h", ".hpp", ".inl", ".so"})
    return sorted(paths)


def capture_original():
    protected, sources = prior_maps()
    paths = protected_paths()
    actual = {label(path): digest(path) for path in paths if path.is_file()}
    mismatches = {"historical_protected": [], "historical_sources": []}
    for category, expected in (("historical_protected", protected), ("historical_sources", sources)):
        for name, wanted in expected.items():
            if not permitted(name):
                continue
            path = resolve(name)
            got = actual.get(label(path))
            if got != wanted:
                mismatches[category].append({"path": name, "expected_sha256": wanted,
                                             "actual_sha256": got})
    excluded = sorted({name for name in {*protected, *sources} if not permitted(name)})
    document = {
        "schema": "ntru-stage3-original-protection-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(ROOT),
        "private_contents_read": False,
        "filter": "Reject any path containing private, truth, or reference (case insensitive) before read_bytes.",
        "excluded_content_paths": excluded,
        "protected_sha256": actual,
        "historical_digest_comparison": mismatches,
        "original_git_status": subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=ROOT, text=True).splitlines(),
        "applicable_agents_found": [],
        "agents_search_scope": ["/AGENTS.md", "/home/AGENTS.md", "/home/inca/AGENTS.md",
                                str(ROOT / "AGENTS.md"), "repository descendants"],
        "original_protected_file_count": len(actual),
    }
    write_new(HERE / "preflight_original.json", document)
    print(json.dumps({"protected_count": len(actual), "excluded_count": len(excluded),
                      "historical_mismatch_counts": {k: len(v) for k, v in mismatches.items()}}))


def freeze():
    _, historical_sources = prior_maps()
    paths = {resolve(name) for name in historical_sources if permitted(name)}
    comparison = ROOT / "experiments/ntru_hybrid_comparison"
    paths.update(comparison.glob("*.py"))
    paths.update((comparison / "tests").glob("*.py"))
    paths.update(path for path in HERE.rglob("*") if path.is_file()
                 and permitted(path) and path.suffix in {".py", ".pyx", ".pxd", ".cpp", ".h", ".hpp", ".inl", ".so", ".patch"})
    for name in ("plan.json", "environment.json", "command.json", "diagnostic_runtime.json", "preparation_access_incident.json", "preflight_ready/summary.json", "preflight_existing/tests_summary.json"):
        path = HERE / name
        if not path.is_file():
            raise ValueError("required pre-run freeze input missing: " + name)
        paths.add(path)
    frozen = {label(path): digest(path) for path in sorted(paths) if path.is_file() and permitted(path)}
    document = {"schema": "ntru-stage3-source-freeze-v1",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "private_contents_read": False, "source_sha256": frozen,
                "source_file_count": len(frozen),
                "scope": "Source, original numerical runtime, diagnostic runtime, plan, environment and command; no numerical outputs."}
    write_new(HERE / "sources.json", document)
    print(json.dumps({"frozen_count": len(frozen)}))


def verify():
    documents = [("original", HERE / "preflight_original.json", "protected_sha256")]
    if (HERE / "sources.json").exists():
        documents.append(("frozen", HERE / "sources.json", "source_sha256"))
    results = {}
    for kind, path, field in documents:
        expected = json.loads(path.read_text())[field]
        mismatches = []
        for name, wanted in expected.items():
            target = resolve(name)
            got = digest(target) if target.is_file() else None
            if got != wanted:
                mismatches.append({"path": name, "expected_sha256": wanted, "actual_sha256": got})
        results[kind] = {"file_count": len(expected), "mismatches": mismatches,
                         "all_match": not mismatches}
    print(json.dumps(results, indent=2))
    return results


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"capture-original", "freeze", "verify"}:
        raise SystemExit("usage: freeze_sources.py capture-original|freeze|verify")
    {"capture-original": capture_original, "freeze": freeze, "verify": verify}[sys.argv[1]]()
