from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from authzbench.benchmark_model import DOCUMENT_SHARING
from authzbench.dataset import SCALES, scale_named
from authzbench.dataset_files import read_dataset, write_dataset
from authzbench.emitters import EMITTERS
from authzbench.engine import Engine
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.remote import EngineServer, RemoteEngine, ServedEngine
from authzbench.results import environment, to_document, write_document
from authzbench.runner import IN_PROCESS, PLACEMENTS, RunPlan, run
from authzbench.timing import NANOS_PER_SECOND

_MILLIS = 1_000_000
DEMONSTRATION_PROFILE = CalibrationProfile(staleness_ns=500 * _MILLIS, extra_latency_ns=2 * _MILLIS)


@dataclass(frozen=True)
class EngineChoice:
    build: Callable[[argparse.Namespace], Engine]
    remote: bool


def _nodus(_: argparse.Namespace) -> Engine:
    from authzbench.engines.nodus import NodusEngine

    return NodusEngine.in_memory()


def _tokens(arguments: argparse.Namespace) -> Engine:
    from authzbench.engines.tokens import TokenEngine

    if arguments.revocation_cap_seconds <= arguments.token_lifetime_seconds:
        raise SystemExit(f"authzbench: a token lives {arguments.token_lifetime_seconds:g} s, so the revocation cap "
                         f"must be longer, not {arguments.revocation_cap_seconds:g} s")
    return TokenEngine(_seconds(arguments.token_lifetime_seconds))


def _nodus_follower(arguments: argparse.Namespace) -> Engine:
    import nodusdb

    from authzbench.engines.nodus_follower import NodusFollowerEngine

    settings = {"public_key_file": _required(arguments, "public_key"), "key_id": arguments.key_id,
                "read_wait_ms": round(1000 * (arguments.read_wait_seconds or arguments.revocation_cap_seconds))}
    if arguments.ship_directory is not None:
        follow = nodusdb.Follower.directory(arguments.ship_directory, **settings)
    else:
        follow = nodusdb.Follower.s3(**_s3_location(arguments), credentials=nodusdb.environment_credentials(),
                                     **settings)
    return NodusFollowerEngine(RemoteEngine(_required(arguments, "writer")), nodusdb.Graph(follow=follow))


def _nodus_writer(arguments: argparse.Namespace) -> ServedEngine:
    import nodusdb

    from authzbench.engines.nodus import NodusEngine

    key = _required(arguments, "signing_key")
    public_key = key.with_suffix(".pub")
    if not key.exists():
        nodusdb.generate_signing_key(key, public_key)
        print(f"made a signing key; copy {public_key} to the driver's machine")
    settings = {"key_file": key, "key_id": arguments.key_id, "public_key_file": public_key}
    if arguments.ship_directory is not None:
        shipping = nodusdb.Shipping.directory(arguments.ship_directory, **settings)
    else:
        shipping = nodusdb.Shipping.s3(**_s3_location(arguments), credentials=nodusdb.environment_credentials(),
                                       **settings)
    return NodusEngine.shipping(str(_required(arguments, "graph")), shipping)


ENGINES: dict[str, EngineChoice] = {
    "nodus": EngineChoice(_nodus, remote=False),
    "calibration": EngineChoice(lambda _: CalibrationEngine(DEMONSTRATION_PROFILE), remote=False),
    "tokens": EngineChoice(_tokens, remote=False),
    "nodus-follower": EngineChoice(_nodus_follower, remote=True),
    "remote-calibration": EngineChoice(lambda arguments: RemoteEngine(_required(arguments, "writer")), remote=True),
}

SERVED: dict[str, Callable[[argparse.Namespace], ServedEngine]] = {
    "nodus-writer": _nodus_writer,
    "calibration": lambda _: CalibrationEngine(DEMONSTRATION_PROFILE),
}


def _emit(arguments: argparse.Namespace) -> None:
    arguments.out.mkdir(parents=True, exist_ok=True)
    for emitter in EMITTERS:
        (arguments.out / emitter.file_name).write_text(emitter.emit(DOCUMENT_SHARING), encoding="utf-8", newline="\n")


def _generate(arguments: argparse.Namespace) -> None:
    write_dataset(generate(scale_named(arguments.scale), arguments.seed), arguments.out)


