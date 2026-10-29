"""Bounded stage3 owner: global session plus separately owned numerical PGs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time

from experiments.ntru_hybrid_comparison.supervisor import supervise_command, probe_environment
from .common import write, append

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MODULE = 'experiments.ntru_hybrid_comparison.' + HERE.name


def access_check(output):
    """Check explicit Python blocks and native path opens after each task."""
    output = Path(output)
    python_log, native_log = output / 'file_access.jsonl', output / 'file_opens.log'
    denied, native_forbidden = [], []
    if python_log.exists():
        denied = [json.loads(line) for line in python_log.read_text().splitlines()
                  if json.loads(line).get('denied')]
    if native_log.exists():
        for line in native_log.read_text(errors='replace').splitlines():
            names = re.findall(r'"([^"\n]*)"', line)
            if names and any(word in names[0].lower() for word in ('private', 'truth', 'reference')):
                native_forbidden.append(line)
    return {'audit_hook_log_present': python_log.exists(), 'native_open_log_present': native_log.exists(),
            'denied_python_open_attempts': denied, 'native_forbidden_path_open_attempts': native_forbidden,
            'all_checks_passed': python_log.exists() and native_log.exists() and not denied and not native_forbidden,
            'scope': 'this numerical worker process and traced descendants; preparation scan uncertainty is separate'}


def window(start, budget, global_start):
    hard = min(start + budget, global_start + 4800)
    return {'started_monotonic': start, 'work_deadline': hard - 5,
            'hard_deadline': hard, 'budget_seconds': budget, 'cleanup_reserve_seconds': 5}


def owner(request):
    global_start = request['global_started_monotonic']
    plan = json.loads((HERE / 'plan.json').read_text())
    runtime = json.loads((REPO / 'experiments/ntru_e2e/.deps/runtime.json').read_text())
    records = []
    stopped = False

    def task(task_id, module, arguments, budget, fields=None, diagnostic_runtime=False):
        nonlocal stopped
        tick = time.monotonic()
        if stopped or tick >= global_start + 4790:
            row = {'task_id': task_id, 'status': 'not_run', 'wall_seconds': None,
                   'reason': 'cleanup_failure_or_insufficient_remaining_global_work_budget'}
            records.append(row)
            return row
        timing = window(tick, budget, global_start)
        output = HERE / task_id
        output.mkdir(exist_ok=False)
        worker_request = {**(fields or {}), **timing}
        write(output / 'request.json', worker_request)
        argv = ['/usr/bin/strace', '-f', '-qq', '-e', 'trace=open,openat,openat2',
                '-o', str(output / 'file_opens.log'), runtime['python'], '-B', str(HERE / 'audit_entry.py'),
                '--audit-log', str(output / 'file_access.jsonl'), module, '--request', str(output / 'request.json'),
                '--output', str(output)] + arguments
        env = {**os.environ, **runtime['environment']}
        if diagnostic_runtime:
            diagnostic = json.loads((HERE / 'diagnostic_runtime.json').read_text())
            env.update(diagnostic['environment'])
        write(output / 'command.json', {'argv': argv, 'environment': {k: env[k] for k in
              ('PYTHONPATH', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
              'timing': timing})
        append(HERE / 'owner.events.jsonl', {'event': 'task_started', 'task_id': task_id,
               'monotonic': tick, 'timing': timing})
        print(json.dumps({'task_started': task_id, 'elapsed': tick-global_start}), flush=True)
        supervision = supervise_command(argv, work_deadline=timing['work_deadline'],
            hard_deadline=timing['hard_deadline']-.25, cwd=REPO, env=env,
            stdout_path=output / 'stdout.txt', stderr_path=output / 'stderr.txt',
            ownership='process_group')
        write(output / 'supervision.json', supervision)
        access = access_check(output)
        write(output / 'access_validation.json', access)
        try:
            worker = json.loads((output / 'result.json').read_text())
        except (OSError, ValueError):
            worker = None
        finished = time.monotonic()
        normal = supervision['execution_status'] == 'completed' and supervision['returncode'] == 0
        row = {'task_id': task_id, 'status': worker.get('status') if normal and worker else supervision['execution_status'],
               'worker_status': worker.get('status') if worker else None,
               'wall_seconds': finished-tick, 'timing': timing, 'supervision': supervision,
               'cleanup_verified': supervision.get('cleanup_verified'),
               'deadline_met': finished <= timing['hard_deadline'],
               'result_path': str(output / 'result.json'), 'started_monotonic': tick,
               'finished_monotonic': finished, 'file_access_validation': access}
        if normal and worker and worker.get('status') not in ('completed', 'success'):
            row['status'] = worker.get('status')
        records.append(row)
        write(HERE / 'owner.partial.json', {'records': records,
               'elapsed_seconds': time.monotonic()-global_start})
        append(HERE / 'owner.events.jsonl', {'event': 'task_finished', 'task_id': task_id,
               'monotonic': finished, 'status': row['status']})
        print(json.dumps({'task_finished': task_id, 'status': row['status'], 'wall': row['wall_seconds']}), flush=True)
        if not supervision.get('cleanup_verified') or not row['deadline_met'] or not access['all_checks_passed']:
            stopped = True
        return row

    for index in (0, 2):
        task(f'pattern_{index}', MODULE+'.reduce_worker', [], 600,
             {'pattern_index': index, 'public_path': plan['public_path'],
              'reduction_protocol': plan['reduction_protocol']})

    # Witness labels live only in this independent process and never enter decoder requests.
    witness_task = task('witness_check', MODULE+'.witness', [], 120,
                        {'stage3_path': str(HERE)})

    configurations = [(0, 'E'), (0, 'D-normal'), (2, 'E'), (2, 'D-skip'),
                      (2, 'D-normal'), (2, 'D-probe-1'), (2, 'D-probe-400')]
    for index, configuration in configurations:
        task_id = f'decode_p{index}_{configuration}'
        manifest_path = HERE / f'pattern_{index}/basis_manifest.json'
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        reason = None
        basis_task = next((r for r in records if r['task_id'] == f'pattern_{index}'), {})
        if (basis_task.get('status') != 'completed'
                or not manifest.get('eligible_for_same_basis_diagnostics')):
            reason = 'basis_missing_or_historical_index0_digest_mismatch'
        witness = HERE / 'witness_consistency.json'
        witness_result = json.loads(witness.read_text()) if witness.exists() else {}
        witness_patterns = {p['pattern_index']: p for p in witness_result.get('patterns', [])}
        if (witness_task.get('status') != 'completed'
                or not witness_result.get('all_checks_passed')
                or witness_patterns.get(index, {}).get('actual_saved_input_public_mapping_matches') is not True):
            reason = 'witness_consistency_not_confirmed'
        if configuration == 'D-probe-400':
            probe_path = HERE / 'decode_p2_D-probe-1/result.json'
            probe = json.loads(probe_path.read_text()) if probe_path.exists() else {}
            probe_task = next((r for r in records if r['task_id'] == 'decode_p2_D-probe-1'), {})
            if (probe_task.get('status') != 'completed' or probe.get('status') != 'completed'
                    or not probe.get('probe_400_gate_passed')):
                reason = 'probe1_control_counter_state_integrity_gate_not_passed'
        if reason:
            records.append({'task_id': task_id, 'status': 'not_run', 'wall_seconds': None, 'reason': reason})
            write(HERE / 'owner.partial.json', {'records': records})
            continue
        task(task_id, MODULE+'.decode_worker', [], 120 if configuration == 'E' else 600,
             {'input_path': str(HERE / f'pattern_{index}/input.json'), 'configuration': configuration,
              'pattern_index': index}, diagnostic_runtime=True)

    # Independent validator reopens artifacts and public candidates; no reliance on success flags.
    task('independent_validation', MODULE+'.validate', [], 180, {'stage3_path': str(HERE)})
    from .summarize import summarize
    summarize(HERE, records)
    result = {'records': records, 'global_started_monotonic': global_start,
              'finished_monotonic': time.monotonic(), 'global_budget_seconds': 4800,
              'numerical_owner_wall_seconds': time.monotonic()-global_start,
              'all_cleanup_verified': all(r.get('cleanup_verified') is True for r in records if r['status'] != 'not_run')}
    write(HERE / 'owner.final.json', result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner-request')
    args = parser.parse_args()
    if args.owner_request:
        owner(json.loads(Path(args.owner_request).read_text()))
        return
    if (HERE / 'numerical_started.json').exists():
        raise RuntimeError('stage3 numerical run already attempted; no implicit retries')
    ready = json.loads((HERE / 'preflight_ready/summary.json').read_text())
    if ready.get('passed') is not True:
        raise RuntimeError('targeted preflight tests must pass before numerical launch')
    from .freeze_sources import verify
    integrity = verify()
    write(HERE / 'pre_numerical_integrity.json', integrity)
    if set(integrity) != {'original', 'frozen'} or not all(v['all_match'] for v in integrity.values()):
        raise RuntimeError('original protection and frozen source verification required before numerical launch')
    # First numerical job starts now; startup/import, all subsequent preparation and IO are charged.
    started = time.monotonic()
    request = {'global_started_monotonic': started,
               'utc': datetime.now(timezone.utc).isoformat(), 'global_budget_seconds': 4800}
    write(HERE / 'numerical_started.json', request)
    write(HERE / 'supervisor_environment.json', probe_environment())
    argv = [sys.executable, '-B', '-m', MODULE+'.run', '--owner-request', str(HERE / 'numerical_started.json')]
    supervision = supervise_command(argv, work_deadline=started+4795, hard_deadline=started+4799.75,
        cwd=REPO, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        stdout_path=HERE / 'owner.stdout.txt', stderr_path=HERE / 'owner.stderr.txt', ownership='session')
    write(HERE / 'global_supervision.json', supervision)
    finished = time.monotonic()
    write(HERE / 'execution.json', {'global_started_monotonic': started,
          'finished_monotonic': finished, 'numerical_total_wall_seconds': finished-started,
          'global_budget_seconds': 4800, 'global_hard_deadline_met': finished<=started+4800,
          'cleanup_verified': supervision.get('cleanup_verified'), 'supervision': supervision,
          'timing_scope': 'first numerical owner launch through all input, compute, diagnostics, state saves, validation and cleanup; report writing excluded'})


if __name__ == '__main__':
    main()
