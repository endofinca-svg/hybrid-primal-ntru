"""Bounded repair verification; saved projections are replayed without new BKZ."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from experiments.ntru_hybrid_comparison.supervisor import supervise_command, probe_environment
from experiments.ntru_hybrid_comparison.stage3_fixed_basis_diagnostics_20261028.common import write, append
from experiments.ntru_hybrid_comparison.stage3_fixed_basis_diagnostics_20261028.access_v2 import access_check

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
STAGE3 = HERE.parent / 'stage3_fixed_basis_diagnostics_20261028'
MODULE = 'experiments.ntru_hybrid_comparison.' + HERE.name


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze():
    paths = list(HERE.glob('*.py')) + [HERE / 'plan.json', HERE / 'environment.json'] + [
        REPO / name for name in (
            'experiments/ntru_e2e/slicer_adapter.py',
            'experiments/ntru_e2e/algebra.py',
            'experiments/ntru_hybrid_comparison/fixed_backend.py',
            'experiments/ntru_hybrid_comparison/inputs.py',
            'experiments/ntru_hybrid_comparison/validation.py',
            'experiments/ntru_hybrid_comparison/supervisor.py',
            'experiments/ntru_hybrid_comparison/candidates.py',
            'experiments/ntru_hybrid_comparison/exact_lattice.py',
            'experiments/ntru_hybrid_comparison/patterns.py',
            'experiments/ntru_e2e/.deps/runtime.json',
        )
    ]
    paths += [STAGE3 / name for name in (
        'audit_entry_v2.py', 'access_v2.py', 'common.py',
        'artifact_manifest.json', 'pattern_2/input.json',
        'witness_consistency.json', 'witness.py',
        'supplementary_validate.py', 'validate.py',
    )]
    paths += [STAGE3 / ('decode_p2_' + name) / 'target_telemetry.jsonl'
              for name in ('E', 'D-skip', 'D-normal', 'D-probe-1', 'D-probe-400')]
    paths += list((REPO / 'experiments/ntru_e2e/tests').glob('test_lift*.py'))
    files = {str(path.relative_to(REPO)): sha(path) for path in sorted(set(paths))}
    record = {'files': files, 'frozen_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'corrected decoder, replay and validation programs, runtime and explicit saved public inputs'}
    write(HERE / 'sources.json', record)
    return record


def check_frozen():
    record = json.loads((HERE / 'sources.json').read_text())
    mismatches = [name for name, want in record['files'].items() if sha(REPO / name) != want]
    return {'files_checked': len(record['files']), 'mismatches': mismatches, 'all_match': not mismatches}


def owner(request):
    started = request['global_started_monotonic']
    deadline = started + 4800
    plan = json.loads((HERE / 'plan.json').read_text())
    runtime = json.loads((REPO / 'experiments/ntru_e2e/.deps/runtime.json').read_text())
    records, stopped = [], False
    for job in plan['jobs']:
        tick = time.monotonic()
        if stopped or tick >= deadline - 10:
            records.append({'task_id': job['task_id'], 'status': 'not_run', 'wall_seconds': None,
                            'reason': 'prior_task_invariant_or_cleanup_failure_or_budget_exhausted'})
            continue
        integrity = check_frozen()
        if not integrity['all_match']:
            raise RuntimeError('frozen repair source or saved input changed: ' + repr(integrity))
        output = HERE / job['task_id']
        output.mkdir(exist_ok=False)
        hard = min(tick + job['budget_seconds'], deadline - .5)
        timing = {'started_monotonic': tick, 'work_deadline': hard - 5,
                  'hard_deadline': hard, 'global_deadline': deadline}
        worker_request = {**job['fields'], **timing}
        write(output / 'request.json', worker_request)
        argv = ['/usr/bin/strace', '-f', '-qq', '-s', '4096', '-yy', '-e',
                'trace=open,openat,openat2', '-o', str(output / 'file_opens.log'),
                runtime['python'], '-B', str(STAGE3 / 'audit_entry_v2.py'),
                '--audit-log', str(output / 'file_access.jsonl'), MODULE + '.' + job['module'],
                '--request', str(output / 'request.json'), '--output', str(output)]
        env = {**os.environ, **runtime['environment']}
        write(output / 'command.json', {'argv': argv, 'timing': timing,
              'environment': runtime['environment'], 'task_budget_seconds': job['budget_seconds']})
        append(HERE / 'owner.events.jsonl', {'event': 'task_started', 'task_id': job['task_id'], 'monotonic': tick})
        print(json.dumps({'task_started': job['task_id'], 'elapsed_seconds': tick-started}), flush=True)
        supervision = supervise_command(argv, work_deadline=timing['work_deadline'],
            hard_deadline=hard-.25, cwd=REPO, env=env,
            stdout_path=output / 'stdout.txt', stderr_path=output / 'stderr.txt', ownership='process_group')
        write(output / 'supervision.json', supervision)
        access = access_check(output)
        write(output / 'access_validation.json', access)
        try:
            worker = json.loads((output / 'result.json').read_text())
        except (OSError, ValueError):
            worker = None
        finished = time.monotonic()
        completed = (supervision['execution_status'] == 'completed' and supervision['returncode'] == 0
                     and worker is not None and worker.get('status') == 'completed')
        row = {'task_id': job['task_id'], 'status': 'completed' if completed else 'incomplete_or_failed',
               'worker_status': worker.get('status') if worker else None,
               'started_monotonic': tick, 'finished_monotonic': finished,
               'wall_seconds': finished-tick, 'timing': timing,
               'deadline_met': finished <= hard,
               'cleanup_verified': supervision.get('cleanup_verified'),
               'access_checks_passed': access['all_checks_passed'],
               'supervision': supervision}
        records.append(row)
        write(HERE / 'owner.partial.json', {'records': records, 'elapsed_seconds': finished-started})
        append(HERE / 'owner.events.jsonl', {'event': 'task_finished', 'task_id': job['task_id'],
               'status': row['status'], 'monotonic': finished})
        print(json.dumps({'task_finished': job['task_id'], 'status': row['status'],
                          'wall_seconds': row['wall_seconds']}), flush=True)
        stopped = (not completed or not row['deadline_met'] or not row['cleanup_verified']
                   or not row['access_checks_passed'])
    write(HERE / 'owner.final.json', {'records': records,
          'finished_monotonic': time.monotonic(), 'elapsed_seconds': time.monotonic()-started,
          'all_tasks_completed': all(r['status'] == 'completed' for r in records),
          'all_cleanup_verified': all(r.get('cleanup_verified') for r in records if r['status'] != 'not_run')})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner-request')
    parser.add_argument('--freeze', action='store_true')
    args = parser.parse_args()
    if args.freeze:
        print(json.dumps({'frozen_files': len(freeze()['files'])}))
        return
    if args.owner_request:
        owner(json.loads(Path(args.owner_request).read_text()))
        return
    if (HERE / 'numerical_started.json').exists():
        raise RuntimeError('repair verification has already started; no implicit retries')
    preflight = json.loads((HERE / 'preflight_regression/result.json').read_text())
    additional = json.loads((HERE / 'preflight_additional/result.json').read_text())
    if not preflight.get('passed') or not additional.get('passed'):
        raise RuntimeError('repair regressions must pass before main-instance verification')
    integrity = check_frozen()
    write(HERE / 'pre_numerical_integrity.json', integrity)
    if not integrity['all_match']:
        raise RuntimeError('frozen sources changed')
    started = time.monotonic()
    request = {'global_started_monotonic': started,
               'started_utc': datetime.now(timezone.utc).isoformat(), 'global_budget_seconds': 4800}
    write(HERE / 'numerical_started.json', request)
    write(HERE / 'supervisor_environment.json', probe_environment())
    argv = [sys.executable, '-B', '-m', MODULE + '.run', '--owner-request', str(HERE / 'numerical_started.json')]
    supervision = supervise_command(argv, work_deadline=started+4795, hard_deadline=started+4799.75,
        cwd=REPO, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        stdout_path=HERE / 'owner.stdout.txt', stderr_path=HERE / 'owner.stderr.txt', ownership='session')
    finished = time.monotonic()
    write(HERE / 'global_supervision.json', supervision)
    owner_result = json.loads((HERE / 'owner.final.json').read_text()) if (HERE / 'owner.final.json').exists() else {}
    sources_after = check_frozen()
    passed = (supervision['execution_status'] == 'completed' and supervision['returncode'] == 0
              and owner_result.get('all_tasks_completed') and owner_result.get('all_cleanup_verified')
              and sources_after['all_match'] and finished <= started+4800)
    write(HERE / 'execution.json', {'global_started_monotonic': started, 'finished_monotonic': finished,
          'all_tasks_completed': bool(passed),
          'numerical_total_wall_seconds': finished-started, 'global_budget_seconds': 4800,
          'global_hard_deadline_met': finished <= started+4800,
          'cleanup_verified': supervision.get('cleanup_verified'), 'supervision': supervision,
          'sources_after': sources_after,
          'scope': 'saved_projection_replay_and_ideal_projection_check; no BKZ or new sieve kernel search'})
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
