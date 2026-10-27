"""Real bounded Linux processes; no lattice code or backend imports."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from experiments.ntru_hybrid_comparison.supervisor import (
    _read_process,
    probe_environment,
    supervise_command,
)


OBSERVATIONS = []


class SupervisorTests(unittest.TestCase):
    def run_fixture(self, code, *, work_seconds=0.25, reserve=0.6, fault_hook=None, ownership="session"):
        token = "ntru-supervisor-test-" + uuid.uuid4().hex
        start = time.monotonic()
        result = supervise_command(
            # -S excludes site-installed exception reporters from lightweight
            # fixture runtime; the tested worker still raises uncaught errors.
            [sys.executable, "-S", "-B", "-c", code, token],
            work_deadline=start + work_seconds,
            hard_deadline=start + work_seconds + reserve,
            sample_interval=0.005,
            poll_interval=0.005,
            fault_hook=fault_hook,
            ownership=ownership,
        )
        OBSERVATIONS.append({
            "test_id": self.id(),
            "fixture_code_sha256": hashlib.sha256(code.encode()).hexdigest(),
            "unique_trial_token": token,
            "configured_work_seconds": work_seconds,
            "configured_cleanup_reserve_seconds": reserve,
            "configured_ownership": ownership,
            "result": deepcopy(result),
        })
        # Last-resort identity-limited cleanup protects the test host even if
        # an assertion exposes a regression in the supervisor.
        self.addCleanup(self.force_cleanup, result["observed_processes"])
        self.assertTrue(result["cleanup_verified"], result)
        self.assertEqual([], result["residual_active_processes"])
        for record in result["observed_processes"]:
            now = _read_process(record["pid"])
            self.assertTrue(
                now is None or now["starttime_ticks"] != record["starttime_ticks"] or now["state"] in ("Z", "X"),
                (record, now),
            )
        self.assertTrue(result["hard_deadline_met"], result)
        return result

    @staticmethod
    def force_cleanup(records):
        for record in records:
            now = _read_process(record["pid"])
            if now and now["starttime_ticks"] == record["starttime_ticks"] and now["state"] not in ("Z", "X"):
                try:
                    os.kill(record["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_normal_exit(self):
        result = self.run_fixture("import time; time.sleep(.03)")
        self.assertEqual("completed", result["execution_status"])
        self.assertEqual(0, result["returncode"])
        self.assertTrue(result["run_success"])
        self.assertEqual([], result["signals"])

    def test_plain_timeout(self):
        result = self.run_fixture("import time; time.sleep(10)", work_seconds=0.10)
        self.assertEqual("timeout", result["execution_status"])
        self.assertFalse(result["run_success"])
        self.assertTrue(any(row["signal"] == signal.SIGTERM for row in result["signals"]))
        self.assertLess(result["total_wall_seconds"], 0.70)

    def test_cleanup_does_not_touch_another_session(self):
        token = "ntru-unrelated-sentinel-" + uuid.uuid4().hex
        sentinel = subprocess.Popen(
            [sys.executable, "-S", "-B", "-c", "import time;time.sleep(10)", token],
            start_new_session=True,
        )
        try:
            result = self.run_fixture("import time;time.sleep(10)", work_seconds=.1)
            self.assertIsNone(sentinel.poll())
            self.assertNotIn(sentinel.pid, [row["pid"] for row in result["observed_processes"]])
            self.assertNotIn(sentinel.pid, [row["pid"] for row in result["signals"]])
        finally:
            sentinel.kill()
            sentinel.wait(timeout=1)

    def test_process_group_preserves_supervisor_session(self):
        inherited_session = os.getsid(0)
        result = self.run_fixture("import time;time.sleep(10)", work_seconds=.1, ownership="process_group")
        self.assertEqual("timeout", result["execution_status"])
        self.assertEqual("process_group", result["ownership"])
        self.assertEqual(inherited_session, result["session_id"])
        self.assertEqual(result["pid"], result["process_group_id"])
        self.assertNotEqual(os.getpgrp(), result["process_group_id"])
        self.assertIn("process group", result["memory"]["scope"])

    def test_outer_session_kills_blocked_owner_and_nested_process_group(self):
        with tempfile.TemporaryDirectory(prefix="ntru-nested-supervision-") as directory:
            ready = str(Path(directory) / "child.ready")
            child = (
                "import os,signal,time;from pathlib import Path;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                f"Path({ready!r}).write_text(str(os.getpid()));"
                "time.sleep(10)"
            )
            owner = (
                "import sys,time\n"
                "from experiments.ntru_hybrid_comparison.supervisor import supervise_command\n"
                "def block(event, context):\n"
                "    if event == 'after_spawn': time.sleep(10)\n"
                "now=time.monotonic()\n"
                f"supervise_command([sys.executable,'-S','-B','-c',{child!r},sys.argv[1]], "
                "work_deadline=now+5, hard_deadline=now+6, ownership='process_group', fault_hook=block)\n"
            )
            result = self.run_fixture(owner, work_seconds=.35, reserve=.65)
            self.assertTrue(Path(ready).is_file(), "nested worker did not initialize the TERM-ignore fixture")
            child_pid = int(Path(ready).read_text())
            self.assertEqual("timeout", result["execution_status"])
            children = [row for row in result["observed_processes"] if row["pid"] == child_pid]
            self.assertEqual(1, len(children))
            self.assertEqual(result["session_id"], children[0]["session_id"])
            self.assertEqual(child_pid, children[0]["pgid"])
            self.assertNotEqual(result["process_group_id"], children[0]["pgid"])
            self.assertTrue(any(row["pid"] == child_pid and row["signal"] == signal.SIGKILL for row in result["signals"]))
            self.assertFalse(result["run_success"])

    def test_parent_exits_on_term_but_grandchild_ignores_term(self):
        # Real parent -> child -> grandchild.  Parent and child respond to TERM;
        # the grandchild deliberately requires KILL.  All remain in our session.
        grandchild = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)"
        child = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-B','-c',{grandchild!r},sys.argv[1]]);"
            "time.sleep(10)"
        )
        parent = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-B','-c',{child!r},sys.argv[1]]);"
            "time.sleep(10)"
        )
        result = self.run_fixture(parent, work_seconds=0.25)
        self.assertEqual("timeout", result["execution_status"])
        self.assertGreaterEqual(len(result["observed_processes"]), 3)
        self.assertTrue(any(row["signal"] == signal.SIGKILL for row in result["signals"]))
        self.assertFalse(result["run_success"])

    def test_parent_early_exit_is_anomaly_even_after_successful_cleanup(self):
        grandchild = "import time;time.sleep(10)"
        child = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-B','-c',{grandchild!r},sys.argv[1]]);"
            "time.sleep(10)"
        )
        parent = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-B','-c',{child!r},sys.argv[1]]);"
            "time.sleep(.12)"
        )
        result = self.run_fixture(parent, work_seconds=0.6)
        self.assertEqual(0, result["returncode"])
        self.assertEqual("orphaned_descendants", result["execution_status"])
        self.assertEqual("parent_exited_with_live_descendants", result["termination_reason"])
        self.assertGreaterEqual(len(result["observed_processes"]), 3)
        self.assertFalse(result["run_success"])

    def test_worker_exception_nonzero_is_retained(self):
        result = self.run_fixture("raise RuntimeError('explicit worker fault')")
        self.assertEqual("worker_nonzero_exit", result["execution_status"])
        self.assertNotEqual(0, result["returncode"])
        self.assertFalse(result["run_success"])

    def test_worker_nonzero_without_python_exception(self):
        result = self.run_fixture("raise SystemExit(23)")
        self.assertEqual("worker_nonzero_exit", result["execution_status"])
        self.assertEqual(23, result["returncode"])
        self.assertFalse(result["run_success"])

    def test_supervisor_exception_enters_cleanup(self):
        def fail(event, context):
            if event == "after_spawn":
                raise RuntimeError("supervisor test injected exception")

        result = self.run_fixture("import time;time.sleep(10)", fault_hook=fail)
        self.assertEqual("supervisor_exception", result["execution_status"])
        self.assertEqual("RuntimeError", result["supervisor_errors"][0]["type"])
        self.assertFalse(result["run_success"])

    def test_supervisor_interrupt_enters_cleanup(self):
        def fail(event, context):
            if event == "after_spawn":
                raise KeyboardInterrupt("supervisor test interruption")

        result = self.run_fixture("import time;time.sleep(10)", fault_hook=fail)
        self.assertEqual("supervisor_interrupted", result["execution_status"])
        self.assertFalse(result["run_success"])

    def test_expired_work_budget_does_not_spawn(self):
        start = time.monotonic()
        result = supervise_command(
            [sys.executable, "-B", "-c", "raise RuntimeError('must not launch')"],
            work_deadline=start - 0.1, hard_deadline=start + 0.5,
        )
        self.assertIsNone(result["pid"])
        self.assertEqual("not_started_budget_exhausted", result["execution_status"])
        self.assertTrue(result["cleanup_verified"])
        self.assertIsNone(result["memory"]["value"])

    def test_no_cleanup_reserve_is_rejected(self):
        now = time.monotonic()
        with self.assertRaises(ValueError):
            supervise_command([sys.executable], work_deadline=now + 1, hard_deadline=now + 1)

    def test_output_files_and_explicit_memory_scope(self):
        with tempfile.TemporaryDirectory(prefix="ntru-supervisor-output-") as directory:
            stdout = Path(directory) / "stdout"
            stderr = Path(directory) / "stderr"
            start = time.monotonic()
            result = supervise_command(
                [sys.executable, "-B", "-c", "import sys,time;print('ok');print('error-stream',file=sys.stderr);time.sleep(.03)"],
                work_deadline=start + .4, hard_deadline=start + 1,
                stdout_path=stdout, stderr_path=stderr,
            )
            self.assertEqual("ok\n", stdout.read_text())
            self.assertEqual("error-stream\n", stderr.read_text())
            self.assertTrue(result["cleanup_verified"])
            self.assertFalse(result["memory"]["includes_supervisor"])
            self.assertFalse(result["memory"]["strict_memory_limit"])
            self.assertIsNone(result["memory"]["enforcement_backend"])
            self.assertGreater(result["memory"]["sample_count"], 0)

    def test_memory_high_then_low_new_trials_do_not_inherit_peak(self):
        def allocation(mebibytes):
            return (
                f"import time;data=bytearray({mebibytes}*1024*1024);"
                "data[::4096]=b'x'*len(data[::4096]);time.sleep(.18)"
            )

        high = self.run_fixture(allocation(40), work_seconds=.7)
        low = self.run_fixture(allocation(4), work_seconds=.7)
        self.assertEqual("completed", high["execution_status"])
        self.assertEqual("completed", low["execution_status"])
        high_peak, low_peak = high["memory"]["value"], low["memory"]["value"]
        self.assertGreater(high_peak, 35 * 1024 * 1024)
        self.assertLess(low_peak, high_peak - 20 * 1024 * 1024)
        self.assertNotEqual(high["pid"], low["pid"])

    def test_memory_sums_processes_in_same_sweep_without_threads(self):
        child = "import time;data=bytearray(8*1024*1024);time.sleep(.18)"
        parent = (
            "import subprocess,sys,time;data=bytearray(8*1024*1024);"
            f"p=subprocess.Popen([sys.executable,'-B','-c',{child!r},sys.argv[1]]);"
            "p.wait()"
        )
        result = self.run_fixture(parent, work_seconds=.7)
        self.assertEqual("completed", result["execution_status"])
        self.assertGreaterEqual(result["memory"]["process_count_at_peak"], 2)
        self.assertGreater(result["memory"]["value"], 20 * 1024 * 1024)

    def test_environment_probe_is_read_only_and_declares_limit(self):
        probe = probe_environment()
        self.assertEqual("linux_session_proc_identity", probe["selected_backend"])
        self.assertIn("no guarantee", probe["containment"])
        self.assertFalse(probe["strict_memory_limit"])


if __name__ == "__main__":
    unittest.main()
