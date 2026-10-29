"""Small public fixtures and mocked lifting; no main-instance GSO calls."""
from copy import deepcopy
from types import SimpleNamespace
import time

import pytest

from .ideal_projection_check import (STAGE3, checked_witness, lift_case,
                                     prefix_eta_record, validate_request)


def small_fixture(sign=1):
    pair = {'f': [0, sign, 0], 'g': [0, sign, 0]}
    target = [0, -sign, 0, 0]
    mapping = {'backend_integer_id': 7, 'target_index': 0, 'u': [sign]}
    public = {'schema': 'ntru-e2e-public-v1', 'n': 3, 'q': 97, 'ring': 'x^n+1',
              'h': [1, 0, 0],
              'distribution': {'kind': 'fixed_weight_ternary', 'w_f': 1, 'w_g': 1}}
    bundle = {'public_instance': public, 'coordinates': {'P': [2], 'C': [0], 'T': [1]},
              'id_mapping': [mapping], 'targets': [{'used': target}]}
    representative = {'norm_record': {'pair': pair}, 'tail': [sign], 'lattice_vector': [0] * 4,
                      'target_used': target, 'negacyclic_power': 1}
    return bundle, representative


@pytest.mark.parametrize('sign', [-1, 1])
@pytest.mark.parametrize('precision', ['float64', 'float32'])
def test_ideal_input_is_negative_witness_error_and_does_not_count_as_search(sign, precision):
    bundle, representative = small_fixture(sign)
    expected = checked_witness(bundle, representative)
    calls = []

    def lift(target, normalized):
        calls.append((target, normalized))
        return [0] * 4, [0] * 4, 1

    mapper = SimpleNamespace(dim=4, sieve_dim=1, _sqrt_r_over_gh=[2.0], _lift=lift)
    # Supplied GSO coordinates are a small mock; their sign orientation is
    # independent of the exact integer reconstruction tested below.
    e_gso = [0.1, -0.2, 0.3, sign * 0.4]
    case = lift_case(mapper, bundle, expected, e_gso, precision)
    assert calls[0][0] == expected['target']
    assert calls[0][1] == pytest.approx([-sign * 0.8], abs=1e-7)
    if precision == 'float32':
        assert calls[0][1] != [-sign * 0.8]
    assert case['ideal_ambient_residual_t_minus_v'] == [0, -sign, 0, 0]
    assert case['matches_expected_lattice_vector']
    assert case['matches_witness_pair']
    assert case['lift']['public_validation']['public_valid']
    assert case['lift']['full_norm_sq'] == 2
    assert case['lift']['ambient_residual_sq'] == case['lift']['tail_norm_sq'] == 1
    assert case['counts_as_search_success'] is False
    assert case['algorithm_recovery_success'] is False


def test_prefix_margin_is_observed_without_becoming_a_gate():
    result = prefix_eta_record([0.1, -0.5, 0.6, 9.0], 3)
    assert result['values'] == [0.1, -0.5, 0.6]
    assert result['max_abs'] == 0.6
    assert result['minimum_signed_half_margin'] == pytest.approx(-0.1)
    assert result['all_strictly_inside_half'] is False
    assert result['numerical_only'] is True


def test_published_witness_is_revalidated_and_target_mapping_checked():
    bundle, representative = small_fixture()
    bad = deepcopy(representative)
    bad['norm_record']['pair']['g'][0] = 1
    with pytest.raises(ValueError, match='fresh public validation'):
        checked_witness(bundle, bad)
    bad = deepcopy(representative)
    bad['target_used'][1] = 17
    with pytest.raises(ValueError, match='target/lattice-vector'):
        checked_witness(bundle, bad)


def test_request_only_accepts_fixed_public_inputs_and_original_deadlines():
    tick = time.monotonic()
    request = {'input_path': str(STAGE3 / 'pattern_2/input.json'), 'stage3_path': str(STAGE3),
               'started_monotonic': tick, 'work_deadline': tick + 10,
               'hard_deadline': tick + 15, 'global_deadline': tick + 30}
    validate_request(request)
    with pytest.raises(ValueError, match='unexpected'):
        validate_request({**request, 'known_vector': [1, 0]})
    with pytest.raises(ValueError, match='deadline ordering'):
        validate_request({**request, 'hard_deadline': tick + 31})
    with pytest.raises(ValueError, match='saved public pattern2'):
        validate_request({**request, 'input_path': str(STAGE3 / 'pattern_0/input.json')})
