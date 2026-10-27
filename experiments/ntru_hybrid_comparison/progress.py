"""External-target events, independent of decoders, references, and timing costs.

One submitted target has exactly one state. Candidate return events do not
terminate it: a decoder may return several candidates before its explicit
normal completion, error, or cancellation. An absent result leaves it pending.
"""

from copy import deepcopy
import json
import math
import time


class ProgressProtocolError(ValueError):
    """A target event cannot be reconciled with the frozen external ID list."""


def _identifier(value):
    return ((isinstance(value, int) and not isinstance(value, bool))
            or (isinstance(value, str) and bool(value)))


def _solution_token(solution_key):
    """Encode an exact, caller-defined solution identity without orbit merging."""
    if solution_key is None:
        raise ProgressProtocolError("public-valid candidates need an exact solution_key")
    try:
        return json.dumps(solution_key, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ProgressProtocolError("solution_key must be finite JSON-compatible data") from error


class TargetProgress:
    """Record actual method events in frozen target order.

    The injected clock must return monotonic finite seconds. All event times
    are relative to construction and are diagnostic timestamps; no event time
    is a phase duration to add to decoding costs. Keys supplied for valid
    solutions define exact uniqueness, normally the complete ``[f, g]`` pair.
    This class does not independently validate candidates or infer an identity
    from a return position, projection group, or method name.
    """

    def __init__(self, external_ids, *, clock=time.monotonic):
        if not isinstance(external_ids, (list, tuple)):
            raise ProgressProtocolError("external_ids must be an explicit ordered list or tuple")
        if any(not _identifier(value) for value in external_ids):
            raise ProgressProtocolError("external IDs must be nonbool integers or nonempty strings")
        if len({isinstance(value, str) for value in external_ids}) > 1:
            raise ProgressProtocolError("external ID types must be homogeneous")
        if len(set(external_ids)) != len(external_ids):
            raise ProgressProtocolError("external IDs must be unique")
        if not callable(clock):
            raise ProgressProtocolError("clock must be callable")
        self._clock = clock
        self._started = self._read_clock()
        self._last_time = self._started
        self._targets = {
            external_id: {"external_id": external_id, "state": "unsubmitted",
                          "returned_candidate_count": 0,
                          "public_valid_candidate_count": 0}
            for external_id in external_ids
        }
        self._events = []
        self._solution_keys = set()
        self._first_valid_seconds = None

    def _read_clock(self):
        try:
            value = self._clock()
        except Exception as error:
            raise ProgressProtocolError("clock failed") from error
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ProgressProtocolError("clock must return finite seconds")
        return value

    def _timestamp(self):
        current = self._read_clock()
        if current < self._last_time:
            raise ProgressProtocolError("clock moved backwards")
        self._last_time = current
        return current - self._started

    def _target(self, external_id):
        if not _identifier(external_id) or external_id not in self._targets:
            raise ProgressProtocolError("unknown external target ID")
        return self._targets[external_id]

    def _pending(self, external_id):
        target = self._target(external_id)
        if target["state"] != "pending":
            raise ProgressProtocolError("event requires a submitted, nonterminal target")
        return target

    def _append(self, kind, external_id, seconds, **fields):
        event = {"sequence": len(self._events), "event": kind,
                 "external_id": external_id, "elapsed_seconds": seconds, **fields}
        self._events.append(event)
        return deepcopy(event)

    def submit(self, external_id):
        target = self._target(external_id)
        if target["state"] != "unsubmitted":
            raise ProgressProtocolError("target was already submitted")
        seconds = self._timestamp()
        target["state"] = "pending"
        return self._append("submitted", external_id, seconds)

    def add_candidate(self, external_id, *, public_valid, solution_key=None,
                      origin="unknown"):
        target = self._pending(external_id)
        if not isinstance(public_valid, bool):
            raise ProgressProtocolError("public_valid must be an actual boolean")
        if not isinstance(origin, str) or not origin:
            raise ProgressProtocolError("origin must be a nonempty string")
        token = _solution_token(solution_key) if public_valid else None
        seconds = self._timestamp()
        target["returned_candidate_count"] += 1
        if public_valid:
            target["public_valid_candidate_count"] += 1
            self._solution_keys.add(token)
            if self._first_valid_seconds is None:
                self._first_valid_seconds = seconds
        return self._append("candidate_returned", external_id, seconds,
                            public_valid=public_valid, output_origin=origin,
                            solution_key=(json.loads(token) if token is not None else None))

    def _terminate(self, external_id, state, reason=None):
        target = self._pending(external_id)
        if reason is not None and not isinstance(reason, str):
            raise ProgressProtocolError("termination reason must be a string or None")
        seconds = self._timestamp()
        target["state"] = state
        return self._append(state, external_id, seconds, reason=reason)

    def complete(self, external_id):
        """A normal result, including no candidate or only rejected candidates."""
        return self._terminate(external_id, "completed")

    def error(self, external_id, reason=None):
        return self._terminate(external_id, "errored", reason)

    def cancel(self, external_id, reason=None):
        return self._terminate(external_id, "cancelled", reason)

    @property
    def events(self):
        return deepcopy(self._events)

    def snapshot(self):
        """Copy current facts; does not submit, finish, fail, or cancel anything."""
        states = {state: 0 for state in
                  ("unsubmitted", "pending", "completed", "errored", "cancelled")}
        returned = valid = valid_targets = 0
        for target in self._targets.values():
            states[target["state"]] += 1
            returned += target["returned_candidate_count"]
            valid += target["public_valid_candidate_count"]
            valid_targets += bool(target["public_valid_candidate_count"])
        submitted = len(self._targets) - states["unsubmitted"]
        conservation = submitted == sum(states[state] for state in
                                        ("pending", "completed", "errored", "cancelled"))
        if not conservation:
            raise AssertionError("external-target state conservation failed")
        return {"schema": "ntru-external-target-progress-v1",
                "scope": "external_target_events_only",
                "target_count": len(self._targets), "submitted_count": submitted,
                "completed_count": states["completed"],
                "errored_count": states["errored"],
                "cancelled_count": states["cancelled"],
                "pending_count": states["pending"],
                "unsubmitted_count": states["unsubmitted"],
                "returned_candidate_count": returned,
                "public_valid_candidate_count": valid,
                "public_valid_target_count": valid_targets,
                "unique_public_valid_solution_count": len(self._solution_keys),
                "solution_uniqueness": "exact_caller_supplied_full_solution_key",
                "first_public_valid_seconds": self._first_valid_seconds,
                "conservation_holds": conservation,
                "targets": deepcopy(list(self._targets.values())),
                "events": self.events}
