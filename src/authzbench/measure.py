from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import cycle

from authzbench.dataset import RevocationScenario
from authzbench.engine import Checker, Consistency, Writer
from authzbench.relationships import Change, CheckQuery
from authzbench.timing import NANOS_PER_SECOND, Clock, LatencyHistogram, LatencySummary, monotonic_ns, wait_until


class MeasurementError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenLoopResult:
    rate_per_second: int
    latency: LatencySummary
    schedule_lag: LatencySummary


@dataclass(frozen=True)
class RevocationResult:
    staleness_ns: int | None
    resolution_ns: int
    cap_ns: int

    @property
    def capped(self) -> bool:
        return self.staleness_ns is None


def service_time(checker: Checker, queries: Sequence[CheckQuery], consistency: Consistency, duration_ns: int,
                 clock: Clock = monotonic_ns) -> LatencySummary:
    """Back-to-back checks: the cost of one check on an otherwise idle engine."""
    histogram = LatencyHistogram()
    deadline = clock() + duration_ns
    for query in cycle(queries):
        started = clock()
        if started >= deadline:
            break
        checker.check(query, consistency)
        histogram.record(clock() - started)
    return histogram.summary()


def open_loop(checker: Checker, queries: Sequence[CheckQuery], consistency: Consistency, rate_per_second: int,
              duration_ns: int, clock: Clock = monotonic_ns) -> OpenLoopResult:
    """Checks issued on a fixed schedule, each timed from when it was due, so a stall is counted, not skipped."""
    latency = LatencyHistogram()
    lag = LatencyHistogram()
    interval_ns = NANOS_PER_SECOND / rate_per_second
    count = duration_ns * rate_per_second // NANOS_PER_SECOND
    first_due = clock()
    for index, query in zip(range(count), cycle(queries)):
        due = first_due + round(index * interval_ns)
        wait_until(due, clock)
        started = clock()
        checker.check(query, consistency)
        latency.record(clock() - due)
        lag.record(started - due)
    return OpenLoopResult(rate_per_second, latency.summary(), lag.summary())


def race_revocation(writer: Writer, checker: Checker, scenario: RevocationScenario, consistency: Consistency,
                    cap_ns: int, clock: Clock = monotonic_ns) -> RevocationResult:
    """Time from the revoke's acknowledgement to the first check, started after it, that denies.

    The resolution is the gap between that check's start and the one before it."""
    if not checker.check(scenario.probe, consistency):
        raise MeasurementError(f"{scenario.probe} is denied before the revoke")
    revision = writer.write(Change.remove(scenario.revoke))
    acknowledged = previous_start = clock()
    try:
        while True:
            started = clock()
            if started - acknowledged > cap_ns:
                return RevocationResult(None, started - previous_start, cap_ns)
            if not checker.check(scenario.probe, consistency, revision):
                return RevocationResult(started - acknowledged, started - previous_start, cap_ns)
            previous_start = started
    finally:
        writer.write(Change.add(scenario.revoke))
