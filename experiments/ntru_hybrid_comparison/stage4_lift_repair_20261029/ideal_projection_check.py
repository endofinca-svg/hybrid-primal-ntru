"""Lift the two previously published witnesses' ideal projections, separately.

This process alone reads known public representatives. Nothing is submitted to a
search decoder; recovering a supplied witness is never counted as search success.
"""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import resource
import struct
import time
import traceback

from experiments.ntru_e2e.algebra import reconstruct
from experiments.ntru_hybrid_comparison.inputs import load_input_bundle
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate
from .replay_fixed_basis import atomic, make_mapper


HERE = Path(__file__).resolve().parent
STAGE3 = HERE.parent / 'stage3_fixed_basis_diagnostics_20261028'
REQUIRED = {'input_path', 'stage3_path', 'work_deadline', 'hard_deadline', 'global_deadline'}
ALLOWED = REQUIRED | {'started_monotonic'}


def validate_request(request):
    if set(request) - ALLOWED or not REQUIRED <= set(request):
        raise ValueError('unexpected ideal-check request fields')
    for name in ('work_deadline', 'hard_deadline', 'global_deadline'):
        value = request[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('finite inherited absolute deadlines required')
    if not request['work_deadline'] < request['hard_deadline'] <= request['global_deadline']:
        raise ValueError('inherited deadline ordering failed')
    for name, expected in (('input_path', STAGE3 / 'pattern_2/input.json'), ('stage3_path', STAGE3)):
        actual = Path(request[name])
        if any(word in (str(actual) + ' ' + str(actual.resolve())).lower()
               for word in ('private', 'truth', 'secret', 'reference')):
            raise ValueError('forbidden content path')
        if actual.absolute() != expected.absolute() or actual.resolve() != expected.resolve():
            raise ValueError('only the saved public pattern2 artifacts are permitted')


def check_deadline(request):
    if time.monotonic() >= min(request['work_deadline'], request['hard_deadline'] - 5,
                               request['global_deadline'] - 5):
        raise TimeoutError('inherited ideal-check deadline reached')


def prefix_eta_record(e_gso, prefix_dimension):
    values = [float(value) for value in e_gso[:prefix_dimension]]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('nonfinite witness GSO coordinates')
    margins = [0.5 - abs(value) for value in values]
    return {'values': values, 'max_abs': max(map(abs, values), default=0.0),
            'signed_half_margins': margins,
            'minimum_signed_half_margin': min(margins) if margins else None,
            'all_strictly_inside_half': all(value > 0 for value in margins),
            'numerical_only': True,
            'numeric_scope': 'MPFR192 GSO exported through the float64 Python interface; no exact or interval certificate',
            'definition': 'eta_i = (v - t) in global GSO coordinates, i < dim - sieve_dim'}


def checked_witness(bundle, representative):
    public, coordinates = bundle['public_instance'], bundle['coordinates']
    pair = representative['norm_record']['pair']
    validation = validate_public_candidate(public, pair['f'], pair['g'])
    if not validation['public_valid']:
        raise ValueError('published representative failed fresh public validation')
    if any(pair['f'][position] for position in coordinates['P']):
        raise ValueError('published representative violates the zero pattern')
    tail = [pair['f'][position] for position in coordinates['T']]
    matches = [mapping for mapping in bundle['id_mapping'] if mapping['u'] == tail]
    if not any(tail) or len(matches) != 1 or representative['tail'] != tail:
        raise ValueError('published representative does not match one original nonzero target')
    mapping = matches[0]
    target = bundle['targets'][mapping['target_index']]['used']
    vector = [g + t for g, t in zip(pair['g'], target[:public['n']])]
    vector += [pair['f'][position] for position in coordinates['C']]
    if vector != representative['lattice_vector'] or target != representative['target_used']:
        raise ValueError('published witness target/lattice-vector reconstruction mismatch')
    recovered = reconstruct(vector, target, coordinates['P'], coordinates['C'],
                            coordinates['T'], tail, public['n'])
    if recovered != pair:
        raise ValueError('published witness does not reconstruct its public pair')
    return {'mapping': mapping, 'pair': pair, 'validation': validation,
            'target': target, 'vector': vector,
            'e_v_minus_t': [v - t for v, t in zip(vector, target)],
            'negacyclic_power': representative['negacyclic_power']}


def lift_case(mapper, bundle, expected, e_gso, precision):
    start = mapper.dim - mapper.sieve_dim
    # The saved witness convention is e=v-t, whereas _lift consumes r=t-v.
    normalized = [-float(value) * scale for value, scale in
                  zip(e_gso[start:], mapper._sqrt_r_over_gh)]
    if precision == 'float32':
        normalized = [struct.unpack('f', struct.pack('f', value))[0] for value in normalized]
    elif precision != 'float64':
        raise ValueError('unknown projected interface precision')
    if len(normalized) != mapper.sieve_dim or not all(math.isfinite(x) for x in normalized):
        raise ValueError('invalid ideal normalized residual')
    coefficients, vector, ambient = mapper._lift(expected['target'], normalized)
    coefficients, vector = [int(x) for x in coefficients], [int(x) for x in vector]
    coordinates, public = bundle['coordinates'], bundle['public_instance']
    pair = reconstruct(vector, expected['target'], coordinates['P'], coordinates['C'],
                       coordinates['T'], expected['mapping']['u'], public['n'])
    exact_ambient = sum((v - t) ** 2 for v, t in zip(vector, expected['target']))
    tail_norm = sum(x * x for x in expected['mapping']['u'])
    full_norm = sum(x * x for x in pair['f'] + pair['g'])
    if ambient != exact_ambient or full_norm != ambient + tail_norm:
        raise ValueError('ideal-check exact norm reconstruction failed')
    validation = validate_public_candidate(public, pair['f'], pair['g'])
    return {'external_id': expected['mapping']['backend_integer_id'],
            'negacyclic_power': expected['negacyclic_power'], 'precision': precision,
            'expected_pair': expected['pair'], 'expected_lattice_vector': expected['vector'],
            'expected_public_validation': expected['validation'], 'target': expected['target'],
            'tail': expected['mapping']['u'],
            'ideal_ambient_residual_t_minus_v': [-value for value in expected['e_v_minus_t']],
            'eta_prefix': prefix_eta_record(e_gso, start),
            'normalized_residual_input': normalized,
            'sign_convention': 'witness e=v-t; supplied ideal residual r=t-v=-e; no signed-group canonicalization',
            'lift': {'coefficients': coefficients, 'lattice_vector': vector, 'full_pair': pair,
                     'ambient_residual_sq': ambient, 'tail_norm_sq': tail_norm,
                     'full_norm_sq': full_norm, 'public_validation': validation},
            'matches_expected_lattice_vector': vector == expected['vector'],
            'matches_witness_pair': pair == expected['pair'],
            'algorithm_recovery_success': False, 'counts_as_search_success': False}


def run(request, output):
    validate_request(request)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'result.json').exists():
        raise ValueError('ideal-check output exists; no implicit retry')
    began = time.monotonic()
    result = {'schema': 'ntru-stage4-ideal-projection-check-v1', 'status': 'running',
              'mode': 'ideal_projection_check', 'request': request, 'started_monotonic': began,
              'known_public_witness_used': True, 'witness_passed_to_replay': False,
              'algorithm_recovery_success': False, 'counts_as_search_success': False,
              'new_bkz_bdgl_or_kernel_calls': 0, 'cases': [], 'timings': {},
              'current_stage': 'public_input_and_published_witness'}
    atomic(output / 'result.json', result)
    try:
        check_deadline(request)
        tick = time.monotonic()
        bundle = load_input_bundle(request['input_path'], require_reduced_verified=True)
        if (bundle['dimensions']['n'] != 128 or bundle['dimensions']['d'] != 212
                or bundle['coordinates']['T'] != [123, 124, 126, 127]):
            raise ValueError('unexpected saved public pattern2 input')
        witness_path = STAGE3 / 'witness_consistency.json'
        published = json.loads(witness_path.read_text())
        patterns = [row for row in published['patterns'] if row['pattern_index'] == 2]
        if len(patterns) != 1:
            raise ValueError('published pattern2 witness record missing or nonunique')
        pattern = patterns[0]
        if (published['public_content_sha256'] != bundle['public_content_sha256']
                or pattern['id_mapping_sha256'] != bundle['id_mapping_sha256']
                or pattern['actual_saved_reduced_basis_sha256'] != bundle['basis']['reduced_sha256']):
            raise ValueError('published witness/public fixed-basis binding mismatch')
        representatives = pattern['compatible_representatives']
        if len(representatives) != 2:
            raise ValueError('exactly the two original compatible representatives required')
        expected = [checked_witness(bundle, row) for row in representatives]
        if len({row['mapping']['backend_integer_id'] for row in expected}) != 2:
            raise ValueError('duplicate witness external target identity')
        result.update(basis_sha256=bundle['basis']['reduced_sha256'],
                      public_sha256=bundle['public_content_sha256'],
                      id_mapping_sha256=bundle['id_mapping_sha256'],
                      witness_source_path=str(witness_path),
                      witness_source_sha256=sha256(witness_path.read_bytes()).hexdigest(),
                      input_file_sha256=sha256(Path(request['input_path']).read_bytes()).hexdigest())
        result['timings']['public_input_and_published_witness'] = time.monotonic() - tick
        result['current_stage'] = 'mpfr192_gso_setup'
        result['timings']['mpfr192_gso_setup'] = None
        atomic(output / 'result.json', result)
        check_deadline(request)
        tick = time.monotonic()
        backend, mapper = make_mapper(bundle)
        result['gh_sub'] = mapper.gh_sub
        result['gso'] = backend.metadata['gso']
        result['timings']['mpfr192_gso_setup'] = time.monotonic() - tick
        result['current_stage'] = 'ideal_projection_lifting'
        result['timings']['ideal_projection_lifting'] = None
        atomic(output / 'result.json', result)
        tick = time.monotonic()
        for witness in expected:
            check_deadline(request)
            e_gso = [float(value) for value in mapper.G.from_canonical(witness['e_v_minus_t'])]
            for precision in ('float64', 'float32'):
                check_deadline(request)
                result['cases'].append(lift_case(mapper, bundle, witness, e_gso, precision))
                atomic(output / 'result.json', result)
        backend._check_basis()
        result['timings']['ideal_projection_lifting'] = time.monotonic() - tick
        result['basis_unchanged'] = True
        result['all_cases_recovered_witness'] = all(row['matches_witness_pair'] and
                                                   row['matches_expected_lattice_vector'] for row in result['cases'])
        result['all_cases_public_valid'] = all(row['lift']['public_validation']['public_valid'] for row in result['cases'])
        result['status'] = 'completed'
        result['current_stage'] = None
    except Exception as error:
        result['status'] = 'timeout' if isinstance(error, TimeoutError) else 'error'
        result['error'] = {'type': type(error).__name__, 'message': str(error),
                           'traceback': traceback.format_exc()}
    finally:
        result['finished_monotonic'] = time.monotonic()
        result['wall_seconds'] = result['finished_monotonic'] - began
        result['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        atomic(output / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(json.loads(Path(args.request).read_text()), args.output)
    print(json.dumps({'status': result['status'], 'cases': len(result['cases']),
                      'all_cases_recovered_witness': result.get('all_cases_recovered_witness'),
                      'counts_as_search_success': False}))
    raise SystemExit(0 if result['status'] == 'completed' else 2)


if __name__ == '__main__':
    main()
