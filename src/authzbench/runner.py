from __future__ import annotations

from dataclasses import dataclass

from authzbench.dataset import Dataset
from authzbench.engine import Checker, Consistency, Engine
from authzbench.measure import OpenLoopResult, RevocationResult, open_loop, race_revocation, service_time
from authzbench.relationships import Expectation
from authzbench.timing import NANOS_PER_SECOND, LatencySummary

IN_PROCESS = "in_process"


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


@dataclass(frozen=True)
class ModeResult:
    consistency: Consistency
    mode: str
    service_time: LatencySummary
    open_loop: tuple[OpenLoopResult, ...]
    revocation: RevocationResult
    placement: str = IN_PROCESS


@dataclass(frozen=True)
class RunResult:
    engine: str
    engine_version: str
    dataset: Dataset
    conformance_checks: int
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
    modes = tuple(
        ModeResult(
            consistency=consistency,
            mode=mode,
            service_time=service_time(engine, queries, consistency, plan.service_time_ns),
            open_loop=tuple(open_loop(engine, queries, consistency, rate, plan.open_loop_ns)
                            for rate in plan.open_loop_rates),
            revocation=race_revocation(engine, engine, dataset.revocation, consistency, plan.revocation_cap_ns),
        )
        for consistency, mode in engine.modes().items()
    )
    return RunResult(engine.name, engine.version, dataset, conformance_checks, modes)
