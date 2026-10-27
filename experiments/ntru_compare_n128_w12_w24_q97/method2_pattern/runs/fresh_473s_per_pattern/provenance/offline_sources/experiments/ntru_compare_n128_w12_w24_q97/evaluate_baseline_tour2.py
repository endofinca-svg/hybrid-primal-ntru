"""Offline mathematical and scheduling audit for the fixed two-tour baseline.

The independent integer proof is delegated to ``evaluate_baseline``.  This
module additionally verifies the saved two-tour schedule and success metadata.
It never runs lattice reduction or reads the earlier eight-tour result.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .evaluate_baseline import evaluate_files as evaluate_mathematical_files


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def audit_protocol(config: dict, result: dict) -> dict:
    """Check completed stage records, permitting an interrupted final beta.

    A successful run may stop after tour one or two.  An unsuccessful beta can
    be left for the next beta only after its second tour, including when fplll
    reports ``clean=True``.  Errors/interruption may leave a final partial beta.
    """
    checks: dict[str, bool] = {}

    def check(name: str, condition: object) -> None:
        checks[name] = bool(condition)

    check("tour2_config_max_tours", _integer(config.get("bkz_max_tours_per_beta"))
          and config["bkz_max_tours_per_beta"] == 2)
    check("tour2_config_clean_does_not_stop", config.get("bkz_clean_stops_current_beta") is False)
    check("tour2_config_start", _integer(config.get("bkz_start")) and config["bkz_start"] == 30)
    check("tour2_config_jump", _integer(config.get("bkz_jump")) and config["bkz_jump"] == 1)

    raw_stages = result.get("stages")
    stages = raw_stages if isinstance(raw_stages, list) else []
    check("tour2_stages_nonempty", bool(stages))
    expected_beta, expected_tour = 30, 1
    dimension = result.get("dimension")
    upper = config.get("bkz_stop")
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            check(f"tour2_stage_{index}_record", False)
            continue
        kind, beta, tour = stage.get("kind"), stage.get("beta"), stage.get("tour")
        if index == 0:
            check("tour2_initial_lll", kind == "lll" and beta is None
                  and _integer(tour) and tour == 0)
        else:
            valid = (kind == "bkz" and _integer(beta) and _integer(tour)
                     and beta == expected_beta and tour == expected_tour)
            if _integer(dimension):
                valid = valid and beta <= dimension if _integer(beta) else False
            if _integer(upper):
                valid = valid and beta <= upper if _integer(beta) else False
            check(f"tour2_stage_{index}_beta_and_tour", valid)
            # Advance the expected schedule independently of the supplied record.
            if expected_tour == 1:
                expected_tour = 2
            else:
                expected_beta, expected_tour = expected_beta + 1, 1
        expected_count = int(result.get("status") == "success" and index == len(stages) - 1)
        count = stage.get("candidate_count")
        check(f"tour2_stage_{index}_candidate_count", _integer(count) and count == expected_count)

    final = stages[-1] if stages and isinstance(stages[-1], dict) else {}
    candidate = result.get("found_candidate")
    if result.get("status") == "success":
        check("tour2_success_final_stage", bool(final) and final.get("kind") in {"lll", "bkz"})
        if final.get("kind") == "lll":
            check("tour2_lll_success_has_no_bkz", len(stages) == 1)
        check("tour2_first_success_beta", "first_success_beta" in result
              and result["first_success_beta"] == final.get("beta")
              and (result["first_success_beta"] is None or _integer(result["first_success_beta"])))
        check("tour2_first_success_tour", _integer(result.get("first_success_tour"))
              and result["first_success_tour"] == final.get("tour"))
        check("tour2_first_success_stage", result.get("first_success_stage") == final.get("kind"))
        check("tour2_candidate_final_metadata", isinstance(candidate, dict)
              and candidate.get("stage_id") == final.get("stage_id")
              and candidate.get("beta") == final.get("beta")
              and (candidate.get("beta") is None or _integer(candidate.get("beta")))
              and _integer(candidate.get("tour")) and candidate["tour"] == final.get("tour"))
    else:
        check("tour2_unsuccessful_has_no_candidate", candidate is None)
        check("tour2_unsuccessful_has_no_success_metadata", all(
            result.get(name) is None for name in ("first_success_beta", "first_success_tour", "first_success_stage")))
        if result.get("status") == "exhausted":
            check("tour2_exhausted_final_beta_completed", final.get("kind") == "bkz" and final.get("tour") == 2)
            if _integer(upper) and _integer(dimension):
                check("tour2_exhausted_reached_stop", final.get("beta") == min(upper, dimension))

    failed = [name for name, passed in checks.items() if not passed]
    return {"protocol": "lll_then_bkz_start30_jump1_exactly2_tours_per_unsuccessful_beta",
            "protocol_passed": not failed, "checks": checks, "failed_checks": failed,
            "completed_bkz_tours": sum(isinstance(stage, dict) and stage.get("kind") == "bkz"
                                       for stage in stages)}


def evaluate_files(run_dir: str | Path, *, public_path: str | Path | None = None,
                   truth_path: str | Path | None = None,
                   config_path: str | Path | None = None,
                   result_path: str | Path | None = None,
                   exact_determinant: bool = False) -> dict:
    started = time.perf_counter()
    root = Path(run_dir).resolve()
    config_path = Path(config_path) if config_path is not None else root / "baseline_tour2/config.json"
    result_path = Path(result_path) if result_path is not None else root / "baseline_tour2/result.json"
    report = evaluate_mathematical_files(
        root, public_path=public_path, truth_path=truth_path,
        config_path=config_path, result_path=result_path, exact_determinant=exact_determinant)
    config = json.loads(config_path.read_text())
    result = json.loads(result_path.read_text())
    protocol = audit_protocol(config, result)
    report["schema"] = "ntru-primal-baseline-tour2-offline-audit-v1"
    report["tour2_protocol"] = protocol
    report["checks"].update(protocol["checks"])
    report["failed_checks"].extend(protocol["failed_checks"])
    report["audit_passed"] = report["audit_passed"] and protocol["protocol_passed"]
    report["audit_wall_seconds"] = time.perf_counter() - started
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    for name in ("public", "truth", "config", "result", "output"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--exact-determinant", action="store_true")
    args = parser.parse_args(argv)
    report = evaluate_files(args.run_dir, public_path=args.public, truth_path=args.truth,
                            config_path=args.config, result_path=args.result,
                            exact_determinant=args.exact_determinant)
    output = args.output or args.run_dir / "baseline_tour2/audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"audit_passed": report["audit_passed"],
                      "classification": report["classification"], "output": str(output)}))
    return 0 if report["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
