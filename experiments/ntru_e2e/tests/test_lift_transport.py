"""Real, small MPFR GSO checks for global projected-residual lifting.

These fixtures call neither a reducing decoder constructor nor G6K. The small
dimensions deliberately isolate GSO transport from lattice reduction and sieving.
"""

import math

import pytest

from experiments.ntru_e2e.slicer_adapter import SlicerDecoder, _multiply_left


fpylll = pytest.importorskip("fpylll", reason="real GSO transport checks require the pinned runtime")


def mapper(rows, sieve_dim, *, gh_sub=25.0):
    fpylll.FPLLL.set_precision(192)
    matrix = fpylll.IntegerMatrix.from_matrix(rows, int_type="mpz")
    gso = fpylll.GSO.Mat(matrix, float_type="mpfr")
    gso.update_gso()
    decoder = SlicerDecoder.__new__(SlicerDecoder)
    decoder.rows, decoder.G = rows, gso
    decoder.dim, decoder.sieve_dim = len(rows), sieve_dim
    decoder.gh_sub = gh_sub
    decoder._sqrt_r_over_gh = [math.sqrt(float(r) / gh_sub) for r in gso.r()[-sieve_dim:]]
    decoder._sqrt_gh_over_r = [1.0 / value for value in decoder._sqrt_r_over_gh]
    return decoder


def direct_babai(decoder, target):
    coefficients = [int(value) for value in decoder.G.babai(target)]
    vector = _multiply_left(decoder.rows, coefficients)
    return coefficients, vector, sum((a - b) ** 2 for a, b in zip(target, vector))


def test_skew_two_dimensional_counterexample_uses_global_orthogonal_tail():
    decoder = mapper([[10, 0], [1000, 10]], 1)
    target = [0, 4]
    normalized = decoder._project_reduced(target)
    assert normalized == pytest.approx([0.8])

    # Fixed fplll's nonzero-start conversion selects the original subbasis row,
    # not the global orthogonal tail. This was the old production lift.
    local_residual = decoder.G.to_canonical([0.4], start=1)
    assert local_residual == pytest.approx([400.0, 4.0])
    old_coefficients = [int(x) for x in decoder.G.babai(
        [t - r for t, r in zip(target, local_residual)])]
    old_vector = _multiply_left(decoder.rows, old_coefficients)
    assert old_vector == [-400, 0]
    assert sum((t - v) ** 2 for t, v in zip(target, old_vector)) == 160016

    coefficients, vector, distance_sq = decoder._lift(target, normalized)
    assert (coefficients, vector, distance_sq) == ([0, 0], [0, 0], 16)
    assert (coefficients, vector, distance_sq) == direct_babai(decoder, target)


def test_lift_transport_preserves_zero_global_prefix_and_roundtrips_tail():
    rows = [[7, 1, 0, 0], [3, 6, 1, 0], [-5, 4, 5, 1], [8, -9, 2, 4]]
    decoder = mapper(rows, 2, gh_sub=3.0)
    gso = decoder.G
    tail_coordinates = [0.125, -0.25]
    normalized = [value * scale for value, scale in zip(tail_coordinates, decoder._sqrt_r_over_gh)]
    calls = []

    class ObservedGSO:
        def to_canonical(self, coordinates, start=0):
            residual = gso.to_canonical(coordinates, start=start)
            calls.append((list(coordinates), start, residual))
            return residual

        def babai(self, target):
            return gso.babai(target)

    decoder.G = ObservedGSO()
    decoder._lift([1, -2, 3, 4], normalized)
    coordinates, start, residual = calls[0]
    assert start == 0
    assert coordinates == pytest.approx([0.0, 0.0, *tail_coordinates], abs=1e-14)
    recovered = gso.from_canonical(residual)
    assert recovered[:2] == pytest.approx([0.0, 0.0], abs=1e-12)
    assert recovered[2:] == pytest.approx(tail_coordinates, abs=1e-12)
    # Orthogonality is an independent ambient-space condition, not merely an
    # assertion about which arguments the implementation passed to fplll.
    for prefix_row in rows[:2]:
        assert sum(a * b for a, b in zip(prefix_row, residual)) == pytest.approx(0.0, abs=1e-11)


@pytest.mark.parametrize("storage", ["float64", "float32_grouped"])
@pytest.mark.parametrize("rows,target,sieve_dim", [
    ([[10, 0], [1000, 10]], [13, 26], 1),
    ([[11, 0, 0, 0, 0], [3, 13, 0, 0, 0], [-2, 4, 17, 0, 0],
      [5, -3, 2, 19, 0], [-4, 6, -5, 7, 23]], [13, -7, 9, 46, -31], 2),
])
def test_original_nonzero_projection_reproduces_direct_babai_with_scaling_and_signs(
        rows, target, sieve_dim, storage):
    decoder = mapper(rows, sieve_dim, gh_sub=13.0)
    targets = [target, [-value for value in target]]
    expected = {identifier: direct_babai(decoder, value) for identifier, value in enumerate(targets)}
    assert any(expected[0][0][-sieve_dim:]), "fixture must have a nonzero tail lattice shift"

    if storage == "float64":
        lifted = {identifier: decoder._lift(value, decoder._project_reduced(value))
                  for identifier, value in enumerate(targets)}
    else:
        groups = decoder._groups(targets, [100000] * 2, [0, 1])
        assert len(groups) == 1
        assert {member["sign"] for member in groups[0]["members"]} == {-1, 1}
        lifted = {member["target_id"]: decoder._lift(
            member["target"], [member["sign"] * value for value in group["projection"]])
            for group in groups for member in group["members"]}

    assert lifted == expected
    assert lifted[1][1] == [-value for value in lifted[0][1]]
    assert lifted[0][2] == lifted[1][2]


def test_lift_with_no_prefix_keeps_full_projection_behavior():
    decoder = mapper([[11, 1], [4, 9]], 2, gh_sub=7.0)
    target = [25, -19]
    assert decoder._lift(target, decoder._project_reduced(target)) == direct_babai(decoder, target)
