"""V2 policy regressions and a bounded traced pinned normal BDGL import smoke."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest

from .access_v2 import ALLOWED_MODULE_PATHS, REPO, access_check, classify_path


class ContentPolicy(unittest.TestCase):
    def test_exact_numpy_private_modules_are_allowed(self):
        for name in ALLOWED_MODULE_PATHS:
            record = classify_path(name)
            self.assertFalse(record['denied'], record)
            self.assertEqual(record['allowed_module'], name)

    def test_secret_arrays_and_repository_content_are_denied(self):
        for name in (REPO / 'private/example.json', REPO / 'truth/sample.dat',
                     REPO / 'reference/anchor.py', '/tmp/one.truth.json',
                     '/tmp/ref_f.npy', '/tmp/reference_arrays.json', '/tmp/private/coefficients.npz'):
            self.assertTrue(classify_path(name)['denied'], str(name))

    def test_symlink_resolution_cannot_bypass_array_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            secret = path / 'private' / 'coefficients.npy'
            secret.parent.mkdir()
            secret.write_bytes(b'synthetic test only')
            alias = path / 'public.dat'
            alias.symlink_to(secret)
            self.assertTrue(classify_path(alias)['denied'])
            self.assertEqual(classify_path(alias)['realpath'], str(secret))

    def test_native_parser_uses_same_policy_and_records_module(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'file_access.jsonl').write_text('')
            module = sorted(ALLOWED_MODULE_PATHS)[0]
            (output / 'file_opens.log').write_text(f'123 openat(AT_FDCWD, "{module}", O_RDONLY) = 3\n')
            result = access_check(output)
            self.assertTrue(result['all_checks_passed'], result)
            self.assertEqual(result['allowed_module_paths'][0]['realpath'], module)
            (output / 'file_opens.log').write_text('123 openat(AT_FDCWD, "/tmp/ref_f.npy", O_RDONLY) = 3\n')
            self.assertFalse(access_check(output)['all_checks_passed'])

    def test_truncated_native_path_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'file_access.jsonl').write_text('')
            (output / 'file_opens.log').write_text('123 openat(AT_FDCWD, "/tmp/long_path"..., O_RDONLY) = 3\n')
            self.assertFalse(access_check(output)['all_checks_passed'])


def smoke(output):
    """Existing public d28 fixture, normal BDGL only; no reduction or decoder."""
    import numpy.testing._private.utils
    import g6k.siever
    import g6k.slicer
    from experiments.ntru_hybrid_comparison.inputs import load_input_bundle, create_working_copy
    from experiments.ntru_hybrid_comparison.fixed_backend import FixedSlicerBackend
    bundle = load_input_bundle(REPO / 'experiments/ntru_hybrid_comparison/phase2c_results_20261026/micro_n15_d28.public_input.json', require_reduced_verified=True)
    assert bundle['dimensions']['d'] == 28 and bundle['public_instance']['n'] == 15
    backend = FixedSlicerBackend(create_working_copy(bundle), {
        'float_type': 'mpfr', 'precision_bits': 192, 'sieve_dim': 28, 'seed': 10001,
        'db_size': 128, 'nrand': 2, 'max_iterations': 1, 'preprocessing': 'bdgl2',
        'buckets': 4, 'blocks': 1, 'multi_hash': 1,
        'interface_test_mode': False, 'force_kernel_iterations': False})
    backend.preprocess()
    result = {'status': 'passed', 'normal_bdgl_preprocess_count': backend.metrics['preprocess_count'],
              'interface_test_mode': backend.config.interface_test_mode,
              'sample_database_only': False, 'main_n128_jobs': 0, 'reduction_calls': 0,
              'lattice_db_size': len(backend.siever), 'dimension': 28,
              'g6k_siever_path': g6k.siever.__file__, 'g6k_slicer_path': g6k.slicer.__file__}
    (Path(output) / 'smoke_result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


def guard_denial(output, fixture):
    attempts = []
    for path in (Path(fixture), Path(fixture).parent.parent / 'alias.dat'):
        try:
            with path.open('rb') as stream:
                stream.read(1)
        except PermissionError:
            attempts.append({'path': str(path), 'denied_before_content_read': True})
        else:
            raise AssertionError('synthetic private array read was not denied')
    result = {'status': 'passed', 'denied_count': len(attempts), 'attempts': attempts,
              'fixture_kind': 'synthetic_guard_test_only_no_real_reference_arrays'}
    (Path(output) / 'guard_denial_result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke-output')
    parser.add_argument('--guard-denial-fixture')
    args = parser.parse_args()
    if args.guard_denial_fixture:
        guard_denial(args.smoke_output, args.guard_denial_fixture)
    elif args.smoke_output:
        smoke(args.smoke_output)
    else:
        unittest.main(argv=['test_access_v2'])
