"""Exact integer algebra for the fixed-weight NTRU recovery experiment.

Polynomials are coefficient lists in increasing degree order.  Lattice bases
contain rows, while multiplication matrices act on coefficient columns.
This module contains no lattice reduction or decoding implementation.
"""

from itertools import combinations, product


def _integer(value):
    # JSON booleans must not become coefficients merely because bool subclasses
    # int in Python.  The offline verifier uses the same integer convention.
    return isinstance(value, int) and not isinstance(value, bool)


def _vector(values, name):
    values = list(values)
    if not values or any(not _integer(value) for value in values):
        raise ValueError(f"{name} must be a nonempty integer vector")
    return values


def _modulus(q):
    if not _integer(q) or q < 2:
        raise ValueError("q must be an integer at least two")
    return q


def _indices(values, n, name):
    values = list(values)
    if any(not _integer(value) or not 0 <= value < n for value in values):
        raise ValueError(f"{name} contains an invalid coordinate")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate coordinates")
    return values


def _matrix(A):
    A = [_vector(row, "matrix row") for row in A]
    n = len(A)
    if n == 0 or any(len(row) != n for row in A):
        raise ValueError("A must be a nonempty square integer matrix")
    return A


def negacyclic_mul(a, b, q=None):
    """Multiply in Z[x]/(x^n+1), optionally reducing into [0, q)."""
    a, b = _vector(a, "a"), _vector(b, "b")
    if len(a) != len(b):
        raise ValueError("polynomials must have the same length")
    if q is not None:
        _modulus(q)
    n = len(a)
    result = [0] * n
    for i, left in enumerate(a):
        for j, right in enumerate(b):
            degree = i + j
            result[degree % n] += (-1 if degree >= n else 1) * left * right
    return result if q is None else [value % q for value in result]


def signed_rotate(a, shift=1):
    """Multiply by x^shift modulo x^n+1, including negative shifts."""
    a = _vector(a, "a")
    if not _integer(shift):
        raise ValueError("shift must be an integer")
    n = len(a)
    result = [0] * n
    for i, value in enumerate(a):
        turns, position = divmod(i + shift, n)
        result[position] = -value if turns % 2 else value
    return result


def _trim(poly):
    while poly and poly[-1] == 0:
        poly.pop()
    return poly


def _poly_sub(a, b, q):
    result = [0] * max(len(a), len(b))
    for i in range(len(result)):
        result[i] = ((a[i] if i < len(a) else 0)
                     - (b[i] if i < len(b) else 0)) % q
    return _trim(result)


def _poly_mul(a, b, q):
    if not a or not b:
        return []
    result = [0] * (len(a) + len(b) - 1)
    for i, left in enumerate(a):
        for j, right in enumerate(b):
            result[i + j] = (result[i + j] + left * right) % q
    return _trim(result)


def _poly_divmod(a, b, q):
    remainder = _trim([value % q for value in a])
    divisor = _trim([value % q for value in b])
    if not divisor:
        raise ValueError("division by the zero polynomial")
    try:
        leading_inverse = pow(divisor[-1], -1, q)
    except ValueError as exc:
        raise ValueError("polynomial division needs an invertible leading coefficient") from exc
    quotient = [0] * max(0, len(remainder) - len(divisor) + 1)
    while remainder and len(remainder) >= len(divisor):
        degree = len(remainder) - len(divisor)
        coefficient = remainder[-1] * leading_inverse % q
        quotient[degree] = coefficient
        for i, value in enumerate(divisor):
            remainder[degree + i] = (remainder[degree + i] - coefficient * value) % q
        _trim(remainder)
    return _trim(quotient), remainder


def inverse_mod(a, q):
    """Invert a modulo (q, x^n+1), or raise ValueError.

    The experiment uses prime q.  Polynomial Euclid also works for composite
    moduli when its divisions have unit leading coefficients; no general
    inversion guarantee for composite q is provided.
    """
    a = _vector(a, "a")
    q = _modulus(q)
    n = len(a)
    modulus = [1] + [0] * (n - 1) + [1]
    old_r, r = modulus, _trim([value % q for value in a])
    old_s, s = [], [1]
    while r:
        quotient, remainder = _poly_divmod(old_r, r, q)
        old_r, r = r, remainder
        old_s, s = s, _poly_sub(old_s, _poly_mul(quotient, s, q), q)
    if len(old_r) != 1:
        raise ValueError("polynomial is not invertible modulo (q, x^n+1)")
    try:
        scale = pow(old_r[0], -1, q)
    except ValueError as exc:
        raise ValueError("polynomial is not invertible modulo (q, x^n+1)") from exc
    inverse = [0] * n
    for degree, value in enumerate(old_s):
        turns, position = divmod(degree, n)
        inverse[position] += (-1 if turns % 2 else 1) * scale * value
    inverse = [value % q for value in inverse]
    if negacyclic_mul(a, inverse, q) != [1] + [0] * (n - 1):
        raise ValueError("polynomial is not invertible modulo (q, x^n+1)")
    return inverse


def multiplication_matrix(h, q):
    """Return rows of A such that A*f equals h*f modulo q."""
    h = _vector(h, "h")
    q = _modulus(q)
    columns = [signed_rotate(h, column) for column in range(len(h))]
    return [[columns[column][row] % q for column in range(len(h))]
            for row in range(len(h))]


def partition(n, P, kappa):
    """Use sorted remaining coordinates, taking the last kappa as the tail."""
    if not _integer(n) or n <= 0:
        raise ValueError("n must be a positive integer")
    P = sorted(_indices(P, n, "P"))
    if not _integer(kappa) or not 0 <= kappa <= n - len(P):
        raise ValueError("kappa must fit within the surviving coordinates")
    pattern = set(P)
    remaining = [coordinate for coordinate in range(n) if coordinate not in pattern]
    split = len(remaining) - kappa
    return {"P": P, "C": remaining[:split], "T": remaining[split:]}


