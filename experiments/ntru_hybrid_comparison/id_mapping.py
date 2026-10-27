"""External candidate identity for paired experiments; no decoder is called.

Each complete tail keeps its own identity even when another tail has an equal
target or a backend later finds a projection collision.  Backend integer IDs
and target indices refer to the frozen external candidate order, starting at
zero.  Reconciliation uses the returned integer ID, never return position.
"""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json

from .candidates import CandidateBatch


MAPPING_FIELDS = frozenset({"candidate_sha_id", "backend_integer_id", "u",
                            "target_index", "target_sha256"})
TARGET_DIGEST_SCOPE = "canonical ASCII JSON complete integer target vector"


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _hex_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _ordered_sequence(value, name):
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an explicit ordered list or tuple")
    return value


def _target(value):
    _ordered_sequence(value, "target")
    if not value or any(not _integer(coefficient) for coefficient in value):
        raise ValueError("target must be a nonempty complete integer vector")
    return list(value)


def target_sha256(target):
    """Hash a full integer vector, without floats, coordinates, or projection.

    The encoded bytes are json.dumps(vector, sort_keys=True,
    separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii").
    Python JSON integers retain arbitrary precision.  The public package binds
    this vector digest to its coordinate interpretation separately.
    """
    encoded = json.dumps(_target(target), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    return sha256(encoded).hexdigest()


def build_id_mapping(batch, targets):
    """Bind every candidate to one integer ID and its actual ordered target.

    Targets are complete vectors in the same order as batch.entries, including
    duplicates if distinct external candidates produce identical targets.
    This function does not mutate the batch or its progress counters.
    """
    if not isinstance(batch, CandidateBatch):
        raise ValueError("batch must be a CandidateBatch")
    _ordered_sequence(targets, "targets")
    if len(batch.entries) != len(targets):
        raise ValueError("one complete target is required per external candidate")
    mapping = [
        {"candidate_sha_id": entry.candidate_id, "backend_integer_id": index,
         "u": list(entry.u), "target_index": index,
         "target_sha256": target_sha256(target)}
        for index, (entry, target) in enumerate(zip(batch.entries, targets))
    ]
    return validate_id_mapping(mapping, batch=batch, targets=targets)


def validate_id_mapping(mapping, *, batch=None, targets=None):
    """Validate a frozen one-to-one mapping and return an independent copy.

    With batch and/or targets, cross-check the complete candidate identities
    and full integer target digests as well.  A mapping by itself cannot prove
    an absent target's contents or the candidate context; package loading must
    pass both dependencies after checking them.
    """
    _ordered_sequence(mapping, "mapping")
    if batch is not None:
        if not isinstance(batch, CandidateBatch):
            raise ValueError("batch must be a CandidateBatch")
        if len(mapping) != len(batch.entries):
            raise ValueError("mapping length does not match the candidate batch")
    target_vectors = None
    if targets is not None:
        _ordered_sequence(targets, "targets")
        if len(mapping) != len(targets):
            raise ValueError("mapping length does not match the target list")
        target_vectors = [_target(target) for target in targets]
        if len({len(target) for target in target_vectors}) > 1:
            raise ValueError("all complete targets must have the same dimension")
    copied, ids, tails, tail_dimension = [], set(), set(), None
    for index, row in enumerate(mapping):
        if not isinstance(row, Mapping) or set(row) != MAPPING_FIELDS:
            raise ValueError("mapping row must contain the exact identity fields")
        candidate_id = row["candidate_sha_id"]
        if not _hex_digest(candidate_id) or candidate_id in ids:
            raise ValueError("candidate SHA IDs must be valid and unique")
        ids.add(candidate_id)
        if (not _integer(row["backend_integer_id"])
                or row["backend_integer_id"] != index):
            raise ValueError("backend_integer_id must equal frozen zero-based order")
        if not _integer(row["target_index"]) or row["target_index"] != index:
            raise ValueError("target_index must equal frozen zero-based order")
        u = row["u"]
        _ordered_sequence(u, "complete tail")
        if (not u or any(not _integer(x) or x not in (-1, 0, 1) for x in u)
                or not any(u)):
            raise ValueError("u must be a complete nonzero integer ternary tail")
        if tail_dimension is None:
            tail_dimension = len(u)
        if len(u) != tail_dimension or tuple(u) in tails:
            raise ValueError("complete tails must have equal dimensions and be unique")
        tails.add(tuple(u))
        if not _hex_digest(row["target_sha256"]):
            raise ValueError("target_sha256 must be a lowercase SHA-256 digest")
        if batch is not None:
            entry = batch.entries[index]
            if candidate_id != entry.candidate_id or tuple(u) != entry.u:
                raise ValueError("mapping candidate identity or order does not match batch")
        if target_vectors is not None:
            if row["target_sha256"] != target_sha256(target_vectors[index]):
                raise ValueError("mapping target digest does not match the actual target")
        copied.append({"candidate_sha_id": candidate_id,
                       "backend_integer_id": index, "u": list(u),
                       "target_index": index, "target_sha256": row["target_sha256"]})
    return copied


def reconcile_results(mapping, results):
    """Restore identities for unordered, optionally incomplete return records.

    Each result must explicitly carry an integer backend_integer_id.  Unknown
    and duplicate IDs are diagnosed; duplicates are retained and are never
    silently resolved by choosing one.  Missing IDs stay missing.  The result
    is identity bookkeeping only: it does not assert decoding, submission,
    completion, success, or any experiment counter.
    """
    frozen = validate_id_mapping(mapping)
    _ordered_sequence(results, "results")
    known = {row["backend_integer_id"]: [] for row in frozen}
    unknown = []
    for index, result in enumerate(results):
        if not isinstance(result, Mapping) or "backend_integer_id" not in result:
            raise ValueError("every result must explicitly carry backend_integer_id")
        identifier = result["backend_integer_id"]
        if not _integer(identifier):
            raise ValueError("returned backend_integer_id must be an integer, not a bool")
        returned = deepcopy(dict(result))
        if identifier not in known:
            unknown.append({"result_index": index, "backend_integer_id": identifier,
                            "result": returned})
        else:
            known[identifier].append(returned)
    duplicate_ids = [identifier for identifier, records in known.items() if len(records) > 1]
    missing_ids = [identifier for identifier, records in known.items() if not records]
    ordered = []
    for row in frozen:
        records = known[row["backend_integer_id"]]
        ordered.append({**row,
                        "identity_status": ("missing" if not records else
                                            "duplicate" if len(records) > 1 else "returned"),
                        "results": records})
    complete = not unknown and not duplicate_ids and not missing_ids
    return {"scope": "identity_reconciliation_only_no_decoder_activity_or_progress_update",
            "complete": complete,
            "status": ("complete" if complete else "invalid_return_identity"
                       if unknown or duplicate_ids else "incomplete"),
            "ordered_results": ordered,
            "unknown_ids": [record["backend_integer_id"] for record in unknown],
            "unknown_results": unknown, "duplicate_ids": duplicate_ids,
            "missing_ids": missing_ids}