def _run(arguments: argparse.Namespace) -> None:
    choice = ENGINES[arguments.engine]
    if choice.remote == (arguments.placement == IN_PROCESS):
        where = "on another machine or process" if choice.remote else "in this process"
        raise SystemExit(f"authzbench: {arguments.engine} runs {where}, so --placement {arguments.placement} "
                         f"would misdescribe it")
    plan = RunPlan(
        service_time_ns=_seconds(arguments.service_seconds),
        open_loop_rates=tuple(arguments.rates),
        open_loop_ns=_seconds(arguments.open_loop_seconds),
        revocation_cap_ns=_seconds(arguments.revocation_cap_seconds),
        revocation_trials=arguments.revocation_trials,
        delegation_interval_ns=_seconds(arguments.delegation_interval_seconds),
        placement=arguments.placement,
    )
    engine = choice.build(arguments)
    try:
        result = run(engine, read_dataset(arguments.dataset), plan)
    finally:
        engine.close()
    write_document(to_document(result, environment()), arguments.out)


def build_server(arguments: argparse.Namespace) -> EngineServer:
    dataset = read_dataset(arguments.dataset)
    engine = SERVED[arguments.engine](arguments)
    engine.load(dataset)
    host, _, port = arguments.listen.rpartition(":")
    return EngineServer(engine, dataset, (host, int(port)), arguments.allow)


def _serve(arguments: argparse.Namespace) -> None:
    server = build_server(arguments)
    host, port = server.address
    print(f"serving {arguments.engine} on {host}:{port} to {', '.join(arguments.allow)}", flush=True)
    server.serve_forever()


def _required(arguments: argparse.Namespace, name: str):
    value = getattr(arguments, name)
    if value is None:
        raise SystemExit(f"authzbench: --{name.replace('_', '-')} is required for {arguments.engine}")
    return value


def _s3_location(arguments: argparse.Namespace) -> dict:
    return {"bucket": _required(arguments, "bucket"), "region": _required(arguments, "region"),
            "prefix": arguments.prefix, "endpoint": arguments.endpoint, "path_style": arguments.path_style or None}


def _seconds(value: float) -> int:
    return round(value * NANOS_PER_SECOND)


def _rates(text: str) -> list[int]:
    return [int(rate) for rate in text.split(",")]


def _addresses(text: str) -> list[str]:
    return [address.strip() for address in text.split(",") if address.strip()]


def _add_log_location(parser: argparse.ArgumentParser) -> None:
    location = parser.add_argument_group("where the Nodus log is shipped (nodus-writer and nodus-follower)")
    location.add_argument("--bucket")
    location.add_argument("--region")
    location.add_argument("--prefix")
    location.add_argument("--endpoint", help="an S3-compatible endpoint; AWS when omitted")
    location.add_argument("--path-style", action="store_true", help="address the bucket in the path")
    location.add_argument("--ship-directory", type=Path, help="a local directory instead of a bucket")
    location.add_argument("--key-id", type=int, default=1)


def build_parser() -> argparse.ArgumentParser:
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
    run_command.add_argument("--placement", choices=PLACEMENTS, default=IN_PROCESS)
    run_command.add_argument("--rates", type=_rates, default=[100, 500, 1_000, 10_000])
    run_command.add_argument("--service-seconds", type=float, default=5.0)
    run_command.add_argument("--open-loop-seconds", type=float, default=10.0)
    run_command.add_argument("--revocation-cap-seconds", type=float, default=30.0)
    run_command.add_argument("--revocation-trials", type=int, default=30, help="revocation races per mode")
    run_command.add_argument("--delegation-interval-seconds", type=float, default=0.05,
                             help="least time between one delegation leaf's checks")
    run_command.add_argument("--writer", help="the engine server, e.g. http://10.0.0.5:7700")
    run_command.add_argument("--public-key", type=Path, help="the writer's public signing key")
    run_command.add_argument("--read-wait-seconds", type=float,
                             help="longest a follower check waits for a token; the revocation cap when omitted")
    run_command.add_argument("--token-lifetime-seconds", type=float, default=60.0,
                             help="how long the token baseline's access tokens live")
    _add_log_location(run_command)
    run_command.set_defaults(handler=_run)

    serve_command = commands.add_parser("serve", help="hold one engine for a driver on another machine")
    serve_command.add_argument("--engine", choices=sorted(SERVED), required=True)
    serve_command.add_argument("--dataset", type=Path, required=True)
    serve_command.add_argument("--listen", required=True, help="the private address to bind, e.g. 10.0.0.5:7700")
    serve_command.add_argument("--allow", type=_addresses, required=True,
                               help="the only addresses served, comma-separated, e.g. 10.0.0.6")
    serve_command.add_argument("--graph", type=Path, help="the writer's graph directory")
    serve_command.add_argument("--signing-key", type=Path, help="the writer's private key; made when missing")
    _add_log_location(serve_command)
    serve_command.set_defaults(handler=_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    arguments.handler(arguments)