def basis_rows(A, q, C):
    """Return the row basis [[q*I_n, 0], [A_C^T, I_core]]."""
    A, q = _matrix(A), _modulus(q)
    n = len(A)
    C = _indices(C, n, "C")
    d = n + len(C)
    rows = [[q if row == column else 0 for column in range(d)]
            for row in range(n)]
    for core_index, coordinate in enumerate(C):
        rows.append([A[row][coordinate] for row in range(n)]
                    + [int(core_index == column) for column in range(len(C))])
    return rows


def make_target(A, T, u, core_size, q=None):
    """Return (-A_T*u, 0_core), optionally centering the first block modulo q.

    Centering shifts the target by a lattice vector in q*Z^n.  Reconstruction
    must use this actual target, including the same choice of representatives.
    """
    A = _matrix(A)
    n = len(A)
    T = _indices(T, n, "T")
    u = list(u)
    if len(u) != len(T) or any(not _integer(value) for value in u):
        raise ValueError("u must contain one integer per tail coordinate")
    if not _integer(core_size) or not 0 <= core_size <= n - len(T):
        raise ValueError("invalid core_size")
    target = [-sum(A[row][coordinate] * guess for coordinate, guess in zip(T, u))
              for row in range(n)]
    if q is not None:
        q = _modulus(q)
        target = [(value + q // 2) % q - q // 2 for value in target]
    return target + [0] * core_size


def reconstruct(v, target, P, C, T, u, n):
    """Reconstruct (f, g) from an ambient lattice vector and its actual target."""
    if not _integer(n) or n <= 0:
        raise ValueError("n must be a positive integer")
    P, C, T = (_indices(values, n, name)
               for values, name in ((P, "P"), (C, "C"), (T, "T")))
    if len(P) + len(C) + len(T) != n or set(P) | set(C) | set(T) != set(range(n)):
        raise ValueError("P, C, T must be a disjoint partition of the coordinates")
    v, target = _vector(v, "v"), _vector(target, "target")
    u = list(u)
    if len(v) != n + len(C) or len(target) != len(v):
        raise ValueError("v and target have the wrong ambient dimension")
    if len(u) != len(T) or any(not _integer(value) for value in u):
        raise ValueError("u must contain one integer per tail coordinate")
    if any(target[n:]):
        raise ValueError("the core block of the target must be zero")
    residual = [value - origin for value, origin in zip(v, target)]
    f = [0] * n
    for coordinate, value in zip(C, residual[n:]):
        f[coordinate] = value
    for coordinate, value in zip(T, u):
        f[coordinate] = value
    return {"f": f, "g": residual[:n]}


def lattice_member(v, A, q, C):
    """Check ambient-vector membership using A_C*z-y == 0 modulo q."""
    A, q = _matrix(A), _modulus(q)
    n = len(A)
    C = _indices(C, n, "C")
    v = list(v)
    if len(v) != n + len(C) or any(not _integer(value) for value in v):
        return False
    return all((sum(A[row][coordinate] * value for coordinate, value in zip(C, v[n:]))
                - v[row]) % q == 0 for row in range(n))


def enumerate_tails(kappa, w_f):
    """Enumerate every nonzero ternary tail of weight at most public w_f."""
    if not _integer(kappa) or kappa < 0:
        raise ValueError("kappa must be a nonnegative integer")
    if not _integer(w_f) or w_f < 0:
        raise ValueError("w_f must be a nonnegative integer")
    guesses = []
    for weight in range(1, min(kappa, w_f) + 1):
        for support in combinations(range(kappa), weight):
            for signs in product((-1, 1), repeat=weight):
                guess = [0] * kappa
                for coordinate, sign in zip(support, signs):
                    guess[coordinate] = sign
                guesses.append(guess)
    guesses.sort()
    return {"raw_nonzero": 3 ** kappa - 1, "guesses": guesses}


def validate_candidate(public, f, g):
    """Validate a candidate using only public fixed-weight ternary metadata."""
    checks = {name: False for name in ("public_format", "dimensions", "nonzero",
                                      "ternary", "weights", "invertible",
                                      "public_key_relation")}
    try:
        n, q = public["n"], _modulus(public["q"])
        h = _vector(public["h"], "h")
        distribution = public["distribution"]
        checks["public_format"] = (
            public.get("schema") == "ntru-e2e-public-v1"
            and public.get("ring") == "x^n+1"
            and distribution.get("kind") == "fixed_weight_ternary"
            and _integer(n) and n > 0 and len(h) == n)
        f, g = _vector(f, "f"), _vector(g, "g")
        checks["dimensions"] = len(f) == len(g) == n
        if not checks["public_format"] or not checks["dimensions"]:
            return {"valid": False, "checks": checks}
        checks["nonzero"] = any(f) or any(g)
        checks["ternary"] = all(value in (-1, 0, 1) for value in f + g)
        w_f, w_g = distribution["w_f"], distribution["w_g"]
        checks["weights"] = (
            _integer(w_f) and _integer(w_g)
            and 0 <= w_f <= n and 0 <= w_g <= n
            and sum(value != 0 for value in f) == w_f
            and sum(value != 0 for value in g) == w_g)
        try:
            inverse = inverse_mod(f, q)
            checks["invertible"] = True
        except ValueError:
            inverse = None
        checks["public_key_relation"] = (
            inverse is not None
            and negacyclic_mul(h, f, q) == [value % q for value in g]
            and negacyclic_mul(g, inverse, q) == [value % q for value in h])
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"valid": False, "checks": checks}
    return {"valid": all(checks.values()), "checks": checks}
