from __future__ import annotations

from collections.abc import Callable, Sequence
import math
from dataclasses import dataclass
from itertools import cycle

from authzbench.dataset import DelegationScenario, RevocationScenario
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
class DelegationResult:
    """One delegation race: per leaf, the time from the revoke's acknowledgement to its first denying check.

    Capped when some leaf was still allowed at the cap; allowed_at_cap says how many. interval_ns is the least time
    between the starts of one leaf's checks."""

    leaf_staleness_ns: tuple[int, ...] | None
    leaf_denied_by_ns: tuple[int, ...] | None
    resolution_ns: int
    write_ns: int
    cap_ns: int
    allowed_at_cap: int
    interval_ns: int = 0

    @property
    def capped(self) -> bool:
        return self.leaf_staleness_ns is None

    @property
    def staleness_ns(self) -> int | None:
        return None if self.leaf_staleness_ns is None else max(self.leaf_staleness_ns)

    @property
    def denied_by_ns(self) -> int | None:
        return None if self.leaf_denied_by_ns is None else max(self.leaf_denied_by_ns)


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
    return _repeat(lambda: race_revocation(writer, checker, scenario, consistency, cap_ns, clock), trials)


def race_delegation(writer: Writer, checker: Checker, scenario: DelegationScenario, consistency: Consistency,
                    cap_ns: int, clock: Clock = SYSTEM_CLOCK, interval_ns: int = 0) -> DelegationResult:
    """Time from revoking the person's delegation to the first denying check of every leaf agent below it.

    Leaves are checked in turn, each until it denies, so a leaf's staleness runs from the acknowledgement to the start
    of its own first denying check, as in the revocation race. Every leaf is checked at once after the
    acknowledgement, and then no sooner than interval_ns after its previous check started. The race starts once every
    leaf is allowed, waiting at most the cap in all; the resolution is that of the last leaf to be denied."""
    probes = scenario.probes
    _await_grants(checker, probes, consistency, cap_ns, clock)
    issued = clock.now()
    revision = writer.write(Change.remove(scenario.revoke))
    acknowledged = clock.now()
    write_ns = acknowledged - issued
    previous_start = [acknowledged] * len(probes)
    due = [acknowledged] * len(probes)
    staleness = [0] * len(probes)
    denied_by = [0] * len(probes)
    resolution = [0] * len(probes)
    allowed = list(range(len(probes)))
    try:
        while allowed:
            still_allowed = []
            for leaf in allowed:
                clock.wait_until(due[leaf])
                started = clock.now()
                if started - acknowledged > cap_ns:
                    return DelegationResult(None, None, started - previous_start[leaf], write_ns, cap_ns,
                                            len(allowed), interval_ns)
                if checker.check(probes[leaf], consistency, revision):
                    previous_start[leaf], due[leaf] = started, started + interval_ns
                    still_allowed.append(leaf)
                else:
                    denied_by[leaf] = clock.now() - acknowledged
                    staleness[leaf] = started - acknowledged
                    resolution[leaf] = started - previous_start[leaf]
            allowed = still_allowed
        last = max(range(len(probes)), key=staleness.__getitem__)
        return DelegationResult(tuple(staleness), tuple(denied_by), resolution[last], write_ns, cap_ns, 0,
                                interval_ns)
    finally:
        writer.write(Change.add(scenario.revoke))


def race_delegations(writer: Writer, checker: Checker, scenario: DelegationScenario, consistency: Consistency,
                     cap_ns: int, trials: int, clock: Clock = SYSTEM_CLOCK,
                     interval_ns: int = 0) -> tuple[DelegationResult, ...]:
    """The delegation race run up to trials times in a row; a capped trial ends the run, as for revocations."""
    return _repeat(lambda: race_delegation(writer, checker, scenario, consistency, cap_ns, clock, interval_ns), trials)


def _repeat(race: Callable[[], RevocationResult | DelegationResult], trials: int) -> tuple:
    if trials < 1:
        raise MeasurementError(f"a revocation needs at least one trial, not {trials}")
    results = []
    for _ in range(trials):
        results.append(race())
        if results[-1].capped:
            break
    return tuple(results)


def _await_grant(checker: Checker, probe: CheckQuery, consistency: Consistency, cap_ns: int, clock: Clock) -> None:
    _await_grants(checker, (probe,), consistency, cap_ns, clock)


def _await_grants(checker: Checker, probes: Sequence[CheckQuery], consistency: Consistency, cap_ns: int,
                  clock: Clock) -> None:
    deadline = clock.now() + cap_ns
    for probe in probes:
        while not checker.check(probe, consistency):
            if clock.now() > deadline:
                raise MeasurementError(f"{probe} is denied before the revoke for longer than the cap")
