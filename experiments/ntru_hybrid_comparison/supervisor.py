"""Linux trial supervision with an absolute deadline and sampled process RSS.

Each invocation starts a new session by default, or an independent process
group inside an existing session for nested supervision.  Membership is
discovered from /proc and identified by (PID, start time), never by a process
name.  This is deliberately not cgroup containment: unobserved children that
detach from the chosen boundary can escape.  Production workers must not
detach or daemonize.
"""

from __future__ import annotations

import ctypes
import inspect
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import traceback
from typing import Callable, Mapping, Sequence


def _read_process(pid: int) -> dict | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        fields = raw[raw.rfind(")") + 2 :].split()
        return {
            "pid": pid,
            "state": fields[0],
            "ppid": int(fields[1]),
            "pgid": int(fields[2]),
            "session_id": int(fields[3]),
            "starttime_ticks": int(fields[19]),
            "rss_bytes": max(0, int(fields[21])) * os.sysconf("SC_PAGE_SIZE"),
        }
    except (OSError, ValueError, IndexError):
        return None


def _process_table() -> dict[int, dict]:
    table = {}
    with os.scandir("/proc") as entries:
        for entry in entries:
            if entry.name.isdecimal():
                row = _read_process(int(entry.name))
                if row is not None:
                    table[row["pid"]] = row
    return table


