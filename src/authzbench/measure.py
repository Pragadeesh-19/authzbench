from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import cycle

from authzbench.dataset import RevocationScenario
from authzbench.engine import Checker, Consistency, Writer
from authzbench.relationships import Change, CheckQuery
from authzbench.timing import NANOS_PER_SECOND, SYSTEM_CLOCK, Clock, LatencyHistogram, LatencySummary


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
    write_ns: int
    cap_ns: int

    @property
    def capped(self) -> bool:
        return self.staleness_ns is None


def service_time(checker: Checker, queries: Sequence[CheckQuery], consistency: Consistency, duration_ns: int,
                 clock: Clock = SYSTEM_CLOCK) -> LatencySummary:
    """Back-to-back checks: the cost of one check on an otherwise idle engine."""
    histogram = LatencyHistogram()
    deadline = clock.now() + duration_ns
    for query in cycle(queries):
        started = clock.now()
        if started >= deadline:
            break
        checker.check(query, consistency)
        histogram.record(clock.now() - started)
    return histogram.summary()


def open_loop(checker: Checker, queries: Sequence[CheckQuery], consistency: Consistency, rate_per_second: int,
              duration_ns: int, clock: Clock = SYSTEM_CLOCK) -> OpenLoopResult:
    """Checks issued on a fixed schedule, each timed from when it was due, so a stall is counted, not skipped."""
    latency = LatencyHistogram()
    lag = LatencyHistogram()
    interval_ns = NANOS_PER_SECOND / rate_per_second
    count = duration_ns * rate_per_second // NANOS_PER_SECOND
    first_due = clock.now()
    for index, query in zip(range(count), cycle(queries)):
        due = first_due + round(index * interval_ns)
        clock.wait_until(due)
        started = clock.now()
        checker.check(query, consistency)
        latency.record(clock.now() - due)
        lag.record(started - due)
    return OpenLoopResult(rate_per_second, latency.summary(), lag.summary())


def race_revocation(writer: Writer, checker: Checker, scenario: RevocationScenario, consistency: Consistency,
                    cap_ns: int, clock: Clock = SYSTEM_CLOCK) -> RevocationResult:
    """Time from the revoke's acknowledgement to the first check, started after it, that denies.

    The resolution is the gap between that check's start and the one before it; the write time runs from issuing
    the revoke to its acknowledgement."""
    if not checker.check(scenario.probe, consistency):
        raise MeasurementError(f"{scenario.probe} is denied before the revoke")
    issued = clock.now()
    revision = writer.write(Change.remove(scenario.revoke))
    acknowledged = previous_start = clock.now()
    write_ns = acknowledged - issued
    try:
        while True:
            started = clock.now()
            if started - acknowledged > cap_ns:
                return RevocationResult(None, started - previous_start, write_ns, cap_ns)
            if not checker.check(scenario.probe, consistency, revision):
                return RevocationResult(started - acknowledged, started - previous_start, write_ns, cap_ns)
            previous_start = started
    finally:
        writer.write(Change.add(scenario.revoke))
