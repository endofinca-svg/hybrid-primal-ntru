"""Summarize observed fixed-basis diagnostics without inferring E2E costs."""
import csv
import json
from pathlib import Path

from .common import write


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def summarize(stage, records):
    stage = Path(stage)
    validation = read(stage / 'validation.json', {})
    rows, details = [], {}
    for task in records:
        if not task['task_id'].startswith('decode_'):
            continue
        name = task['task_id']
        result = read(stage / name / 'result.json', {})
        counters = result.get('counters', {})
        audited = validation.get('configurations', {}).get(name.removeprefix('decode_'), {})
        valid = audited.get('independently_public_valid_returned_candidates')
        summaries = result.get('target_summaries', [])
        minima = {}
        for phase in ('babai', 'pre', 'post'):
            available = [(s['backend_integer_id'], s.get(phase + '_min_full')) for s in summaries
                         if s.get(phase + '_min_full') is not None]
            if available:
                identifier, entry = min(available, key=lambda pair: pair[1]['full_norm_sq'])
                minima[phase] = {'backend_integer_id': identifier, **entry}
        row = {'task_id': name, 'pattern_index': result.get('pattern_index'),
               'configuration': result.get('configuration', name.split('_', 2)[-1]),
               'mode': 'fixed_basis_diagnostic', 'status': task['status'],
               'wall_seconds': task.get('wall_seconds'),
               'constructed_count': result.get('constructed_count'),
               'submitted_count': len(result['submitted_ids']) if 'submitted_ids' in result else None,
               'processed_count': len(result['completed_ids']) if 'completed_ids' in result else None,
               'radius_pass_target_count': result.get('radius_pass_target_count'),
               'public_valid_target_count': result.get('public_valid_target_count'),
               'independently_valid_candidates': len(valid) if valid is not None else None,
               'basis_row_valid_count': sum(c['public_validation']['public_valid'] for c in result.get('basis_row_candidates', [])) if result else None,
               'kernel_calls': counters.get('kernel_calls'),
               'iterations_entered': counters.get('iterations_entered'),
               'iterations_completed': counters.get('iterations_completed'),
               'replacements': counters.get('replacements'), 'successful_reductions': counters.get('redsucc'),
               'babai_lift_calls': counters.get('babai_lift_calls'),
               'extra_diagnostic_lift_calls': counters.get('diagnostic_additional_babai_lift_calls'),
               'minimum_full_norm_sq': (minima.get('babai') or minima.get('post') or {}).get('full_norm_sq'),
               'minimum_pre_full_norm_sq': minima.get('pre', {}).get('full_norm_sq'),
               'peak_rss_bytes': task.get('supervision', {}).get('memory', {}).get('value'),
               'cleanup_verified': task.get('cleanup_verified'), 'deadline_met': task.get('deadline_met'),
               'exit_reason': result.get('stop', {}).get('exit_reason', task.get('reason'))}
        rows.append(row)
        details[name] = {'summary': row, 'minima_with_entry_identity': minima,
                         'timings': result.get('timings'), 'stop': result.get('stop'),
                         'pairing': result.get('pairing'), 'target_summaries': summaries,
                         'candidates': result.get('public_valid_candidates'),
                         'not_run_reason': task.get('reason')}
    document = {'schema': 'ntru-stage3-actual-results-v1', 'mode': 'fixed_basis_diagnostic',
                'costs_do_not_include_reduction_as_decoder_success': True,
                'index2_scope': 'posthoc_known_coverage_exploratory_diagnostic',
                'configurations': rows, 'details': details, 'all_tasks': records,
                'starting_state_pairing': validation.get('checks', {}).get('starting_state_pairing')}
    write(stage / 'actual_results.json', document)
    with (stage / 'actual_results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else ['task_id'])
        writer.writeheader()
        writer.writerows(rows)
    manifests = {str(i): read(stage / f'pattern_{i}/basis_manifest.json') for i in (0, 2)}
    write(stage / 'basis_manifest.json', {'mode': 'fixed_basis_diagnostic', 'patterns': manifests,
          'standalone_end_to_end_time_inference_allowed': False})
    return document