def _enable_subreaper() -> dict:
    """Adopt orphan descendants without ever waitpid(-1)-reaping other work."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        current = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(current), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_GET_CHILD_SUBREAPER")
        if not current.value and libc.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
        return {
            "enabled": True,
            "already_enabled": bool(current.value),
            "scope": "supervisor process; persists for this process lifetime",
        }
    except (AttributeError, OSError) as exc:
        return {"enabled": False, "reason": str(exc)}


def probe_environment() -> dict:
    """Read-only capability evidence; do not create cgroups or call systemd."""
    cgroup = Path("/sys/fs/cgroup")
    return {
        "platform": os.uname().sysname,
        "proc_available": Path("/proc/self/stat").is_file(),
        "current_cgroup": Path("/proc/self/cgroup").read_text().strip(),
        "cgroup_root_writable": os.access(cgroup, os.W_OK),
        "selected_backend": "linux_session_proc_identity",
        "supported_ownership": ["session", "process_group"],
        "popen_process_group_keyword_available": "process_group" in inspect.signature(subprocess.Popen).parameters,
        "process_group_fallback": "preexec_fn=os.setpgrp, accepted only in a single-threaded supervisor",
        "pidfd_available": hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"),
        "strict_memory_limit": False,
        "containment": "session members and observed descendants; no guarantee for unobserved setsid escape",
    }


def supervise_command(
    argv: Sequence[str],
    *,
    work_deadline: float,
    hard_deadline: float,
    cwd: str | os.PathLike | None = None,
    env: Mapping[str, str] | None = None,
    stdout_path: str | os.PathLike | None = None,
    stderr_path: str | os.PathLike | None = None,
    sample_interval: float = 0.01,
    poll_interval: float = 0.01,
    fault_hook: Callable[[str, dict], None] | None = None,
    ownership: str = "session",
) -> dict:
    """Run one independently owned worker, returning failures as durable facts.

    Deadlines are absolute ``time.monotonic()`` values supplied by the caller.
    The interval between them is the caller's cleanup reserve, not extra time.
    ``fault_hook(event, context)`` is a test-only fault injection interface.
    Interrupts and supervisor faults are recorded, then enter bounded cleanup.
    Normal exit with live descendants is abnormal even if cleanup succeeds.
    ``ownership='process_group'`` preserves an enclosing owner session, letting
    that outer session supervisor also terminate nested workers.  On Python
    without Popen.process_group this requires a single-threaded supervisor.
    """
    started = time.monotonic()
    if not argv or isinstance(argv, (str, bytes)) or any(not isinstance(x, str) for x in argv):
        raise ValueError("argv must be a nonempty sequence of strings")
    if ownership not in ("session", "process_group"):
        raise ValueError("ownership must be session or process_group")
    group_keyword = "process_group" in inspect.signature(subprocess.Popen).parameters
    if ownership == "process_group" and not group_keyword:
        # Python's threading registry does not include native backend threads.
        # The /proc task count must also establish a single-threaded owner.
        if threading.active_count() != 1 or len(os.listdir("/proc/self/task")) != 1:
            raise ValueError("process_group fallback requires a single-threaded supervisor")
    for label, value in (("work_deadline", work_deadline), ("hard_deadline", hard_deadline)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label} must be a finite monotonic timestamp")
    if work_deadline >= hard_deadline:
        raise ValueError("work_deadline must precede hard_deadline (cleanup reserve required)")
    for interval in (sample_interval, poll_interval):
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("sampling and polling intervals must be positive and finite")

    proc = None
    sid = None
    pgid = None
    tracked: dict[tuple[int, int], dict] = {}
    pidfds: dict[tuple[int, int], int] = {}
    output_handles = []
    signal_log = []
    faults = []
    samples = 0
    peak_rss = None
    peak_process_count = 0
    largest_sweep_seconds = 0.0
    last_sample = float("-inf")
    peak_sample_offset = None
    subreaper = {"enabled": False, "reason": "worker not started"}
    status = "not_started_budget_exhausted"
    reason = "work_deadline_reached_before_launch"
    work_finished = started
    cleanup_started = started
    cleanup_verified = False
    residual = []
    reaped = []
    cleanup_error = None

    def observe(*, force_sample: bool = False) -> list[dict]:
        nonlocal samples, peak_rss, peak_process_count, largest_sweep_seconds
        nonlocal last_sample, peak_sample_offset
        begin = time.monotonic()
        table = _process_table()
        owned = {
            pid for pid, row in table.items()
            if (sid is not None and ownership == "session" and row["session_id"] == sid)
            or (pgid is not None and ownership == "process_group" and row["pgid"] == pgid)
        }
        # Preserve observed ownership if a descendant changes session/group.
        owned.update(
            pid for (pid, ticks) in tracked
            if pid in table and table[pid]["starttime_ticks"] == ticks
        )
        changed = True
        while changed:
            children = {pid for pid, row in table.items() if row["ppid"] in owned}
            changed = bool(children - owned)
            owned.update(children)
        rows = [table[pid] for pid in sorted(owned)]
        for row in rows:
            key = row["pid"], row["starttime_ticks"]
            if key not in tracked:
                tracked[key] = {
                    **row,
                    "first_observed_process_group_id": row["pgid"],
                    "first_observed_session_id": row["session_id"],
                    "first_observed_monotonic": begin,
                }
                if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
                    try:
                        fd = os.pidfd_open(row["pid"], 0)
                        # Verify the PID was not reused while opening the fd.
                        after = _read_process(row["pid"])
                        if after and after["starttime_ticks"] == key[1]:
                            pidfds[key] = fd
                        else:
                            os.close(fd)
                    except OSError:
                        pass
            # An outer observer may see a fork before the child's setpgrp.
            # Keep both initial and latest membership rather than freezing
            # that transient inherited group as the final process evidence.
            tracked[key].update(row)
            tracked[key]["last_observed_monotonic"] = time.monotonic()
        ended = time.monotonic()
        if sid is not None and (force_sample or ended - last_sample >= sample_interval):
            active = [row for row in rows if row["state"] not in ("Z", "X")]
            rss = sum(row["rss_bytes"] for row in active)
            samples += 1
            last_sample = ended
            largest_sweep_seconds = max(largest_sweep_seconds, ended - begin)
            if peak_rss is None or rss > peak_rss:
                peak_rss = rss
                peak_process_count = len(active)
                peak_sample_offset = ended - started
        return [row for row in rows if row["state"] not in ("Z", "X")]

    def send(rows: list[dict], sig: int) -> None:
        for row in rows:
            key = row["pid"], row["starttime_ticks"]
            actual = _read_process(row["pid"])
            if actual is None or actual["starttime_ticks"] != key[1] or actual["state"] in ("Z", "X"):
                continue
            try:
                if key in pidfds:
                    signal.pidfd_send_signal(pidfds[key], sig)
                else:
                    os.kill(row["pid"], sig)
                signal_log.append({"pid": key[0], "starttime_ticks": key[1], "signal": sig, "at_seconds": time.monotonic() - started})
            except ProcessLookupError:
                pass
            except OSError as exc:
                faults.append({"phase": "signal", "pid": key[0], "error": repr(exc)})

    def reap_owned() -> None:
        if proc is not None:
            proc.poll()  # subprocess owns its direct child's wait status.
        for pid, ticks in tuple(tracked):
            if proc is not None and pid == proc.pid:
                continue
            current = _read_process(pid)
            if current is not None and current["starttime_ticks"] != ticks:
                continue
            try:
                got, wait_status = os.waitpid(pid, os.WNOHANG)
                if got:
                    reaped.append({"pid": pid, "starttime_ticks": ticks, "wait_status": wait_status})
            except (ChildProcessError, ProcessLookupError):
                pass

    try:
        if time.monotonic() < work_deadline:
            subreaper = _enable_subreaper()
            for path in (stdout_path, stderr_path):
                output_handles.append(open(path, "wb") if path is not None else open(os.devnull, "wb"))
            # This launch is charged to the same preexisting work deadline.
            if time.monotonic() >= work_deadline:
                reason = "work_deadline_reached_during_launch_preparation"
            else:
                launch_boundary = {"start_new_session": ownership == "session"}
                if ownership == "process_group":
                    if group_keyword:
                        launch_boundary["process_group"] = 0
                    else:
                        launch_boundary["preexec_fn"] = os.setpgrp
                proc = subprocess.Popen(
                    list(argv), cwd=cwd, env=None if env is None else dict(env),
                    stdin=subprocess.DEVNULL, stdout=output_handles[0], stderr=output_handles[1],
                    close_fds=True, **launch_boundary,
                )
                sid = proc.pid if ownership == "session" else os.getsid(0)
                pgid = proc.pid
                status = "running"
                reason = None
                observe(force_sample=True)
                if fault_hook:
                    fault_hook("after_spawn", {"pid": proc.pid, "session_id": sid, "process_group_id": pgid, "ownership": ownership})
                while True:
                    now = time.monotonic()
                    if now >= work_deadline:
                        status, reason = "timeout", "work_deadline_reached"
                        break
                    rows = observe()
                    returncode = proc.poll()
                    if time.monotonic() >= work_deadline:
                        # A sampling sweep is real budgeted work.  Completion
                        # first observed after the deadline is conservative
                        # timeout evidence, not an inferred earlier finish.
                        status, reason = "timeout", "work_deadline_reached"
                        break
                    if returncode is not None:
                        # Re-scan after observing exit; do not mistake leader exit
                        # for an empty trial boundary.
                        rows = observe(force_sample=True)
                        if rows:
                            status, reason = "orphaned_descendants", "parent_exited_with_live_descendants"
                        elif returncode == 0:
                            status, reason = "completed", "worker_exited_zero"
                        else:
                            status, reason = "worker_nonzero_exit", "worker_exited_nonzero"
                        break
                    if fault_hook:
                        fault_hook("monitor", {"pid": proc.pid, "session_id": sid, "process_group_id": pgid, "ownership": ownership})
                    time.sleep(min(poll_interval, max(0.0, work_deadline - time.monotonic())))
        work_finished = time.monotonic()
    except BaseException as exc:
        work_finished = time.monotonic()
        status = "supervisor_interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "supervisor_exception"
        reason = "supervision_interrupted" if status == "supervisor_interrupted" else "supervision_raised_exception"
        faults.append({"phase": "supervision", "type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        cleanup_started = time.monotonic()
        try:
            rows = observe(force_sample=True) if sid is not None else []
            if rows and status == "completed":
                status, reason = "orphaned_descendants", "parent_exited_with_live_descendants"
            term_sent = set()
            remaining = max(0.0, hard_deadline - cleanup_started)
            kill_at = cleanup_started + min(0.25, remaining * 0.4)
            while rows:
                now = time.monotonic()
                unseen = [row for row in rows if (row["pid"], row["starttime_ticks"]) not in term_sent]
                if unseen:
                    send(unseen, signal.SIGTERM)
                    term_sent.update((row["pid"], row["starttime_ticks"]) for row in unseen)
                if now >= kill_at:
                    send(rows, signal.SIGKILL)
                reap_owned()
                rows = observe(force_sample=True)
                if not rows or time.monotonic() >= hard_deadline:
                    break
                wake_at = min(hard_deadline, kill_at if now < kill_at else hard_deadline)
                time.sleep(min(poll_interval, max(0.0, wake_at - time.monotonic())))
            reap_owned()
            residual = observe(force_sample=True) if sid is not None else []
            cleanup_verified = not residual
        except BaseException as exc:
            cleanup_error = {"type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}
            # A final identity-limited, nonblocking KILL is best effort, never a
            # claim of verified cleanup when observation itself failed.
            try:
                send(list(tracked.values()), signal.SIGKILL)
                # The direct unreaped child cannot have its PID reused.  This
                # also covers a failure in the very first /proc observation.
                if proc is not None and proc.poll() is None:
                    proc.kill()
                reap_owned()
            except BaseException:
                pass
            cleanup_verified = False
        finally:
            for handle in output_handles:
                handle.close()
            for fd in pidfds.values():
                os.close(fd)

    finished = time.monotonic()
    execution_status = status
    if not cleanup_verified:
        execution_status = "cleanup_unverified" if cleanup_error else "cleanup_failed"
    return {
        "schema": "ntru-trial-supervision-v1",
        "pid": None if proc is None else proc.pid,
        "session_id": sid,
        "process_group_id": pgid,
        "ownership": ownership,
        "group_creation_backend": (
            "Popen(start_new_session=True)" if ownership == "session"
            else ("Popen(process_group=0)" if group_keyword else "Popen(preexec_fn=os.setpgrp), single-threaded supervisor")
        ),
        "returncode": None if proc is None else proc.poll(),
        "execution_status": execution_status,
        "execution_status_before_cleanup": status,
        "termination_reason": reason,
        "cleanup_verified": cleanup_verified,
        "run_success": status == "completed" and cleanup_verified and finished <= hard_deadline,
        "work_seconds": max(0.0, work_finished - started),
        "cleanup_seconds": max(0.0, finished - cleanup_started),
        "total_wall_seconds": finished - started,
        "started_monotonic": started,
        "finished_monotonic": finished,
        "work_deadline": work_deadline,
        "hard_deadline": hard_deadline,
        "work_deadline_from_start_seconds": work_deadline - started,
        "hard_deadline_from_start_seconds": hard_deadline - started,
        "hard_deadline_met": finished <= hard_deadline,
        "boundary": (
            "new Linux session; PID/starttime verified session members and observed descendants"
            if ownership == "session" else
            "new Linux process group in inherited session; PID/starttime verified group members and observed descendants"
        ),
        "containment_limitation": (
            "unobserved children that detach using setsid can escape; production worker must not daemonize"
            if ownership == "session" else
            "unobserved children that leave the process group can escape this inner boundary; inherited outer session still owns non-setsid descendants"
        ),
        "subreaper": subreaper,
        "signals": signal_log,
        "observed_processes": list(tracked.values()),
        "residual_active_processes": residual,
        "reaped_adopted_processes": reaped,
        "supervisor_errors": faults,
        "cleanup_error": cleanup_error,
        "memory": {
            "metric_name": "sampled_simultaneous_process_tree_rss_bytes",
            "value": peak_rss,
            "unit": "bytes",
            "scope": (
                "trial session and observed descendants, through cleanup; excludes supervisor"
                if ownership == "session" else
                "trial process group and observed descendants in inherited session, through cleanup; excludes supervisor"
            ),
            "backend": "Linux /proc/<pid>/stat RSS, one process entry per PID",
            "sample_interval_seconds": sample_interval,
            "sampling_estimate": True,
            "includes_supervisor": False,
            "covers_descendants": True,
            "descendant_scope_limitation": (
                "no guarantee for unobserved setsid escape" if ownership == "session" else
                "no guarantee for unobserved process-group escape; outer session ownership is preserved"
            ),
            "sample_count": samples,
            "peak_sample_offset_seconds": peak_sample_offset,
            "process_count_at_peak": peak_process_count,
            "maximum_sampling_sweep_seconds": largest_sweep_seconds,
            "rss_semantics": "sum within one sequential /proc sampling sweep, then maximum over sweeps; shared pages can be counted repeatedly; not exclusive physical memory",
            "not_atomic_snapshot": True,
            "enforcement_backend": None,
            "strict_memory_limit": False,
            "missing_reason": None if peak_rss is not None else "worker not launched or no successful sampling sweep",
        },
    }
