"""Public, exact paired inputs; no reduction or decoding occurs on build/load.

JSON integers never pass through floating point.  Hashes check transport while
public reconstruction and integer certificates check algebra independently.
Every load/copy owns its mutable data.  GSO creation is explicit and does not
run LLL, BKZ, Babai or Slicer.
"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile

from experiments.ntru_e2e.algebra import basis_rows, lattice_member, make_target, multiplication_matrix
from .candidates import CandidatePolicy, build_candidates, context_from_public
from .exact_lattice import verify_unimodular_transform
from .id_mapping import build_id_mapping, target_sha256, validate_id_mapping
from .reduction import (ReductionProtocol, protocol_from_manifest, protocol_manifest,
                        validate_protocol, validate_execution_record)


SCHEMA = "ntru-paired-public-input-v1"
ENVELOPE_SCHEMA = "ntru-paired-input-envelope-v1"
_decoder_mpfr_precision = None


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def content_sha256(value):
    return sha256(canonical_bytes(value)).hexdigest()


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _positions(values, n, label):
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} must be an explicitly ordered list or tuple")
    result = list(values)
    if any(not _integer(i) or not 0 <= i < n for i in result) or len(set(result)) != len(result):
        raise ValueError(f"invalid {label} coordinates")
    return result


def _protocol_document(protocol):
    if protocol is None:
        protocol = ReductionProtocol(schedule=())
    if isinstance(protocol, dict):
        protocol = protocol_from_manifest(protocol)
    return protocol_manifest(protocol)


def _provenance(value):
    value = deepcopy(value) if value is not None else {"kind": "test_fixture"}
    allowed = {"kind", "code_version", "module_sha256", "runtime", "source_public_path",
               "source_public_file_sha256", "selection", "built_utc"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("unknown provenance fields; reference data is not accepted")
    if value.get("kind") not in ("test_fixture", "public_input_build_only", "actual_reduction_product"):
        raise ValueError("explicit input provenance kind required")
    # Provenance is public metadata, not an extension point for secret/evaluation data.
    forbidden = {"f", "g", "reference_f", "reference_g", "truth", "secret", "reference",
                 "keygen_seed", "pattern_compatible", "nonzero_tail_covered", "public_valid_found"}
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(k, str) or k in forbidden for k in item):
                raise ValueError("reference or invalid provenance metadata")
            for child in item.values():
                check(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                check(child)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise ValueError("provenance must be JSON metadata")
    check(value)
    value.setdefault("built_utc", datetime.now(timezone.utc).isoformat())
    canonical_bytes(value)
    return value


def build_input_bundle(public, P, C, T, policy, *, remaining_positions=None,
                       target_representation="centered_mod_q", reduction_protocol=None,
                       provenance=None):
    """Build an original-only public package; no private/reference inputs accepted."""
    context = context_from_public(public, T)
    n, q = context.n, public["q"]
    P, C, T = (_positions(v, n, label) for v, label in ((P, "P"), (C, "C"), (T, "T")))
    P = sorted(P)
    if len(P) + len(C) + len(T) != n or set(P) | set(C) | set(T) != set(range(n)):
        raise ValueError("P/C/T must be a complete disjoint partition")
    remaining = sorted(C + T) if remaining_positions is None else _positions(remaining_positions, n, "remaining")
    if set(remaining) != set(C + T):
        raise ValueError("remaining coordinates do not match deletion of P")
    if target_representation not in ("raw", "centered_mod_q"):
        raise ValueError("target representation must be raw or centered_mod_q")
    batch = build_candidates(context, policy)
    d = n + len(C)
    A = multiplication_matrix(public["h"], q)
    original = basis_rows(A, q, C)
    targets = []
    for index, candidate in enumerate(batch.entries):
        raw = make_target(A, T, candidate.u, len(C))
        used = make_target(A, T, candidate.u, len(C), q if target_representation == "centered_mod_q" else None)
        delta = [b - a for a, b in zip(raw, used)]
        coefficients = [value // q for value in delta[:n]] + [0] * len(C)
        if any(value % q for value in delta[:n]) or not lattice_member(delta, A, q, C):
            raise ValueError("target translation is not in the original lattice")
        targets.append({"candidate_sha_id": candidate.candidate_id, "target_index": index,
                        "raw": raw, "used": used, "used_minus_raw": delta,
                        "translation_coefficients": coefficients,
                        "used_sha256": target_sha256(used)})
    mapping = build_id_mapping(batch, [target["used"] for target in targets])
    protocol = _protocol_document(reduction_protocol)
    validate_protocol(protocol_from_manifest(protocol), d)
    return {
        "schema_version": SCHEMA,
        "public_instance": deepcopy(public),
        "public_content_sha256": context.public_sha256,
        "public_constraints": {"coefficients": [-1, 0, 1], "w_f": public["distribution"]["w_f"],
                               "w_g": public["distribution"]["w_g"], "require_invertible": True,
                               "expected_norm_sq": public["distribution"]["w_f"] + public["distribution"]["w_g"]},
        "dimensions": {"n": n, "q": q, "k": len(P), "kappa": len(T), "d": d,
                       "d_pattern": 2 * n - len(P)},
        "coordinates": {"P": P, "C": C, "T": T, "remaining": remaining,
                        "g_from_ambient": list(range(n)),
                        "f_from_ambient": [n + C.index(i) if i in C else None for i in range(n)],
                        "f_from_tail": [T.index(i) if i in T else None for i in range(n)],
                        "f_fixed_zero": P},
        "basis_orientation": "rows: [[q*I_n,0],[A_C^T,I_core]]; A*f=h*f",
        "candidate_batch": batch.to_manifest(),
        "target_representation": target_representation,
        "targets": targets,
        "ordered_targets_sha256": content_sha256(targets),
        "target_backend_conversion": {"status": "not_applied", "record": None},
        "id_mapping": mapping,
        "id_mapping_sha256": content_sha256(mapping),
        "basis": {"original": original, "reduced": None, "transform": None,
                  "status": "original_only", "original_sha256": content_sha256(original),
                  "reduced_sha256": None, "protocol": protocol,
                  "execution": {"status": "not_started", "elapsed_seconds": None,
                                "cumulative_seconds": 0, "stages": [],
                                "actual": {"lll_completed": False, "completed_tours": 0},
                                "termination_reason": "not_started", "timing_source": "not_measured"},
                  "lattice_verification": {"status": "original_public_construction_verified",
                                           "reduced_verified": False}},
        "provenance": _provenance(provenance),
    }


def _int_matrix(matrix, d, label):
    if (not isinstance(matrix, list) or len(matrix) != d
            or any(not isinstance(row, list) or len(row) != d
                   or any(not _integer(x) for x in row) for row in matrix)):
        raise ValueError(f"{label} must be a {d} by {d} integer matrix")


def _validate_execution(record, protocol, provenance, *, original_basis=None, reduced_basis=None, transform=None):
    if not isinstance(record, dict) or record.get("status") not in ("completed", "failed", "error", "interrupted", "stopped", "partial"):
        raise ValueError("explicit reduction execution status required")
    for key in ("elapsed_seconds", "cumulative_seconds"):
        value = record.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid measured reduction {key}")
    if record["cumulative_seconds"] < record["elapsed_seconds"]:
        raise ValueError("cumulative reduction cost cannot discard current execution")
    if not isinstance(record.get("stages"), list):
        raise ValueError("reduction history must be explicit")
    if record.get("timing_source") == "synthetic_test_fixture_not_a_backend_measurement":
        # This is a certificate/serialization test, never a backend measurement.
        if provenance.get("kind") != "test_fixture" or set(record) != {
                "status", "elapsed_seconds", "cumulative_seconds", "stages", "timing_source"}:
            raise ValueError("synthetic transform record is allowed only in explicit test fixtures")
        if record["status"] != "completed" or record["stages"] or record["elapsed_seconds"] != 0 or record["cumulative_seconds"] != 0:
            raise ValueError("synthetic algebra fixture cannot claim measured reduction stages")
    else:
        validate_execution_record(record, protocol_from_manifest(protocol), original_basis=original_basis,
                                  reduced_basis=reduced_basis, transform=transform)
    canonical_bytes(record)


def validate_input_bundle(bundle, *, require_reduced_verified=False):
    """Recompute construction, mappings and target equations, then certificates."""
    if not isinstance(bundle, dict) or bundle.get("schema_version") != SCHEMA:
        raise ValueError("unsupported input package schema")
    try:
        coordinates = bundle["coordinates"]
        manifest = bundle["candidate_batch"]
        policy = CandidatePolicy(**manifest["policy"])
        basis = bundle["basis"]
        expected = build_input_bundle(
            bundle["public_instance"], coordinates["P"], coordinates["C"], coordinates["T"], policy,
            remaining_positions=coordinates["remaining"], target_representation=bundle["target_representation"],
            reduction_protocol=basis["protocol"], provenance=bundle["provenance"])
        if set(bundle) != set(expected) or set(basis) != set(expected["basis"]):
            raise ValueError("unexpected input package fields")
        # Canonical JSON distinguishes boolean from integer and normalizes tuple/list.
        for key in set(expected) - {"basis"}:
            if canonical_bytes(bundle[key]) != canonical_bytes(expected[key]):
                raise ValueError(f"input package {key} does not match public construction")
        for key in ("original", "original_sha256", "protocol"):
            if canonical_bytes(basis[key]) != canonical_bytes(expected["basis"][key]):
                raise ValueError(f"basis {key} does not match public construction")
        batch = build_candidates(context_from_public(bundle["public_instance"], coordinates["T"]), policy)
        validate_id_mapping(bundle["id_mapping"], batch=batch, targets=[target["used"] for target in bundle["targets"]])
        status = basis["status"]
        if status == "original_only":
            if canonical_bytes(basis) != canonical_bytes(expected["basis"]):
                raise ValueError("original_only must contain no executed or reduced state")
        elif status in ("reduced_verified", "reduced_unverified"):
            _int_matrix(basis["reduced"], bundle["dimensions"]["d"], "reduced basis")
            if basis["reduced_sha256"] != content_sha256(basis["reduced"]):
                raise ValueError("reduced basis digest mismatch")
            _validate_execution(basis["execution"], basis["protocol"], bundle["provenance"],
                                original_basis=basis["original"], reduced_basis=basis["reduced"],
                                transform=basis["transform"])
            if status == "reduced_verified":
                verification = verify_unimodular_transform(basis["original"], basis["reduced"], basis["transform"])
                if not verification["verified"]:
                    raise ValueError("reduced basis certificate rejected: " + str(verification["rejection_reasons"]))
                if basis["lattice_verification"] != verification:
                    raise ValueError("reduced basis verification record mismatch")
            elif basis["transform"] is not None or basis["lattice_verification"] != {
                    "status": "certificate_missing", "reduced_verified": False}:
                raise ValueError("unverified historical basis must have explicit missing certificate")
        else:
            raise ValueError("unknown basis state")
        if require_reduced_verified and status != "reduced_verified":
            raise ValueError("paired decoding requires a reduced_verified package")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("malformed input package") from exc
    return True


def attach_reduction(bundle, execution, *, allow_unverified=False):
    """Attach exact certified output, or explicitly quarantine a legacy checkpoint."""
    validate_input_bundle(bundle)
    result = deepcopy(bundle)
    basis = result["basis"]
    required = {"original_basis", "reduced_basis", "transform", "protocol", "record"}
    if (not isinstance(execution, dict) or not required <= set(execution)
            or set(execution) - required - {"lattice_status"}):
        raise ValueError("expected reduction execution record and basis certificate")
    if execution["original_basis"] != basis["original"]:
        raise ValueError("reduction output belongs to a different original lattice")
    protocol = _protocol_document(execution["protocol"])
    prior = basis["execution"] if basis["status"] != "original_only" else None
    if protocol != basis["protocol"] and prior is None:
        raise ValueError("reduction protocol differs from package plan")
    _validate_execution(execution["record"], protocol, result["provenance"],
                        original_basis=basis["original"], reduced_basis=execution["reduced_basis"],
                        transform=execution["transform"])
    if prior is not None and execution["record"] != prior:
        incoming = execution["record"]
        if (not any(item.get("record_sha256") == content_sha256(prior) for item in incoming.get("lineage", []))
                or incoming.get("prior_cumulative_seconds") != prior["cumulative_seconds"]
                or incoming["stages"][:len(prior["stages"])] != prior["stages"]):
            raise ValueError("continuation must retain the previous execution history and cumulative cost")
        old_protocol = {k: v for k, v in basis["protocol"].items() if k != "schedule"}
        new_protocol = {k: v for k, v in protocol.items() if k != "schedule"}
        if old_protocol != new_protocol:
            raise ValueError("continuation changes reduction settings")
    basis["protocol"] = protocol
    basis.update(reduced=deepcopy(execution["reduced_basis"]), transform=deepcopy(execution["transform"]),
                 reduced_sha256=content_sha256(execution["reduced_basis"]), execution=deepcopy(execution["record"]))
    if basis["transform"] is None and allow_unverified:
        basis.update(status="reduced_unverified", lattice_verification={"status": "certificate_missing", "reduced_verified": False})
    else:
        verification = verify_unimodular_transform(basis["original"], basis["reduced"], basis["transform"])
        if not verification["verified"]:
            raise ValueError("invalid reduced basis certificate: " + str(verification["rejection_reasons"]))
        basis.update(status="reduced_verified", lattice_verification=verification)
    if result["provenance"]["kind"] != "test_fixture":
        result["provenance"]["kind"] = "actual_reduction_product"
    validate_input_bundle(result)
    return result


def save_input_bundle(path, bundle):
    """Validate, fsync a same-directory temporary file, then atomically publish."""
    validate_input_bundle(bundle)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"schema": ENVELOPE_SCHEMA, "complete": True,
                "payload": bundle, "payload_sha256": content_sha256(bundle)}
    data = canonical_bytes(envelope) + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return envelope["payload_sha256"]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_input_bundle(path, *, require_reduced_verified=False):
    """Load fresh independent integers/data, rejecting partial or changed input."""
    try:
        envelope = json.loads(Path(path).read_bytes(), object_pairs_hook=_unique_object,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("incomplete or malformed input package") from exc
    if (not isinstance(envelope, dict) or set(envelope) != {"schema", "complete", "payload", "payload_sha256"}
            or envelope["schema"] != ENVELOPE_SCHEMA or envelope["complete"] is not True):
        raise ValueError("input package is not complete")
    if content_sha256(envelope["payload"]) != envelope["payload_sha256"]:
        raise ValueError("input package digest mismatch")
    validate_input_bundle(envelope["payload"], require_reduced_verified=require_reduced_verified)
    return envelope["payload"]


def create_working_copy(bundle, *, use_reduced=True, float_type=None, precision_bits=None):
    """Create private mutable inputs and optionally a fresh GSO, without reduction.

    The decoder's GSO precision is independent of the integer reduction protocol.
    Call once per D/E arm.  Original-only copies require use_reduced=False.
    """
    global _decoder_mpfr_precision
    validate_input_bundle(bundle, require_reduced_verified=use_reduced)
    result = {"original_rows": deepcopy(bundle["basis"]["original"]),
              "rows": deepcopy(bundle["basis"]["reduced"] if use_reduced else bundle["basis"]["original"]),
              "targets": [deepcopy(item["used"]) for item in bundle["targets"]],
              "id_mapping": deepcopy(bundle["id_mapping"]), "gso": None,
              "gso_metadata": {"status": "not_created", "float_type": None, "precision_bits": None}}
    if float_type is not None:
        if float_type not in ("mpfr", "long double"):
            raise ValueError("unsupported explicit decoder GSO float type")
        if float_type == "mpfr" and (not _integer(precision_bits) or precision_bits < 53):
            raise ValueError("explicit MPFR decoder precision required")
        if float_type != "mpfr" and precision_bits is not None:
            raise ValueError("long double has platform precision; do not label it MPFR bits")
        from fpylll import FPLLL, GSO, IntegerMatrix
        if float_type == "mpfr":
            if _decoder_mpfr_precision is not None and _decoder_mpfr_precision != precision_bits:
                raise ValueError("decoder GSO copies in one process must share MPFR precision; use separate processes for different precisions")
            FPLLL.set_precision(precision_bits)
            if FPLLL.get_precision() != precision_bits:
                raise RuntimeError("decoder MPFR precision was not applied")
        matrix = IntegerMatrix.from_matrix(result["rows"], int_type="mpz")
        result["gso"] = GSO.Mat(matrix, float_type=float_type)
        if result["gso"].float_type != float_type:
            raise RuntimeError("decoder GSO float type mismatch")
        result["gso"].update_gso()
        if float_type == "mpfr":
            _decoder_mpfr_precision = precision_bits
        result["gso_metadata"] = {"status": "created_without_reduction", "float_type": float_type,
                                  "precision_bits": precision_bits, "separate_from_reduction_protocol": True,
                                  "precision_scope": "MPFR is process-global; do not change while any GSO is alive"}
    elif precision_bits is not None:
        raise ValueError("precision requires an explicit decoder float type")
    return result
