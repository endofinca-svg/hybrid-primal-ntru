"""Deterministic, public-input-only ternary tail candidates for phase 2A.

The tail is ordered in ORIGINAL f coordinates: u[i] is f[T[i]].  Candidate
identity includes that ordered coordinate list and the public-instance context,
but never the decoder, candidate rule, pattern, reference secret, or run state.
The public fingerprint below hashes canonical JSON content, not file bytes.

Generation has no decoder side effects.  All batches therefore report zero
submitted/completed targets and an unknown (None) projection-group count.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import combinations, product
import json
from collections.abc import Iterable, Mapping


ORDER_GENERATED = "weight_ascending/support_lexicographic/sign_lexicographic(-1,+1)"
ORDER_SUPPLIED = "supplied_order/filter_then_exact_deduplicate_keep_first"
COUNT_DEFINITIONS = {
    "theoretical_full_nonzero_count": "3**kappa - 1; theoretical, not generated",
    "generated_raw_count": "actual entries generated or received, before filtering",
    "policy_kept_count": "entries satisfying the nonzero weight policy, before exact deduplication",
    "exact_unique_count": "complete integer tail vectors after exact deduplication, keep first",
    "projection_group_count": "backend projection groups; null until actually computed",
    "submitted_count": "external targets actually submitted to a decoder; zero in phase 2A",
    "completed_count": "external targets returned with an explicit decoder status; zero in phase 2A",
}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CandidateContext:
    """Public instance and ordered original-f tail coordinates, with kappa > 0."""

    instance_id: str
    public_sha256: str
    n: int
    tail_positions: tuple[int, ...]

    def __post_init__(self):
        if not isinstance(self.instance_id, str) or not self.instance_id:
            raise ValueError("instance_id must be a nonempty string")
        if (not isinstance(self.public_sha256, str) or len(self.public_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.public_sha256)):
            raise ValueError("public_sha256 must be a lowercase SHA-256 hex digest")
        if not _integer(self.n) or self.n < 1:
            raise ValueError("n must be a positive integer")
        if isinstance(self.tail_positions, (set, frozenset, Mapping)):
            raise ValueError("tail_positions must preserve explicit coordinate order")
        try:
            tail = tuple(self.tail_positions)
        except TypeError as exc:
            raise ValueError("tail_positions must be an ordered coordinate sequence") from exc
        if not tail:
            raise ValueError("kappa=0 belongs to the primal path, not a hybrid candidate policy")
        if any(not _integer(i) or not 0 <= i < self.n for i in tail):
            raise ValueError("tail_positions contains an invalid original-f coordinate")
        if len(set(tail)) != len(tail):
            raise ValueError("tail_positions must not contain duplicate coordinates")
        object.__setattr__(self, "tail_positions", tail)

    @property
    def kappa(self):
        return len(self.tail_positions)

    def to_manifest(self):
        return {"instance_id": self.instance_id, "public_sha256": self.public_sha256,
                "n": self.n, "tail_positions": list(self.tail_positions)}


def context_from_public(public: Mapping, tail_positions: Iterable[int]) -> CandidateContext:
    """Validate a secret-free public-v1 document and hash its canonical contents.

    Unknown keys (including reference labels/secrets) are rejected rather than
    silently included or ignored.  No reference file or solver is accessed.
    A caller's direct CandidateContext construction is a trust boundary: callers
    loading an actual public document should use this helper.
    """
    expected = {"schema", "instance_id", "n", "q", "ring", "h", "distribution"}
    if not isinstance(public, Mapping) or set(public) != expected:
        raise ValueError("public instance must contain only the public-v1 fields")
    if public["schema"] != "ntru-e2e-public-v1" or public["ring"] != "x^n+1":
        raise ValueError("unsupported public schema or ring")
    n, q = public["n"], public["q"]
    if not _integer(n) or n < 1 or not _integer(q) or q <= 2:
        raise ValueError("invalid public dimension or modulus")
    h = public["h"]
    if (not isinstance(h, (list, tuple)) or len(h) != n
            or any(not _integer(x) or not 0 <= x < q for x in h)):
        raise ValueError("public h must contain n canonical integer residues")
    distribution = public["distribution"]
    if (not isinstance(distribution, Mapping)
            or set(distribution) != {"kind", "w_f", "w_g"}
            or distribution["kind"] != "fixed_weight_ternary"
            or any(not _integer(distribution[key]) or not 0 <= distribution[key] <= n
                   for key in ("w_f", "w_g"))):
        raise ValueError("invalid fixed-weight ternary public distribution")
    canonical_public = {key: public[key] for key in expected}
    canonical_public["h"] = list(h)
    canonical_public["distribution"] = dict(distribution)
    return CandidateContext(public["instance_id"], _canonical_hash(canonical_public),
                            n, tail_positions)


@dataclass(frozen=True)
class CandidatePolicy:
    """Explicit nonzero ternary policy; weight_range means weights 1..max_weight."""

    mode: str
    max_weight: int | None = None

    def __post_init__(self):
        if self.mode not in ("full_nonzero", "weight_range"):
            raise ValueError("mode must be full_nonzero or weight_range")
        if self.mode == "full_nonzero" and self.max_weight is not None:
            raise ValueError("full_nonzero does not accept a weight cap")
        if self.mode == "weight_range" and (not _integer(self.max_weight) or self.max_weight < 1):
            raise ValueError("weight_range requires a positive integer max_weight")

    def to_manifest(self):
        return asdict(self)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    u: tuple[int, ...]
    weight: int


@dataclass(frozen=True)
class CandidateCounts:
    theoretical_full_nonzero_count: int
    generated_raw_count: int
    policy_kept_count: int
    exact_unique_count: int
    projection_group_count: int | None = None
    submitted_count: int = 0
    completed_count: int = 0


@dataclass(frozen=True)
class CandidateBatch:
    context: CandidateContext
    policy: CandidatePolicy
    entries: tuple[Candidate, ...]
    counts: CandidateCounts
    order: str
    ordered_sha256: str

    @property
    def vectors(self):
        return tuple(candidate.u for candidate in self.entries)

    def to_manifest(self):
        return {
            "schema": "ntru-hybrid-candidate-batch-v1",
            "context": self.context.to_manifest(),
            "public_sha256_scope": "canonical public-v1 JSON content, not file bytes",
            "policy": self.policy.to_manifest(), "order": self.order,
            "counts": asdict(self.counts), "count_definitions": dict(COUNT_DEFINITIONS),
            "ordered_sha256": self.ordered_sha256,
            "ordered_sha256_scope": "context plus ordered complete tails and candidate IDs; excludes policy/run progress",
            "candidates": [{"candidate_id": item.candidate_id, "u": list(item.u),
                            "weight": item.weight} for item in self.entries],
        }


def _generated_tails(kappa, max_weight):
    """Enumerate only desired supports and signs, never 3**kappa then filter."""
    for weight in range(1, max_weight + 1):
        for support in combinations(range(kappa), weight):
            for signs in product((-1, 1), repeat=weight):
                u = [0] * kappa
                for position, sign in zip(support, signs):
                    u[position] = sign
                yield tuple(u)


def build_candidates(context: CandidateContext, policy: CandidatePolicy, *,
                     supplied_candidates=None) -> CandidateBatch:
    """Generate or receive tails, apply the rule, and exact-deduplicate keep-first.

    Supplied tails must have kappa integer ternary entries.  Malformed tails
    raise ValueError; valid zero/overweight tails are counted raw then filtered.
    Opposite signs and equal projected targets are NEVER merged.  Directly
    generated truncated batches have raw=kept=unique (e.g. kappa12,h2: 288).
    """
    if not isinstance(context, CandidateContext) or not isinstance(policy, CandidatePolicy):
        raise ValueError("context and policy must use the explicit candidate types")
    kappa = context.kappa
    max_weight = kappa if policy.mode == "full_nonzero" else policy.max_weight
    if max_weight > kappa:
        raise ValueError("max_weight must not exceed kappa")
    if supplied_candidates is None:
        source, order = _generated_tails(kappa, max_weight), ORDER_GENERATED
    else:
        if isinstance(supplied_candidates, (set, frozenset, Mapping)):
            raise ValueError("supplied_candidates must preserve explicit candidate order")
        try:
            source = iter(supplied_candidates)
        except TypeError as exc:
            raise ValueError("supplied_candidates must be iterable") from exc
        order = ORDER_SUPPLIED
    raw, kept = 0, 0
    entries, seen = [], set()
    for values in source:
        raw += 1
        if isinstance(values, (set, frozenset, Mapping)):
            raise ValueError("each supplied tail must preserve explicit coefficient order")
        try:
            u = tuple(values)
        except TypeError as exc:
            raise ValueError("each supplied tail must be an integer vector") from exc
        if len(u) != kappa or any(not _integer(x) or x not in (-1, 0, 1) for x in u):
            raise ValueError("each supplied tail must have kappa ternary integer coefficients")
        weight = sum(x != 0 for x in u)
        if not 1 <= weight <= max_weight:
            continue
        kept += 1
        if u in seen:
            continue
        seen.add(u)
        identifier = _canonical_hash({"schema": "ntru-hybrid-tail-id-v1",
                                      "context": context.to_manifest(), "u": list(u)})
        entries.append(Candidate(identifier, u, weight))
    entries = tuple(entries)
    digest = _canonical_hash({"schema": "ntru-hybrid-tail-order-v1",
                              "context": context.to_manifest(),
                              "candidates": [{"candidate_id": item.candidate_id,
                                              "u": list(item.u)} for item in entries]})
    counts = CandidateCounts(3 ** kappa - 1, raw, kept, len(entries))
    return CandidateBatch(context, policy, entries, counts, order, digest)
