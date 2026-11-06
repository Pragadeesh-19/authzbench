from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from authzbench.dataset import Dataset
from authzbench.engine import Consistency, Revision
from authzbench.reference import ReferenceEvaluator, RelationshipStore
from authzbench.relationships import Change, CheckQuery
from authzbench.timing import SYSTEM_CLOCK, Clock


@dataclass(frozen=True)
class CalibrationProfile:
    """Behavior with known timing: writes become visible to fastest-mode checks after staleness_ns, every check
    takes at least extra_latency_ns, and every stall_period_ns the engine stops answering for stall_duration_ns."""

    staleness_ns: int = 0
    extra_latency_ns: int = 0
    stall_period_ns: int = 0
    stall_duration_ns: int = 0


class CalibrationEngine:
    name = "calibration"
    version = "1"

    def __init__(self, profile: CalibrationProfile, clock: Clock = SYSTEM_CLOCK) -> None:
        self._profile = profile
        self._clock = clock
        self._started = clock.now()
        self._store = RelationshipStore()
        self._evaluator: ReferenceEvaluator | None = None
        self._pending: deque[tuple[int, Change]] = deque()
        self._revision = 0

    def modes(self) -> dict[Consistency, str]:
        return {Consistency.FRESH: "fresh", Consistency.FASTEST: "stale"}

    def load(self, dataset: Dataset) -> None:
        self._store = RelationshipStore(dataset.relationships)
        self._evaluator = ReferenceEvaluator(dataset.model, self._store)

    def write(self, change: Change) -> Revision:
        self._pending.append((self._clock.now() + self._profile.staleness_ns, change))
        self._revision += 1
        return Revision(self._revision)

    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool:
        started = self._clock.now()
        self._wait_out_stall(started)
        self._apply_pending(visible_before=None if consistency is Consistency.FRESH else self._clock.now())
        allowed = self._require_loaded().check(query)
        self._clock.wait_until(started + self._profile.extra_latency_ns)
        return allowed

    def close(self) -> None:
        self._pending.clear()

    def _wait_out_stall(self, now: int) -> None:
        if self._profile.stall_period_ns == 0:
            return
        phase = (now - self._started) % self._profile.stall_period_ns
        if phase < self._profile.stall_duration_ns:
            self._clock.wait_until(now + self._profile.stall_duration_ns - phase)

    def _apply_pending(self, visible_before: int | None) -> None:
        while self._pending and (visible_before is None or self._pending[0][0] <= visible_before):
            self._store.apply(self._pending.popleft()[1])

    def _require_loaded(self) -> ReferenceEvaluator:
        if self._evaluator is None:
            raise RuntimeError("load a dataset before checking")
        return self._evaluator
