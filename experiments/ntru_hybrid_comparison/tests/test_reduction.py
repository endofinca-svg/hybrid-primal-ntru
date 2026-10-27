"""Pure protocol/journal tests; never run a real backend reduction."""

from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison.reduction import (
    ReductionProtocol, ReductionStage, execute_reduction, new_execution_record,
    protocol_from_manifest, protocol_manifest, validate_protocol,
    validate_execution_record,
)


def completed_fixture(protocol, cumulative=12.0):
    """Explicit synthetic journal, not a measured reduction result."""
    record = new_execution_record(protocol)
    record.update(status="completed", cumulative_seconds=cumulative,
                  elapsed_seconds=cumulative,
                  termination_reason="requested_schedule_completed",
                  started_utc="fixture-start", finished_utc="fixture-finish")
    record["stages"] = [dict(s, status="completed", elapsed_seconds=0.1,
                             started_utc="fixture-start", finished_utc="fixture-end", clean=True)
                        for s in record["requested"]["stages"]]
    record["actual"]["completed_lll_calls"] = 1
    record["actual"]["completed_tours"] = sum(s.requested_tours for s in protocol.schedule)
    record["actual"].update(float_type="mpfr", precision_bits=protocol.precision_bits,
                            threads=protocol.threads, gso_float_type="mpfr", strategy_sha256="0" * 64)
    record["backend"] = {
        "python": "test fixture", "python_implementation": "test fixture",
        "fpylll_version": "test fixture", "fplll_version": "test fixture",
        "available_float_types": ["mpfr"], "precision_before_execution": 53,
        "threads_before_execution": 1, "backend_probe_did_reduce": False,
        "source_hashes": {name: {"path": "not-a-real-file", "sha256": "0" * 64}
                          for name in ("fpylll.algorithms.bkz", "fpylll.algorithms.bkz2",
                                       "fpylll.fplll.lll", "fpylll.fplll.gso")},
        "strategy": {"kind": protocol.strategy, "path": "not-a-real-file", "sha256": "0" * 64,
                     "byte_count": 0, "above_max": "unpruned SVP; no preprocessing blocks", "loaded_max_beta": 100}}
    digest = sha256(json.dumps([[1, 0], [0, 1]], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record.update(original_basis_sha256=digest, reduced_basis_sha256=digest, transform_sha256=digest)
    return record


class ReductionProtocolTests(unittest.TestCase):
    def test_default_is_mpfr192_no_unmeasured_beta(self):
        p = ReductionProtocol()
        self.assertEqual((p.float_type, p.precision_bits, p.lll_method), ("mpfr", 192, "proved"))
        self.assertEqual(p.schedule, ())
        manifest = protocol_manifest(p)
        self.assertFalse(manifest["main_experiment_schedule_frozen"])
        self.assertIn("not a measured cold-start", manifest["historical_anchor"])
        self.assertIn("separate", manifest["decoder_internal_precision"])

    def test_protocol_manifest_round_trip_and_tamper(self):
        p = ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2)))
        document = protocol_manifest(p)
        self.assertEqual(protocol_from_manifest(document), p)
        for key, value in (("precision_bits", 53), ("float_type", "long double"),
                           ("bkz_implementation", "pretend"), ("unexpected", True)):
            bad = deepcopy(document)
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                protocol_from_manifest(bad)

    def test_unsupported_modes_fail_explicitly_without_fallback(self):
        cases = ({"float_type": "long double"}, {"precision_bits": True},
                 {"precision_bits": 32}, {"lll_method": "wrapper"},
                 {"lll_flags": 1}, {"gso_flags": True}, {"bkz_flags": 4},
                 {"tour_semantics": "max_loops"}, {"auto_abort": True},
                 {"threads": 0}, {"seed": -1}, {"lll_delta": float("nan")},
                 {"lll_eta": 1}, {"strategy": "unrecorded"})
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ReductionProtocol(**values)

    def test_progressive_order_and_dimension_validation(self):
        with self.assertRaises(ValueError):
            ReductionProtocol(schedule=(ReductionStage(32, 2), ReductionStage(30, 2)))
        with self.assertRaises(ValueError):
            ReductionStage(4, 0)
        with self.assertRaises(ValueError):
            ReductionStage(True, 2)
        p = ReductionProtocol(schedule=(ReductionStage(4, 1),))
        self.assertIs(validate_protocol(p, 8), p)
        with self.assertRaises(ValueError):
            validate_protocol(p, 3)

    def test_requested_tours_do_not_become_actual_tours(self):
        p = ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2)))
        r = new_execution_record(p)
        self.assertEqual(r["requested"]["total_tours"], 4)
        self.assertEqual(r["actual"]["completed_tours"], 0)
        self.assertEqual(r["actual"]["completed_lll_calls"], 0)
        self.assertNotIn("precision_bits", r["actual"])
        self.assertEqual(r["status"], "not_started")
        self.assertEqual(r["stages"], [])
        self.assertIsNone(r["termination_reason"])
        self.assertEqual(r["actual"]["backend_internal_iterations"], "not_observable")

    def test_resume_preserves_progressive_history_and_cost(self):
        prior = completed_fixture(ReductionProtocol(schedule=(ReductionStage(30, 2),)))
        saved = deepcopy(prior)
        p = ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2), ReductionStage(32, 2)))
        resumed = new_execution_record(p, prior=prior)
        self.assertEqual(resumed["stages"], prior["stages"])
        self.assertEqual(resumed["actual"]["completed_tours"], 2)
        self.assertEqual(resumed["requested"]["total_tours"], 6)
        self.assertEqual(resumed["cumulative_seconds"], 12)
        self.assertEqual(resumed["prior_cumulative_seconds"], 12)
        self.assertEqual(prior, saved)
        self.assertEqual(resumed["lineage"][0]["prior_completed_stage_count"], 3)
        resumed["stages"][0]["clean"] = False
        self.assertEqual(prior, saved)

    def test_resume_does_not_relabel_progressive_beta32_as_cold_start(self):
        prior = completed_fixture(ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2))))
        with self.assertRaises(ValueError):
            new_execution_record(ReductionProtocol(schedule=(ReductionStage(32, 2),)), prior=prior)
        with self.assertRaises(ValueError):
            new_execution_record(ReductionProtocol(schedule=(ReductionStage(30, 1), ReductionStage(31, 1))), prior=prior)
        with self.assertRaises(ValueError):
            new_execution_record(ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2)),
                                                    precision_bits=256), prior=prior)

    def test_invalid_or_incomplete_checkpoint_journal_rejected(self):
        p = ReductionProtocol(schedule=(ReductionStage(4, 1),))
        for mutation in (lambda r: r.update(status="error"),
                         lambda r: r.update(cumulative_seconds=0),
                         lambda r: r.update(active_stage={"stage_id": "unfinished"}),
                         lambda r: r["stages"][0].update(status="started")):
            record = completed_fixture(p)
            mutation(record)
            with self.assertRaises(ValueError):
                new_execution_record(p, prior=record)

    def test_backend_setup_error_records_no_actual_completed_work(self):
        # This patch intentionally prevents all imports/calls to fpylll.
        with patch("experiments.ntru_hybrid_comparison.reduction.backend_metadata",
                   side_effect=RuntimeError("synthetic unavailable backend; no call performed")):
            result = execute_reduction([[2, 0], [1, 1]],
                                       ReductionProtocol(schedule=(ReductionStage(2, 2),)))
        self.assertEqual(result["record"]["status"], "error")
        self.assertEqual(result["record"]["requested"]["total_tours"], 2)
        self.assertEqual(result["record"]["actual"]["completed_tours"], 0)
        self.assertEqual(result["record"]["actual"]["completed_lll_calls"], 0)
        self.assertEqual(result["lattice_status"], "original_only")
        self.assertIsNone(result["reduced_basis"])
        self.assertGreaterEqual(result["record"]["elapsed_seconds"], 0)

    def test_finished_record_consistency_and_matrix_binding(self):
        p = ReductionProtocol(schedule=(ReductionStage(4, 2),))
        record = completed_fixture(p)
        identity = [[1, 0], [0, 1]]
        self.assertTrue(validate_execution_record(record, p, original_basis=identity,
                                                  reduced_basis=identity, transform=identity))
        mutations = (lambda r: r["actual"].update(completed_tours=1),
                     lambda r: r["actual"].update(precision_bits=64),
                     lambda r: r["actual"].update(float_type="long double"),
                     lambda r: r["actual"].update(strategy_sha256="1" * 64),
                     lambda r: r.update(cumulative_seconds=11),
                     lambda r: r["stages"].pop(),
                     lambda r: r.update(reference_f=[1, 0]),
                     lambda r: r.update(unknown_metadata={"arbitrary": "field"}),
                     lambda r: r["actual"].update(unknown_metadata="field"),
                     lambda r: r.update(synthetic_fixture=True),
                     lambda r: r["requested"].update(total_tours=999))
        for mutate in mutations:
            bad = deepcopy(record)
            mutate(bad)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_execution_record(bad, p)
        with self.assertRaises(ValueError):
            validate_execution_record(record, p, reduced_basis=[[2, 0], [0, 1]])

    def test_resume_cannot_truncate_old_work_then_relabel_completed(self):
        old = ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2)))
        record = completed_fixture(old)
        record["stages"] = record["stages"][:1]
        record["actual"]["completed_tours"] = 0
        with self.assertRaises(ValueError):
            new_execution_record(ReductionProtocol(schedule=(ReductionStage(30, 2), ReductionStage(31, 2),
                                                           ReductionStage(32, 2))), prior=record)

    def test_failed_stage_time_is_charged_without_counting_completed_tour(self):
        p = ReductionProtocol(schedule=(ReductionStage(4, 2),))
        record = completed_fixture(p)
        failed = record["stages"].pop()
        failed.pop("clean")
        failed.update(status="failed", completed_tour=False, internal_progress="not_observable")
        record.update(status="error", termination_reason="backend_error", failed_stage=failed)
        record["actual"]["completed_tours"] -= 1
        self.assertTrue(validate_execution_record(record, p))
        record["failed_stage"]["elapsed_seconds"] = 13
        with self.assertRaises(ValueError):
            validate_execution_record(record, p)

    def test_resume_certificate_cannot_reuse_a_different_basis_record(self):
        p = ReductionProtocol()
        record = completed_fixture(p)
        checkpoint = {"original_basis": [[1, 0], [0, 1]],
                      "reduced_basis": [[1, 1], [0, 1]], "transform": [[1, 1], [0, 1]],
                      "protocol": protocol_manifest(p), "record": record, "lattice_status": "reduced_verified"}
        # Relation and determinant are valid, but the old journal binds identity.
        with self.assertRaises(ValueError):
            execute_reduction([[1, 0], [0, 1]], p, checkpoint=checkpoint)


if __name__ == "__main__":
    unittest.main()
