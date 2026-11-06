from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path

from authzbench.benchmark_model import DOCUMENT_SHARING
from authzbench.dataset import SCALES, scale_named
from authzbench.dataset_files import read_dataset, write_dataset
from authzbench.emitters import EMITTERS
from authzbench.engine import Engine
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.results import environment, to_document, write_document
from authzbench.runner import RunPlan, run
from authzbench.timing import NANOS_PER_SECOND

_MILLIS = 1_000_000
DEMONSTRATION_PROFILE = CalibrationProfile(staleness_ns=500 * _MILLIS, extra_latency_ns=2 * _MILLIS)


def _nodus() -> Engine:
    from authzbench.engines.nodus import NodusEngine

    return NodusEngine.in_memory()


ENGINES: dict[str, Callable[[], Engine]] = {
    "nodus": _nodus,
    "calibration": lambda: CalibrationEngine(DEMONSTRATION_PROFILE),
}


def _emit(arguments: argparse.Namespace) -> None:
    arguments.out.mkdir(parents=True, exist_ok=True)
    for emitter in EMITTERS:
        (arguments.out / emitter.file_name).write_text(emitter.emit(DOCUMENT_SHARING), encoding="utf-8", newline="\n")


def _generate(arguments: argparse.Namespace) -> None:
    write_dataset(generate(scale_named(arguments.scale), arguments.seed), arguments.out)


def _run(arguments: argparse.Namespace) -> None:
    plan = RunPlan(
        service_time_ns=_seconds(arguments.service_seconds),
        open_loop_rates=tuple(arguments.rates),
        open_loop_ns=_seconds(arguments.open_loop_seconds),
        revocation_cap_ns=_seconds(arguments.revocation_cap_seconds),
    )
    engine = ENGINES[arguments.engine]()
    try:
        result = run(engine, read_dataset(arguments.dataset), plan)
    finally:
        engine.close()
    write_document(to_document(result, environment()), arguments.out)


def _seconds(value: float) -> int:
    return round(value * NANOS_PER_SECOND)


def _rates(text: str) -> list[int]:
    return [int(rate) for rate in text.split(",")]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="authzbench")
    commands = parser.add_subparsers(required=True)

    emit = commands.add_parser("emit", help="write every engine's schema, generated from the model")
    emit.add_argument("--out", type=Path, default=Path("generated"))
    emit.set_defaults(handler=_emit)

    generate_command = commands.add_parser("generate", help="write a seeded dataset")
    generate_command.add_argument("--scale", choices=sorted(SCALES), required=True)
    generate_command.add_argument("--seed", type=int, required=True)
    generate_command.add_argument("--out", type=Path, required=True)
    generate_command.set_defaults(handler=_generate)

    run_command = commands.add_parser("run", help="measure one engine on one dataset")
    run_command.add_argument("--engine", choices=sorted(ENGINES), required=True)
    run_command.add_argument("--dataset", type=Path, required=True)
    run_command.add_argument("--out", type=Path, required=True)
    run_command.add_argument("--rates", type=_rates, default=[1_000, 10_000])
    run_command.add_argument("--service-seconds", type=float, default=5.0)
    run_command.add_argument("--open-loop-seconds", type=float, default=10.0)
    run_command.add_argument("--revocation-cap-seconds", type=float, default=30.0)
    run_command.set_defaults(handler=_run)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = _parser().parse_args(argv)
    arguments.handler(arguments)
