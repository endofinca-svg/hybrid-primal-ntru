"""Replay saved public projection residuals through the repaired lift only.

This entry never constructs a sieve or target database, runs BDGL, iterates a
Slicer kernel, reads a witness, or changes the saved basis.  E is an independent
direct-Babai baseline on the same matrix. All costs are replay costs.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import resource
import tempfile
import time
import traceback

from experiments.ntru_e2e.algebra import reconstruct
from experiments.ntru_e2e.slicer_adapter import SlicerDecoder, _integer, _multiply_left, _residual_sq
from experiments.ntru_hybrid_comparison.fixed_backend import FixedBabaiBackend, _rows
from experiments.ntru_hybrid_comparison.inputs import content_sha256, create_working_copy, load_input_bundle
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
STAGE3 = HERE.parent / 'stage3_fixed_basis_diagnostics_20261028'
CONFIGURATIONS = ('E', 'D-skip', 'D-normal', 'D-probe-1', 'D-probe-400')
REQUIRED = {'configuration', 'input_path', 'source_path', 'work_deadline', 'hard_deadline', 'global_deadline'}
ALLOWED = REQUIRED | {'started_monotonic'}
IDENTITY_FIELDS = ('phase', 'backend_integer_id', 'tail', 'candidate_sha_id', 'target_index',
                   'group_id', 'sign', 'slot_id', 'uid', 'compressed_ordinal',
                   'ancestor_uid', 'ancestor_slot_id', 'randomization_entry_id',
                   'entry_was_randomized', 'kernel_changed_entry', 'projection_residual',
                   'projection_norm_sq_normalized', 'projection_gh_sub')
ORIGINAL_FIELDS = ('coefficients', 'lattice_vector', 'full_pair', 'ambient_residual_sq',
                   'tail_norm_sq', 'full_norm_sq', 'radius_pass', 'public_validation')


def atomic(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_request(request):
    if set(request) - ALLOWED or not REQUIRED <= set(request):
        raise ValueError('unexpected replay fields; witness/anchor/known-tail inputs forbidden')
    configuration = request['configuration']
    if configuration not in CONFIGURATIONS:
        raise ValueError('unknown replay configuration')
    for name in ('work_deadline', 'hard_deadline', 'global_deadline'):
        value = request[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('finite absolute deadlines required')
    if not request['work_deadline'] < request['hard_deadline'] <= request['global_deadline']:
        raise ValueError('work < task hard <= original global hard deadline required')
    if 'started_monotonic' in request:
        started = request['started_monotonic']
        if (isinstance(started, bool) or not isinstance(started, (int, float))
                or not math.isfinite(started) or request['hard_deadline'] - started > 600):
            raise ValueError('single replay task hard limit is 600 seconds including cleanup')
    expected_input = STAGE3 / 'pattern_2/input.json'
    expected_source = STAGE3 / ('decode_p2_' + configuration) / 'target_telemetry.jsonl'
    for field, expected in (('input_path', expected_input), ('source_path', expected_source)):
        actual = Path(request[field])
        if any(word in str(actual).lower() for word in ('private', 'truth', 'secret', 'witness', 'anchor', 'reference')):
            raise ValueError('forbidden replay content path')
        if actual.absolute() != expected.absolute() or actual.resolve() != expected.resolve():
            raise ValueError('only original public pattern2 matrix and matching saved telemetry permitted')


def check_deadline(request):
    deadline = min(request['work_deadline'], request['hard_deadline'] - 5,
                   request['global_deadline'] - 5)
    if time.monotonic() >= deadline:
        raise TimeoutError('original replay work deadline reached; no reset or retry')


def selected_rows(path, configuration):
    phase = 'babai' if configuration == 'E' else 'post'
    with Path(path).open() as stream:
        for line_number, line in enumerate(stream, 1):
            row = json.loads(line)
            if row.get('phase') == phase:
                yield line_number, row


def validate_source_row(row, identities, configuration):
    identifier = row['backend_integer_id']
    if isinstance(identifier, bool) or identifier not in identities:
        raise ValueError('unknown external ID in saved telemetry')
    mapping = identities[identifier]
    for key, expected in (('tail', mapping['u']), ('candidate_sha_id', mapping['candidate_sha_id']),
                          ('target_index', mapping['target_index'])):
        if row.get(key) != expected:
            raise ValueError('saved row/public ID mapping mismatch: ' + key)
    if configuration != 'E':
        residual = row.get('projection_residual')
        if (not isinstance(residual, list) or len(residual) != 40
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not math.isfinite(x) for x in residual)
                or row.get('sign') not in (-1, 1) or isinstance(row.get('sign'), bool)):
            raise ValueError('exact saved 40-dimensional signed residual required')
    return mapping


def make_mapper(bundle):
    # FixedBabaiBackend constructs only MPFR GSO; its constructor never reduces
    # or imports G6K. No backend preprocess/decode method is invoked here.
    backend = FixedBabaiBackend(create_working_copy(bundle), {'float_type': 'mpfr', 'precision_bits': 192})
    from fpylll.util import gaussian_heuristic
    mapper = SlicerDecoder.__new__(SlicerDecoder)
    mapper.rows, mapper.G = backend.rows, backend.gso
    mapper.dim, mapper.sieve_dim = len(backend.rows), 40
    mapper.gh_sub = float(gaussian_heuristic(mapper.G.r()[-40:]))
    if not math.isfinite(mapper.gh_sub) or mapper.gh_sub <= 0:
        raise ValueError('invalid projected GH scale')
    mapper._sqrt_r_over_gh = [math.sqrt(float(value) / mapper.gh_sub) for value in mapper.G.r()[-40:]]
    mapper._sqrt_gh_over_r = [1 / value for value in mapper._sqrt_r_over_gh]
    return backend, mapper


def repair_row(row, bundle, mapping, mapper, configuration):
    target = bundle['targets'][mapping['target_index']]['used']
    if configuration == 'E':
        coefficients = [_integer(value) for value in mapper.G.babai(target)]
        vector = _multiply_left(mapper.rows, coefficients)
        distance = _residual_sq(target, vector)
    else:
        saved_gh = row.get('projection_gh_sub')
        if (not isinstance(saved_gh, (int, float)) or isinstance(saved_gh, bool)
                or not math.isclose(saved_gh, mapper.gh_sub, rel_tol=1e-12, abs_tol=0.0)):
            raise ValueError('saved projection normalization/GH differs from replay GSO')
        residual = [row['sign'] * float(value) for value in row['projection_residual']]
        coefficients, vector, distance = mapper._lift(target, residual)
        coefficients = [_integer(value) for value in coefficients]
        vector = [_integer(value) for value in vector]
    if len(coefficients) != len(mapper.rows) or len(vector) != len(target):
        raise ValueError('repaired coefficient/vector dimension mismatch')
    actual_distance = _residual_sq(target, vector)
    if distance != actual_distance:
        raise ValueError('lift residual does not match exact integer vector')
    coordinates, public = bundle['coordinates'], bundle['public_instance']
    pair = reconstruct(vector, target, coordinates['P'], coordinates['C'], coordinates['T'], mapping['u'], public['n'])
    tail_norm = sum(value * value for value in mapping['u'])
    full_norm = sum(value * value for value in pair['f'] + pair['g'])
    if full_norm != distance + tail_norm:
        raise ValueError('complete norm restoration failed')
    norm = public['distribution']['w_f'] + public['distribution']['w_g']
    return {'coefficients': coefficients, 'lattice_vector': vector, 'full_pair': pair,
            'ambient_residual': [value - origin for value, origin in zip(vector, target)],
            'ambient_residual_sq': distance, 'tail_norm_sq': tail_norm, 'full_norm_sq': full_norm,
            'radius_pass': distance <= norm - tail_norm,
            'public_validation': validate_public_candidate(public, pair['f'], pair['g'])}


def e_consistency(configuration, row, repaired, baseline):
    # Slot layout is used only for D-skip's unchanged post database. The old
    # probe ancestor labels are retained as source data, never interpreted.
    original_entry = (configuration == 'D-skip' and row.get('slot_id') is not None
                      and row['slot_id'] % 16 == 0)
    if configuration != 'E' and not original_entry:
        return None
    original = baseline[row['backend_integer_id']]
    fields = ('coefficients', 'lattice_vector', 'ambient_residual_sq', 'full_norm_sq', 'full_pair')
    checks = {name: repaired[name] == original[name] for name in fields}
    return {'comparison': 'existing_direct_E_same_public_target', 'checks': checks,
            'all_match': all(checks.values()), 'selection': 'E_baseline' if configuration == 'E' else 'Dskip_unchanged_slot_mod16_zero'}


def coverage(source_counts, emitted_counts, expected_rows):
    expected_ids = set(range(80))
    return {'expected_rows': expected_rows, 'source_selected_rows': sum(source_counts.values()),
            'emitted_rows': sum(emitted_counts.values()),
            'source_counts_by_external_id': dict(sorted(source_counts.items())),
            'emitted_counts_by_external_id': dict(sorted(emitted_counts.items())),
            'all_80_source_targets': set(source_counts) == expected_ids,
            'all_80_replayed_targets': set(emitted_counts) == expected_ids,
            'every_saved_selected_record_replayed': (source_counts == emitted_counts
                                                     and sum(emitted_counts.values()) == expected_rows)}


def run(request, output):
    validate_request(request)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'target_telemetry.jsonl').exists():
        raise ValueError('replay output already exists; no implicit rerun or overwrite')
    began = time.monotonic()
    configuration = request['configuration']
    expected = 80 if configuration == 'E' else 1280
    result = {'schema': 'ntru-stage4-saved-projection-replay-v1', 'status': 'running',
              'mode': 'saved_projection_replay', 'configuration': configuration, 'pattern_index': 2,
              'method': 'direct_babai_baseline' if configuration == 'E' else 'repaired_lift_of_saved_post_projection',
              'request': request, 'started_monotonic': began, 'expected_rows': expected,
              'emitted_rows': 0, 'completed_ids': [], 'timings': {'input_load_and_source_check': None},
              'current_stage': 'input_load_and_source_check',
              'bdgl_calls': 0, 'kernel_iterations': 0, 'lll_bkz_calls': 0,
              'cost_scope': 'saved state replay only; not a new complete D execution or end-to-end result',
              'witness_anchor_correct_tail_labels_used': False}
    atomic(output / 'result.json', result)
    emitted_counts, source_counts = Counter(), Counter()
    summaries = {}
    equality_count, equality_matches = 0, 0
    stream = None
    try:
        check_deadline(request)
        tick = time.monotonic()
        bundle = load_input_bundle(request['input_path'], require_reduced_verified=True)
        if bundle['dimensions']['n'] != 128 or bundle['dimensions']['d'] != 212 or bundle['coordinates']['T'] != [123, 124, 126, 127]:
            raise ValueError('only saved public index2 main basis is permitted')
        identities = {row['backend_integer_id']: row for row in bundle['id_mapping']}
        if set(identities) != set(range(80)) or any(not any(row['u']) for row in identities.values()):
            raise ValueError('original full80 external mapping required')
        baseline = {}
        baseline_path = STAGE3 / 'decode_p2_E/target_telemetry.jsonl'
        for _, row in selected_rows(baseline_path, 'E'):
            validate_source_row(row, identities, 'E')
            if row['backend_integer_id'] in baseline:
                raise ValueError('nonunique existing E baseline ID')
            baseline[row['backend_integer_id']] = row
        if set(baseline) != set(identities):
            raise ValueError('existing E baseline does not cover all public targets')
        for _, row in selected_rows(request['source_path'], configuration):
            check_deadline(request)
            validate_source_row(row, identities, configuration)
            source_counts[row['backend_integer_id']] += 1
        if sum(source_counts.values()) != expected or set(source_counts) != set(identities):
            raise ValueError('saved selected source rows/full80 coverage mismatch')
        result['input_digests'] = {
            'basis_sha256': bundle['basis']['reduced_sha256'], 'public_sha256': bundle['public_content_sha256'],
            'id_mapping_sha256': bundle['id_mapping_sha256'], 'ordered_targets_sha256': bundle['ordered_targets_sha256'],
            'source_file_sha256': sha256(Path(request['source_path']).read_bytes()).hexdigest(),
            'input_file_sha256': sha256(Path(request['input_path']).read_bytes()).hexdigest(),
            'existing_E_file_sha256': sha256(baseline_path.read_bytes()).hexdigest()}
        result['timings']['input_load_and_source_check'] = time.monotonic() - tick
        result['current_stage'] = 'gso_setup'
        result['timings']['gso_setup'] = None
        atomic(output / 'result.json', result)
        check_deadline(request)
        tick = time.monotonic()
        backend, mapper = make_mapper(bundle)
        result['timings']['gso_setup'] = time.monotonic() - tick
        result['gh_sub'] = mapper.gh_sub
        result['gso'] = backend.metadata['gso']
        result['current_stage'] = 'saved_projection_lifting_and_public_validation'
        result['timings'][result['current_stage']] = None
        atomic(output / 'result.json', result)
        stream = (output / 'target_telemetry.jsonl').open('x')
        tick = time.monotonic()
        for line_number, source in selected_rows(request['source_path'], configuration):
            check_deadline(request)
            mapping = validate_source_row(source, identities, configuration)
            lift_tick = time.monotonic()
            repaired = repair_row(source, bundle, mapping, mapper, configuration)
            consistency = e_consistency(configuration, source, repaired, baseline)
            row = {name: source.get(name) for name in IDENTITY_FIELDS}
            row.update(mode='saved_projection_replay', configuration=configuration,
                       source_line_number=line_number, source_row_sha256=content_sha256(source),
                       original={name: source[name] for name in ORIGINAL_FIELDS}, repaired=repaired,
                       replay_seconds=time.monotonic() - lift_tick,
                       existing_E_consistency=consistency,
                       source_ancestor_labels_interpreted=False)
            row['changes'] = {
                'coefficients_changed': source['coefficients'] != repaired['coefficients'],
                'lattice_vector_changed': source['lattice_vector'] != repaired['lattice_vector'],
                'ambient_residual_sq_delta': repaired['ambient_residual_sq'] - source['ambient_residual_sq'],
                'full_norm_sq_delta': repaired['full_norm_sq'] - source['full_norm_sq'],
                'public_valid_before': source['public_validation']['public_valid'],
                'public_valid_after': repaired['public_validation']['public_valid']}
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
            identifier = source['backend_integer_id']
            emitted_counts[identifier] += 1
            summary = summaries.setdefault(identifier, {'backend_integer_id': identifier, 'tail': mapping['u'],
                'rows': 0, 'original_min_full_norm_sq': None, 'repaired_min_full_norm_sq': None,
                'radius_pass_count': 0, 'public_valid_count': 0, 'improved_rows': 0,
                'existing_E_compared_rows': 0, 'existing_E_matching_rows': 0})
            summary['rows'] += 1
            for key, value in (('original_min_full_norm_sq', source['full_norm_sq']),
                               ('repaired_min_full_norm_sq', repaired['full_norm_sq'])):
                if summary[key] is None or value < summary[key]:
                    summary[key] = value
                    summary[key + '_source_line'] = line_number
            summary['radius_pass_count'] += int(repaired['radius_pass'])
            summary['public_valid_count'] += int(repaired['public_validation']['public_valid'])
            summary['improved_rows'] += int(repaired['full_norm_sq'] < source['full_norm_sq'])
            if consistency is not None:
                equality_count += 1
                equality_matches += int(consistency['all_match'])
                summary['existing_E_compared_rows'] += 1
                summary['existing_E_matching_rows'] += int(consistency['all_match'])
            result['emitted_rows'] += 1
            if result['emitted_rows'] % 16 == 0:
                stream.flush()
                os.fsync(stream.fileno())
                result['completed_ids'] = sorted(identifier for identifier, count in emitted_counts.items()
                                                  if count == source_counts[identifier])
                result['coverage'] = coverage(source_counts, emitted_counts, expected)
                result['target_summaries'] = [summaries[i] for i in sorted(summaries)]
                atomic(output / 'result.json', result)
        stream.flush()
        os.fsync(stream.fileno())
        result['timings']['saved_projection_lifting_and_public_validation'] = time.monotonic() - tick
        check_deadline(request)
        if _rows(backend.gso) != bundle['basis']['reduced']:
            raise ValueError('saved integer basis changed during replay')
        result['coverage'] = coverage(source_counts, emitted_counts, expected)
        if not all(result['coverage'][name] for name in ('all_80_source_targets', 'all_80_replayed_targets', 'every_saved_selected_record_replayed')):
            raise ValueError('complete source record/full80 replay coverage failed')
        result['status'] = 'completed'
        result['current_stage'] = None
        result['basis_unchanged'] = True
    except Exception as error:
        result['status'] = 'timeout' if isinstance(error, TimeoutError) else 'error'
        result['error'] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
    finally:
        if stream is not None:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        result['coverage'] = coverage(source_counts, emitted_counts, expected)
        result['completed_ids'] = sorted(identifier for identifier, count in emitted_counts.items()
                                          if count == source_counts[identifier])
        result['target_summaries'] = [summaries[i] for i in sorted(summaries)]
        result['existing_E_consistency'] = {'compared_rows': equality_count, 'matching_rows': equality_matches,
                                           'all_compared_match': equality_matches == equality_count if equality_count else None}
        result['public_valid_row_count'] = sum(row['public_valid_count'] for row in summaries.values())
        result['public_valid_external_ids'] = [i for i in sorted(summaries) if summaries[i]['public_valid_count']]
        result['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        result['finished_monotonic'] = time.monotonic()
        result['wall_seconds'] = result['finished_monotonic'] - began
        atomic(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(json.loads(Path(args.request).read_text()), args.output)
    print(json.dumps({'configuration': result['configuration'], 'status': result['status'],
                      'emitted_rows': result['emitted_rows'], 'mode': result['mode']}))
    if result['status'] != 'completed':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
