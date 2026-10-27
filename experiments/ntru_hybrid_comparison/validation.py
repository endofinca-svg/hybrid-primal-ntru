"""Public-only NTRU candidate checks, independent of solver and reference data.

Only pure integer ring operations are reused from the sealed experiment.  This
module neither imports an evaluator nor opens files.  Extra instance metadata
and any solver success flags are ignored; the listed public fields are checked
again for every candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from experiments.ntru_e2e.algebra import inverse_mod, negacyclic_mul


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class PublicConstraints:
    """Public acceptance choices; weights are declared by the public instance.

    ``expected_norm_sq`` defaults to w_f+w_g for fixed-weight ternary secrets.
    Setting ``require_invertible=False`` is an explicit alternative model, useful
    for algebra tests; it is never the default for the NTRU experiment.
    """

    require_invertible: bool = True
    expected_norm_sq: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.require_invertible, bool):
            raise ValueError("require_invertible must be a boolean")
        if self.expected_norm_sq is not None and (
            not _integer(self.expected_norm_sq) or self.expected_norm_sq < 0
        ):
            raise ValueError("expected_norm_sq must be a nonnegative integer or None")


def validate_public_candidate(
    public: Mapping[str, Any],
    f: Any,
    g: Any,
    *,
    constraints: PublicConstraints | None = None,
) -> dict[str, Any]:
    """Recheck a pair using only h, ring, dimension and declared constraints.

    Malformed public records or candidate vectors return structured rejection.
    Invalid acceptance configuration raises ValueError.  ``checks`` uses None
    for checks that cannot yet be evaluated, or invertibility explicitly disabled
    by the public configuration.  A valid norm alone is never sufficient.
    """

    if constraints is None:
        constraints = PublicConstraints()
    if not isinstance(constraints, PublicConstraints):
        raise ValueError("constraints must be PublicConstraints or None")
    checks: dict[str, bool | None] = {
        name: None for name in (
            "public_format", "candidate_format", "dimensions", "integer_coefficients",
            "nonzero", "ternary", "weights", "norm_sq", "public_key_relation", "invertible",
        )
    }
    reasons: list[str] = []
    result: dict[str, Any] = {
        "public_valid": False, "checks": checks, "rejection_reasons": reasons,
        "norm_sq": None, "weights": None,
        "require_invertible": constraints.require_invertible,
        "invertibility_status": "not_evaluated" if constraints.require_invertible else "not_required",
    }

    def check(name: str, value: bool, reason: str) -> None:
        checks[name] = bool(value)
        if not value:
            reasons.append(reason)

    n = q = w_f = w_g = None
    h: Any = None
    metadata_valid = False
    if isinstance(public, Mapping):
        n, q, h = public.get("n"), public.get("q"), public.get("h")
        distribution = public.get("distribution")
        if isinstance(distribution, Mapping):
            w_f, w_g = distribution.get("w_f"), distribution.get("w_g")
            metadata_valid = (
                public.get("schema") == "ntru-e2e-public-v1"
                and public.get("ring") == "x^n+1"
                and distribution.get("kind") == "fixed_weight_ternary"
                and _integer(n) and n > 0
                and _integer(q) and q > 2
                and _integer(w_f) and 0 <= w_f <= n
                and _integer(w_g) and 0 <= w_g <= n
                and isinstance(h, (list, tuple)) and len(h) == n
                and all(_integer(value) and 0 <= value < q for value in h)
            )
    check("public_format", metadata_valid, "invalid_public_instance")
    check("candidate_format", isinstance(f, (list, tuple)) and isinstance(g, (list, tuple)),
          "candidate_vectors_must_be_lists_or_tuples")
    if not metadata_valid or not checks["candidate_format"]:
        return result

    check("dimensions", len(f) == n and len(g) == n, "candidate_dimension_mismatch")
    check("integer_coefficients", all(_integer(value) for value in (*f, *g)),
          "candidate_coefficients_must_be_integers")
    if not checks["dimensions"] or not checks["integer_coefficients"]:
        return result

    result["norm_sq"] = sum(value * value for value in (*f, *g))
    result["weights"] = {"f": sum(value != 0 for value in f), "g": sum(value != 0 for value in g)}
    check("nonzero", any(f) or any(g), "zero_pair")
    check("ternary", all(value in (-1, 0, 1) for value in (*f, *g)), "nonternary_coefficient")
    check("weights", result["weights"] == {"f": w_f, "g": w_g}, "weight_mismatch")
    expected_norm = w_f + w_g if constraints.expected_norm_sq is None else constraints.expected_norm_sq
    result["expected_norm_sq"] = expected_norm
    check("norm_sq", result["norm_sq"] == expected_norm, "norm_sq_mismatch")
    check("public_key_relation", negacyclic_mul(h, f, q) == [value % q for value in g],
          "public_key_relation_mismatch")
    if constraints.require_invertible:
        try:
            inverse_mod(f, q)
        except ValueError:
            check("invertible", False, "f_not_invertible")
            result["invertibility_status"] = "not_invertible"
        else:
            check("invertible", True, "f_not_invertible")
            result["invertibility_status"] = "invertible"
    result["public_valid"] = not reasons
    return result
