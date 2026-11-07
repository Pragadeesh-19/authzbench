from __future__ import annotations

from dataclasses import dataclass

from authzbench.dataset import Dataset
from authzbench.engine import Checker, Consistency, Engine
from authzbench.engines.noop import NoopChecker
from authzbench.measure import OpenLoopResult, RevocationResult, open_loop, race_revocations, service_time
from authzbench.relationships import Expectation
from authzbench.timing import NANOS_PER_SECOND, LatencySummary

IN_PROCESS = "in_process"
PLACEMENTS = (IN_PROCESS, "same_machine", "second_machine")


class ConformanceError(RuntimeError):
    def __init__(self, mismatches: list[Expectation]) -> None:
        listed = "; ".join(map(str, mismatches[:5]))
        super().__init__(f"{len(mismatches)} checks disagree with the reference evaluator, first: {listed}")
        self.mismatches = mismatches


@dataclass(frozen=True)
class RunPlan:
    service_time_ns: int = 5 * NANOS_PER_SECOND
    open_loop_rates: tuple[int, ...] = (1_000, 10_000)
    open_loop_ns: int = 10 * NANOS_PER_SECOND
    revocation_cap_ns: int = 30 * NANOS_PER_SECOND
    revocation_trials: int = 30
    placement: str = IN_PROCESS

    def __post_init__(self) -> None:
        if self.placement not in PLACEMENTS:
            raise ValueError(f"placement must be one of {', '.join(PLACEMENTS)}, not {self.placement!r}")
        if self.revocation_trials < 1:
            raise ValueError(f"a revocation needs at least one trial, not {self.revocation_trials}")


@dataclass(frozen=True)
class ModeResult:
    consistency: Consistency
    mode: str
    service_time: LatencySummary
    open_loop: tuple[OpenLoopResult, ...]
    revocations: tuple[RevocationResult, ...]
    revocation_trials: int
    placement: str


@dataclass(frozen=True)
class RunResult:
    engine: str
    engine_version: str
    dataset: Dataset
    conformance_checks: int
    driver_baseline: tuple[OpenLoopResult, ...]
    modes: tuple[ModeResult, ...]


def verify_conformance(checker: Checker, dataset: Dataset) -> int:
    expectations = (*dataset.expectations, dataset.nesting)
    mismatches = [expectation for expectation in expectations
                  if checker.check(expectation.query, Consistency.FRESH) is not expectation.allowed]
    if mismatches:
        raise ConformanceError(mismatches)
    return len(expectations)


def run(engine: Engine, dataset: Dataset, plan: RunPlan) -> RunResult:
    engine.load(dataset)
    conformance_checks = verify_conformance(engine, dataset)
    queries = [expectation.query for expectation in dataset.expectations]
    driver_baseline = tuple(open_loop(NoopChecker(), queries, Consistency.FRESH, rate, plan.open_loop_ns)
                            for rate in plan.open_loop_rates)
    modes = tuple(
        ModeResult(
            consistency=consistency,
            mode=mode,
            service_time=service_time(engine, queries, consistency, plan.service_time_ns),
            open_loop=tuple(open_loop(engine, queries, consistency, rate, plan.open_loop_ns)
                            for rate in plan.open_loop_rates),
            revocations=race_revocations(engine, engine, dataset.revocation, consistency, plan.revocation_cap_ns,
                                         plan.revocation_trials),
            revocation_trials=plan.revocation_trials,
            placement=plan.placement,
        )
        for consistency, mode in engine.modes().items()
    )
    return RunResult(engine.name, engine.version, dataset, conformance_checks, driver_baseline, modes)
