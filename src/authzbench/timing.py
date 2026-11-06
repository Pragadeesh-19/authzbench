from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from hdrh.histogram import HdrHistogram

NANOS_PER_SECOND = 1_000_000_000
_SPIN_BEFORE_DEADLINE_NS = 2_000_000
_LOWEST_NS = 1
_HIGHEST_NS = 600 * NANOS_PER_SECOND
_SIGNIFICANT_DIGITS = 3


class Clock(Protocol):
    def now(self) -> int: ...

    def wait_until(self, deadline_ns: int) -> None: ...


class SystemClock:
    """The process's monotonic clock; waits sleep until shortly before the deadline and spin the rest."""

    def now(self) -> int:
        return time.perf_counter_ns()

    def wait_until(self, deadline_ns: int) -> None:
        while True:
            remaining = deadline_ns - self.now()
            if remaining <= 0:
                return
            if remaining > _SPIN_BEFORE_DEADLINE_NS:
                time.sleep((remaining - _SPIN_BEFORE_DEADLINE_NS) / NANOS_PER_SECOND)


SYSTEM_CLOCK = SystemClock()


@dataclass(frozen=True)
class LatencySummary:
    count: int
    p50_ns: int
    p99_ns: int
    p999_ns: int
    max_ns: int


class LatencyHistogram:
    def __init__(self) -> None:
        self._histogram = HdrHistogram(_LOWEST_NS, _HIGHEST_NS, _SIGNIFICANT_DIGITS)

    def record(self, value_ns: int) -> None:
        if not 0 <= value_ns <= _HIGHEST_NS:
            raise ValueError(f"{value_ns} ns is outside the recordable range")
        self._histogram.record_value(max(value_ns, _LOWEST_NS))

    def summary(self) -> LatencySummary:
        return LatencySummary(
            count=self._histogram.get_total_count(),
            p50_ns=self._histogram.get_value_at_percentile(50),
            p99_ns=self._histogram.get_value_at_percentile(99),
            p999_ns=self._histogram.get_value_at_percentile(99.9),
            max_ns=self._histogram.get_max_value(),
        )
