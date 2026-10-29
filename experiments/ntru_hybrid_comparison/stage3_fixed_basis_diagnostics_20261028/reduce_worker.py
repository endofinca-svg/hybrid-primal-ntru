"""Exactly one original schedule attempt; immutable public input only."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import time
import traceback

from experiments.ntru_e2e.algebra import reconstruct
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.inputs import (
    attach_reduction, build_input_bundle, content_sha256, load_input_bundle, save_input_bundle,
)
from experiments.ntru_hybrid_comparison.patterns import select_pattern
from experiments.ntru_hybrid_comparison.reduction import protocol_from_manifest, execute_reduction
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate
from .common import Journal, write

EXPECTED_FILE = '8c03b57a732fb8114d900ff47c2b04f93ca257bef5066db0988e74a10d89baf9'
EXPECTED_PUBLIC = '8e2e7cd27fdbd595d58a4baefb5326a4b04d05d0804962395ab9f74b26146bc7'
EXPECTED_BASIS_0 = 'd883f3f8157c213be9de4ff1eabb45b4b3da76ded23859567199f0750f42ded9'


def run(request, output):
    journal = Journal(output, request)
    result, output = journal.result, Path(output)
    try:
        with journal.stage('input_and_basis_construction'):
            raw = Path(request['public_path']).read_bytes()
            if sha256(raw).hexdigest() != EXPECTED_FILE:
                raise ValueError('public file hash mismatch')
            public = json.loads(raw)
            index = request['pattern_index']
            if index not in (0, 2):
                raise ValueError('only pattern 0 and 2 permitted')
            P = select_pattern(128, 40, 10001, index, 'historical_sha256_shuffle_v1')
            remaining = [i for i in range(128) if i not in P]
            T = remaining[-4:]
            C = remaining[:-4]
            if T != {0: [121, 124, 125, 126], 2: [123, 124, 126, 127]}[index]:
                raise ValueError('historical tail coordinates mismatch')
            protocol = protocol_from_manifest(request['reduction_protocol'])
            bundle = build_input_bundle(public, P, C, T, CandidatePolicy(mode='full_nonzero'),
                        reduction_protocol=protocol, target_representation='centered_mod_q',
                        provenance={'kind': 'public_input_build_only',
                          'source_public_path': request['public_path'],
                          'source_public_file_sha256': EXPECTED_FILE})
            if bundle['public_content_sha256'] != EXPECTED_PUBLIC:
                raise ValueError('public content hash mismatch')
            write(output / 'original.basis.json', bundle['basis']['original'])
            write(output / 'coordinates.json', bundle['coordinates'])
            save_input_bundle(output / 'original.input.json', bundle)
            result['original_sha256'] = bundle['basis']['original_sha256']
            result['public_file_sha256'] = EXPECTED_FILE
            result['public_sha256'] = EXPECTED_PUBLIC
            result['target_count'] = len(bundle['targets'])
        def progress(payload):
            journal.emit('reduction_progress', **payload)
            result['reduction_progress'] = payload
            journal.save()
        with journal.stage('lll_bkz_and_exact_certificate'):
            execution = execute_reduction(bundle['basis']['original'], protocol, event_sink=progress)
            write(output / 'reduction.json', execution['record'])
            if execution['reduced_basis'] is not None:
                write(output / 'reduced.basis.json', execution['reduced_basis'])
                write(output / 'transform.json', execution['transform'])
            if execution['record']['status'] != 'completed' or execution['lattice_status'] != 'reduced_verified':
                raise ValueError('complete schedule and lattice certificate required')
        with journal.stage('attach_save_reload_and_digest'):
            bundle = attach_reduction(bundle, execution)
            save_input_bundle(output / 'input.json', bundle)
            reloaded = load_input_bundle(output / 'input.json', require_reduced_verified=True)
            digest = content_sha256(reloaded['basis']['reduced'])
            if digest != content_sha256(execution['reduced_basis']):
                raise ValueError('reload digest mismatch')
            historical_match = digest == EXPECTED_BASIS_0 if index == 0 else None
            manifest = {'mode': 'fixed_basis_diagnostic', 'pattern_index': index,
                'original_sha256': bundle['basis']['original_sha256'], 'reduced_sha256': digest,
                'transform_sha256': content_sha256(bundle['basis']['transform']),
                'serialization': 'UTF-8 JSON nested arrays of exact signed base-10 integers; no floats; row-major',
                'matrix_content_hash': 'sha256(json.dumps(matrix,sort_keys=True,separators=(comma,colon),ensure_ascii=True,allow_nan=False).encode(ascii))',
                'matrix_file_sha256': sha256((output / 'reduced.basis.json').read_bytes()).hexdigest(),
                'historical_expected_sha256': EXPECTED_BASIS_0 if index == 0 else None,
                'historical_digest_matches': historical_match,
                'first_completed_index2_basis': index == 2,
                'reload_digest_matches': True, 'coordinates': bundle['coordinates'],
                'load': 'inputs.load_input_bundle(input.json, require_reduced_verified=True); basis.reduced',
                'proof': bundle['basis']['lattice_verification'],
                'eligible_for_same_basis_diagnostics': index == 2 or historical_match}
            write(output / 'basis_manifest.json', manifest)
            result.update(reduced_sha256=digest, historical_digest_matches=historical_match,
                          eligible_for_same_basis_diagnostics=manifest['eligible_for_same_basis_diagnostics'])
        with journal.stage('basis_row_separate_public_checks'):
            rows, candidates = bundle['basis']['reduced'], []
            norms = [sum(x*x for x in row) for row in rows]
            for i, row in enumerate(rows):
                if norms[i] != 36:
                    continue
                pair = reconstruct(row, [0]*212, P, C, T, [0]*4, 128)
                candidates.append({'source': 'basis_row', 'row_index': i, **pair,
                    'validation': validate_public_candidate(public, pair['f'], pair['g'])})
            basis_rows = {'row_norms_sq': norms, 'candidates': candidates,
                'public_valid_count': sum(c['validation']['public_valid'] for c in candidates),
                'zero_target_babai_called': False, 'does_not_skip_full80_diagnostics': True}
            write(output / 'basis_rows.json', basis_rows)
            result['basis_row_public_valid_count'] = basis_rows['public_valid_count']
        result['status'] = 'completed'
    except BaseException as exc:
        result.update(status='blocked' if isinstance(exc, ValueError) else 'error',
                      error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    result['finished_monotonic'] = time.monotonic()
    result['wall_seconds'] = result['finished_monotonic'] - result['started_monotonic']
    journal.save()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(json.loads(Path(args.request).read_text()), args.output)
    if result['status'] != 'completed':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
