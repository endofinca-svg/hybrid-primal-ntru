"""Immutable absolute monotonic deadlines for one experimental trial.

Creating a new attempt never awards a fresh trial or experiment budget.  The
cleanup reserve is inside every applicable hard bound; it is not an extra grace
period.  Clock values are process-local monotonic seconds, never wall dates.
"""

from dataclasses import dataclass
import math
import time


def _seconds(value, name, *, nonnegative=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (nonnegative and value < 0)):
        raise ValueError(f"{name} must be finite {'nonnegative ' if nonnegative else ''}seconds")
    return float(value)


class BudgetExhausted(TimeoutError):
    """No new work may begin inside the remaining absolute work window."""

    def __init__(self, stage, *, remaining_seconds, required_seconds):
        self.stage = stage
        self.remaining_seconds = remaining_seconds
        self.required_seconds = required_seconds
        super().__init__(f"insufficient work budget before {stage}: "
                         f"remaining={remaining_seconds:.9f}s, required={required_seconds:.9f}s")


@dataclass(frozen=True)
class DeadlineWindow:
    started_at: float
    requested_budget_seconds: float
    cleanup_reserve_seconds: float
    work_deadline: float
    hard_deadline: float
    experiment_started_at: float
    experiment_hard_deadline: float
    trial_hard_deadline: float
    pattern_hard_deadline: float | None = None

    def __post_init__(self):
        for name in ("started_at", "work_deadline", "hard_deadline",
                     "experiment_started_at", "experiment_hard_deadline", "trial_hard_deadline"):
            _seconds(getattr(self, name), name)
        for name in ("requested_budget_seconds", "cleanup_reserve_seconds"):
            _seconds(getattr(self, name), name, nonnegative=True)
        if self.pattern_hard_deadline is not None:
            _seconds(self.pattern_hard_deadline, "pattern_hard_deadline")
        limits = [self.experiment_hard_deadline, self.trial_hard_deadline]
        if self.pattern_hard_deadline is not None:
            limits.append(self.pattern_hard_deadline)
        if self.hard_deadline > min(limits):
            raise ValueError("hard_deadline exceeds a containing deadline")
        if self.work_deadline > self.hard_deadline - self.cleanup_reserve_seconds:
            raise ValueError("work_deadline does not include the cleanup reserve")

    def remaining_work(self, now=None):
        now = _seconds(time.monotonic() if now is None else now, "now")
        return max(0.0, self.work_deadline - now)

    def remaining_hard(self, now=None):
        now = _seconds(time.monotonic() if now is None else now, "now")
        return max(0.0, self.hard_deadline - now)

    def can_start(self, *, now=None, minimum_work_seconds=0.0):
        minimum = _seconds(minimum_work_seconds, "minimum_work_seconds", nonnegative=True)
        remaining = self.remaining_work(now)
        return remaining > 0 and remaining >= minimum

    def require_work(self, stage, *, now=None, minimum_work_seconds=0.0):
        if not isinstance(stage, str) or not stage:
            raise ValueError("stage must be a nonempty string")
        minimum = _seconds(minimum_work_seconds, "minimum_work_seconds", nonnegative=True)
        remaining = self.remaining_work(now)
        if remaining <= 0 or remaining < minimum:
            raise BudgetExhausted(stage, remaining_seconds=remaining, required_seconds=minimum)
        return remaining

    def to_record(self):
        """Portable relative bounds; absolute values stay available to the worker."""
        return {
            "clock": "time.monotonic",
            "requested_budget_seconds": self.requested_budget_seconds,
            "cleanup_reserve_seconds": self.cleanup_reserve_seconds,
            "effective_hard_budget_seconds": max(0.0, self.hard_deadline - self.started_at),
            "work_deadline_relative_seconds": self.work_deadline - self.started_at,
            "hard_deadline_relative_seconds": self.hard_deadline - self.started_at,
            "experiment_hard_deadline_relative_seconds": self.experiment_hard_deadline - self.started_at,
            "trial_hard_deadline_relative_seconds": self.trial_hard_deadline - self.started_at,
            "pattern_hard_deadline_relative_seconds": (
                None if self.pattern_hard_deadline is None else self.pattern_hard_deadline - self.started_at),
            "cleanup_reserve_is_inside_budget": True,
        }


def create_trial_window(*, experiment_started_at, total_budget_seconds,
                        trial_started_at, trial_budget_seconds,
                        cleanup_reserve_seconds):
    """Freeze a trial against the original experiment and trial start times.

    A zero or already-expired budget is representable for truthful not-started
    records; ``can_start`` then returns false.  Loads, spawn time, reduction and
    attempts consume these same deadlines.
    """
    experiment_start = _seconds(experiment_started_at, "experiment_started_at")
    trial_start = _seconds(trial_started_at, "trial_started_at")
    if trial_start < experiment_start:
        raise ValueError("trial_started_at precedes experiment_started_at")
    total = _seconds(total_budget_seconds, "total_budget_seconds", nonnegative=True)
    trial = _seconds(trial_budget_seconds, "trial_budget_seconds", nonnegative=True)
    reserve = _seconds(cleanup_reserve_seconds, "cleanup_reserve_seconds", nonnegative=True)
    experiment_hard = experiment_start + total
    trial_hard = trial_start + trial
    hard = min(experiment_hard, trial_hard)
    return DeadlineWindow(trial_start, trial, reserve, hard - reserve, hard,
                          experiment_start, experiment_hard, trial_hard)


def cap_attempt(window, *, attempt_started_at, pattern_limit_seconds=None):
    """Tighten, never restart, a trial window for a pattern attempt.

    When a per-pattern bound exists it includes cleanup reserve too.  Even when
    the attempt starts late, its hard/work deadlines cannot exceed its parent.
    """
    if not isinstance(window, DeadlineWindow):
        raise ValueError("window must be a DeadlineWindow")
    started = _seconds(attempt_started_at, "attempt_started_at")
    if started < window.started_at:
        raise ValueError("attempt_started_at precedes its parent window")
    pattern_hard = window.pattern_hard_deadline
    if pattern_limit_seconds is not None:
        limit = _seconds(pattern_limit_seconds, "pattern_limit_seconds", nonnegative=True)
        current_limit = started + limit
        pattern_hard = current_limit if pattern_hard is None else min(pattern_hard, current_limit)
    hard = min(window.hard_deadline, pattern_hard) if pattern_hard is not None else window.hard_deadline
    work = min(window.work_deadline, hard - window.cleanup_reserve_seconds)
    return DeadlineWindow(started, window.requested_budget_seconds,
                          window.cleanup_reserve_seconds, work, hard,
                          window.experiment_started_at, window.experiment_hard_deadline,
                          window.trial_hard_deadline, pattern_hard)
