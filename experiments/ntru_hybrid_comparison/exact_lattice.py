"""Exact certificates for integer row bases, without lattice reduction.

The certificate convention is ``reduced_basis = transform * original_basis``.
Matching hashes are separate integrity checks; this module establishes equality
of the integer lattices using an integer unimodular transformation.
"""

from collections.abc import Sequence


def _integer_square_matrix(matrix, name):
    """Return a private list copy, rejecting non-integer or ambiguous input."""
    if (not isinstance(matrix, Sequence) or isinstance(matrix, (str, bytes))
            or len(matrix) == 0):
        raise ValueError(f"{name} must be a nonempty integer square matrix")
    dimension = len(matrix)
    result = []
    for row in matrix:
        if (not isinstance(row, Sequence) or isinstance(row, (str, bytes))
                or len(row) != dimension):
            raise ValueError(f"{name} must be a nonempty integer square matrix")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in row):
            raise ValueError(f"{name} entries must be integers (not bool or float)")
        result.append(list(row))
    return result


def _determinant(matrix):
    """Fraction-free Bareiss elimination on an already validated copy."""
    dimension = len(matrix)
    if dimension == 1:
        return matrix[0][0]
    sign = 1
    previous_pivot = 1
    for index in range(dimension - 1):
        pivot_row = next(
            (row for row in range(index, dimension) if matrix[row][index]),
            None,
        )
        if pivot_row is None:
            return 0
        if pivot_row != index:
            matrix[index], matrix[pivot_row] = matrix[pivot_row], matrix[index]
            sign = -sign
        pivot = matrix[index][index]
        for row in range(index + 1, dimension):
            for column in range(index + 1, dimension):
                numerator = (matrix[row][column] * pivot
                             - matrix[row][index] * matrix[index][column])
                quotient, remainder = divmod(numerator, previous_pivot)
                if remainder:
                    # This cannot occur for a valid integer input with Bareiss
                    # pivoting. Do not hide an implementation error by rounding.
                    raise ArithmeticError("nonexact Bareiss division")
                matrix[row][column] = quotient
            matrix[row][index] = 0
        previous_pivot = pivot
    return sign * matrix[-1][-1]


def exact_determinant(matrix):
    """Return an arbitrary-precision integer determinant.

    A nonempty square matrix of actual Python integers is required. Malformed
    input raises ``ValueError``; no floating conversion or tolerance is used.
    The caller's matrix is never changed.
    """
    return _determinant(_integer_square_matrix(matrix, "matrix"))


def verify_unimodular_transform(original_basis, reduced_basis, transform):
    """Verify an exact full-rank row-basis lattice equality certificate.

    Invalid certificates produce a structured rejection rather than a positive
    result inferred from approximate determinants or a reduction call. The
    ``determinant`` field refers to U, and is ``None`` for malformed matrices or
    incompatible dimensions. This function does not certify reduction quality.
    """
    result = {
        "verified": False,
        "rejection_reasons": [],
        "determinant": None,
        "dimension": None,
        "original_determinant": None,
        "reduced_determinant": None,
        "transform_relation": None,
        "unimodular": None,
        "original_full_rank": None,
        "basis_convention": "row",
        "certificate_relation": "B_red=U*B_original",
    }
    matrices = []
    for name, value in (("original_basis", original_basis),
                        ("reduced_basis", reduced_basis),
                        ("transform", transform)):
        try:
            matrices.append(_integer_square_matrix(value, name))
        except ValueError as error:
            result["rejection_reasons"].append(str(error))
            matrices.append(None)
    if matrices[0] is not None:
        result["dimension"] = len(matrices[0])
    if any(matrix is None for matrix in matrices):
        return result
    original, reduced, transformation = matrices
    dimension = len(original)
    if len(reduced) != dimension or len(transformation) != dimension:
        result["rejection_reasons"].append("matrix dimensions do not match")
        return result

    # Determinant calculations mutate private copies only.
    determinant = exact_determinant(transformation)
    original_determinant = exact_determinant(original)
    result["determinant"] = determinant
    result["original_determinant"] = original_determinant
    result["reduced_determinant"] = exact_determinant(reduced)
    result["unimodular"] = determinant in (-1, 1)
    result["original_full_rank"] = original_determinant != 0
    result["transform_relation"] = all(
        sum(transformation[row][inner] * original[inner][column]
            for inner in range(dimension)) == reduced[row][column]
        for row in range(dimension)
        for column in range(dimension)
    )
    if not result["unimodular"]:
        result["rejection_reasons"].append("transform determinant is not +1 or -1")
    if not result["original_full_rank"]:
        result["rejection_reasons"].append("original basis is singular")
    if not result["transform_relation"]:
        result["rejection_reasons"].append("reduced basis does not equal U*original basis")
    result["verified"] = not result["rejection_reasons"]
    return result
