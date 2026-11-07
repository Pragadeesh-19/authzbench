from __future__ import annotations

from collections.abc import Sequence
import math
from dataclasses import dataclass
from itertools import cycle

from authzbench.dataset import RevocationScenario
from authzbench.engine import Checker, Consistency, Writer
from authzbench.relationships import Change, CheckQuery
from authzbench.timing import NANOS_PER_SECOND, SYSTEM_CLOCK, Clock, LatencyHistogram, LatencySummary

CHECKS_IN_FLIGHT = 1


class MeasurementError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenLoopResult:
    rate_per_second: int
    in_flight: int
    latency: LatencySummary
    schedule_lag: LatencySummary


@dataclass(frozen=True)
class RevocationResult:
    staleness_ns: int | None
    resolution_ns: int
    write_ns: int
    cap_ns: int
    denied_by_ns: int | None

    @property
    def capped(self) -> bool:
        return self.staleness_ns is None


@dataclass(frozen=True)
class TrialSpread:
    count: int
    p50_ns: int
    p99_ns: int
    max_ns: int

    @classmethod
    def of(cls, values: Sequence[int]) -> TrialSpread | None:
        if not values:
            return None
        ordered = sorted(values)
        return cls(len(ordered), _nearest_rank(ordered, 0.50), _nearest_rank(ordered, 0.99), ordered[-1])


def _nearest_rank(ordered: Sequence[int], quantile: float) -> int:
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


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
    """Checks issued on a fixed schedule, each timed from when it was due, so a stall is counted, not skipped.

    Each check runs on the calling thread, as an application calls an embedded engine, so one is in flight."""
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
    return OpenLoopResult(rate_per_second, CHECKS_IN_FLIGHT, latency.summary(), lag.summary())


def race_revocation(writer: Writer, checker: Checker, scenario: RevocationScenario, consistency: Consistency,
                    cap_ns: int, clock: Clock = SYSTEM_CLOCK) -> RevocationResult:
    """Time from the revoke's acknowledgement to the first check, started after it, that denies.

    The race starts once the probe is allowed at the given consistency, waiting at most the cap for an earlier
    race's restore to become visible. The resolution is the gap between the denying check's start and the one
    before it; denied-by runs from the acknowledgement until that check returned, so a check that waits inside the
    call for the revoke is charged its wait; the write time runs from issuing the revoke to its acknowledgement."""
    _await_grant(checker, scenario.probe, consistency, cap_ns, clock)
    issued = clock.now()
    revision = writer.write(Change.remove(scenario.revoke))
    acknowledged = previous_start = clock.now()
    write_ns = acknowledged - issued
    try:
        while True:
            started = clock.now()
            if started - acknowledged > cap_ns:
                return RevocationResult(None, started - previous_start, write_ns, cap_ns, None)
            if not checker.check(scenario.probe, consistency, revision):
                return RevocationResult(started - acknowledged, started - previous_start, write_ns, cap_ns,
                                        clock.now() - acknowledged)
            previous_start = started
    finally:
        writer.write(Change.add(scenario.revoke))


def race_revocations(writer: Writer, checker: Checker, scenario: RevocationScenario, consistency: Consistency,
                     cap_ns: int, trials: int, clock: Clock = SYSTEM_CLOCK) -> tuple[RevocationResult, ...]:
    """The revocation race run up to trials times in a row, each waiting for the previous restore to be visible.

    A capped trial ends the run: its revoke was not seen within the cap, so no further trial can say more than
    "> cap", and its restore may take as long to appear."""
    if trials < 1:
        raise MeasurementError(f"a revocation needs at least one trial, not {trials}")
    results: list[RevocationResult] = []
    for _ in range(trials):
        results.append(race_revocation(writer, checker, scenario, consistency, cap_ns, clock))
        if results[-1].capped:
            break
    return tuple(results)


def _await_grant(checker: Checker, probe: CheckQuery, consistency: Consistency, cap_ns: int, clock: Clock) -> None:
    deadline = clock.now() + cap_ns
    while not checker.check(probe, consistency):
        if clock.now() > deadline:
            raise MeasurementError(f"{probe} is denied before the revoke for longer than the cap")
