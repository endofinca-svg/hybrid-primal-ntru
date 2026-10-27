"""Reference-only classification and deterministic coverage diagnostics.

This module belongs to offline evaluation.  Online candidate generation and
public validation do not import it.  No reference data is used to choose a
pattern, tail, candidate order, or stopping condition.
"""

from collections.abc import Mapping

from experiments.ntru_e2e.algebra import signed_rotate
from experiments.ntru_hybrid_comparison.candidates import context_from_public
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate


def _checked_reference(public, f, g, constraints):
    result = validate_public_candidate(public, f, g, constraints=constraints)
    if not result["public_valid"]:
        reasons = ", ".join(result["rejection_reasons"])
        raise ValueError(f"reference is not a public-valid short-vector pair: {reasons}")
    return tuple(f), tuple(g)


def _orbit_with_powers(f, g):
    """Joint pairs, exactly deduplicated, in increasing canonical x power."""
    seen = set()
    representatives = []
    for power in range(2 * len(f)):
        pair = (tuple(signed_rotate(f, power)), tuple(signed_rotate(g, power)))
        if pair not in seen:
            seen.add(pair)
            representatives.append((power, pair))
    return tuple(representatives)


def reference_orbit(public, reference_f, reference_g, *, constraints=None):
    """Return distinct simultaneous signed negacyclic (f, g) orbit pairs.

    Each pair consists of two integer tuples.  Powers 0 through 2*n-1 include
    the global sign because x**n == -1.  A repeated pair contributes once.
    A malformed or publicly invalid reference raises ValueError.
    """
    f, g = _checked_reference(public, reference_f, reference_g, constraints)
    return tuple(pair for _, pair in _orbit_with_powers(f, g))


def classify_against_reference(public, f, g, reference_f, reference_g, *, constraints=None):
    """Fresh public validation first; reference classification second.

    Invalid candidates return ``classification='invalid'`` with the public
    rejection reasons and do not require a usable reference.  For valid
    candidates, an invalid reference raises a clear ValueError rather than
    altering the candidate's public-valid decision.  Exact identity takes
    precedence over orbit equivalence.
    """
    result = dict(validate_public_candidate(public, f, g, constraints=constraints))
    result.update(classification="invalid", equivalent_power=None, orbit_size=None)
    if not result["public_valid"]:
        return result
    reference_f, reference_g = _checked_reference(
        public, reference_f, reference_g, constraints)
    pair = (tuple(f), tuple(g))
    orbit = _orbit_with_powers(reference_f, reference_g)
    result["orbit_size"] = len(orbit)
    if pair == (reference_f, reference_g):
        result.update(classification="original", equivalent_power=0)
        return result
    for power, representative in orbit:
        if pair == representative:
            result.update(classification="signed_negacyclic_equivalent", equivalent_power=power)
            return result
    result["classification"] = "other_public_valid"
    return result


def _pattern_positions(values, n):
    try:
        positions = tuple(values)
    except TypeError as exc:
        raise ValueError("pattern_positions must be an iterable of coordinates") from exc
    if any(isinstance(value, bool) or not isinstance(value, int)
           or not 0 <= value < n for value in positions):
        raise ValueError("pattern_positions contains an invalid coordinate")
    if len(set(positions)) != len(positions):
        raise ValueError("pattern_positions contains duplicate coordinates")
    return tuple(sorted(positions))


def _output_pair(output):
    # Any asserted solver success/public_valid flags are intentionally ignored.
    if isinstance(output, Mapping):
        return output.get("f"), output.get("g")
    if isinstance(output, (tuple, list)) and len(output) == 2:
        return output[0], output[1]
    return None, None


def diagnose_coverage(public, reference_f, reference_g, pattern_positions, batch,
                      *, outputs=None, constraints=None):
    """Separate reference compatibility, nonzero-tail coverage, and outputs.

    Tail extraction uses the ordered original f coordinates in batch.context.
    Membership is exact complete-integer-vector membership, with no +/- or
    projection merging.  ``public_valid_found`` is None when outputs were not
    supplied, False for an explicitly supplied empty list, and otherwise comes
    only from fresh public validation of the actual supplied output pairs.

    Counts are over distinct *joint* reference orbit pairs, except explicitly
    named distinct-tail counts.  No independent-rotation probability model is
    used.  The four coverage states distinguish incompatible patterns, all
    zero compatible tails, excluded nonzero tails, and covered nonzero tails.
    """
    # Reconstructing the public context verifies n, public-content digest,
    # instance identity, and the complete ordered original-coordinate tail.
    expected_context = context_from_public(public, batch.context.tail_positions)
    if batch.context != expected_context:
        raise ValueError("candidate batch context does not match the public instance")
    n = expected_context.n
    pattern = _pattern_positions(pattern_positions, n)
    tail = tuple(expected_context.tail_positions)
    if set(pattern).intersection(tail):
        raise ValueError("pattern and tail positions must be disjoint")
    vectors = tuple(batch.vectors)
    if any(len(vector) != len(tail)
           or any(isinstance(value, bool) or not isinstance(value, int)
                  or value not in (-1, 0, 1) for value in vector)
           or not any(vector) for vector in vectors):
        raise ValueError("candidate batch must contain complete nonzero ternary tails")
    if len(set(vectors)) != len(vectors):
        raise ValueError("candidate batch must have exact-unique complete tails")
    if batch.counts.exact_unique_count != len(vectors):
        raise ValueError("candidate batch exact_unique_count does not match its vectors")
    candidate_set = set(vectors)
    orbit = reference_orbit(public, reference_f, reference_g, constraints=constraints)
    compatible = tuple((f, g) for f, g in orbit if all(f[position] == 0 for position in pattern))
    compatible_tails = tuple(tuple(f[position] for position in tail) for f, _ in compatible)
    zero_tail_count = sum(not any(u) for u in compatible_tails)
    nonzero_tails = tuple(u for u in compatible_tails if any(u))
    covered_tails = tuple(u for u in nonzero_tails if u in candidate_set)
    if not compatible:
        coverage_state = "pattern_incompatible"
    elif not nonzero_tails:
        coverage_state = "all_compatible_tails_zero"
    elif not covered_tails:
        coverage_state = "nonzero_tail_not_covered"
    else:
        coverage_state = "covered"
    output_validations = None
    public_valid_found = None
    if outputs is not None:
        output_validations = []
        for output in outputs:
            f, g = _output_pair(output)
            output_validations.append(validate_public_candidate(
                public, f, g, constraints=constraints))
        public_valid_found = any(result["public_valid"] for result in output_validations)
    return {
        "schema": "ntru-offline-coverage-v1",
        "pattern_positions": list(pattern),
        "tail_positions": list(tail),
        "candidate_ordered_sha256": batch.ordered_sha256,
        "pattern_compatible": bool(compatible),
        "nonzero_tail_covered": bool(covered_tails),
        "public_valid_found": public_valid_found,
        "coverage_state": coverage_state,
        "unique_orbit_pair_count": len(orbit),
        "compatible_orbit_pair_count": len(compatible),
        "zero_tail_orbit_pair_count": zero_tail_count,
        "nonzero_tail_orbit_pair_count": len(nonzero_tails),
        "covered_orbit_pair_count": len(covered_tails),
        "distinct_nonzero_tail_count": len(set(nonzero_tails)),
        "distinct_covered_tail_count": len(set(covered_tails)),
        "output_count": None if output_validations is None else len(output_validations),
        "output_validations": output_validations,
        "zero_tail_excluded": True,
        "reference_only_diagnostic": True,
    }
