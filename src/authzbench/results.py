from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from authzbench.measure import DelegationResult, OpenLoopResult, RevocationResult, TrialSpread
from authzbench.runner import ModeResult, RunResult

FORMAT = "authzbench.results/3"


def results_schema() -> dict:
    return json.loads(resources.files("authzbench").joinpath("results.schema.json").read_text(encoding="utf-8"))


def environment() -> dict:
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpus": os.cpu_count() or 0,
    }


def to_document(result: RunResult, run_environment: dict) -> dict:
    dataset = result.dataset
    return {
        "format": FORMAT,
        "engine": {"name": result.engine, "version": result.engine_version},
        "dataset": {
            "model": dataset.model_name,
            "scale": dataset.scale.name,
            "seed": dataset.seed,
            "relationships": len(dataset.relationships),
            "checks": len(dataset.expectations),
        },
        "environment": run_environment,
        "conformance": {"checks": result.conformance_checks, "mismatches": 0},
        "driver_baseline": [_open_loop(baseline) for baseline in result.driver_baseline],
        "modes": [_mode(mode) for mode in result.modes],
    }


def write_document(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _mode(mode: ModeResult) -> dict:
    return {
        "consistency": mode.consistency.value,
        "mode": mode.mode,
        "service_time": asdict(mode.service_time),
        "open_loop": [_open_loop(result) for result in mode.open_loop],
        "revocation": _revocation(mode.revocations, mode.revocation_trials, mode.placement),
        "delegation": _delegation(mode.delegations, mode.revocation_trials, mode.placement),
    }


def _open_loop(result: OpenLoopResult) -> dict:
    return {
        "rate_per_second": result.rate_per_second,
        "in_flight": result.in_flight,
        "latency": asdict(result.latency),
        "schedule_lag": asdict(result.schedule_lag),
    }


def _revocation(trials: tuple[RevocationResult, ...], requested: int, placement: str) -> dict:
    seen = [trial for trial in trials if not trial.capped]
    return {
        "placement": placement,
        "cap_ns": trials[0].cap_ns,
        "trials_requested": requested,
        "trials": len(trials),
        "capped": len(trials) - len(seen),
        "staleness_ns": _spread([trial.staleness_ns for trial in seen]),
        "denied_by_ns": _spread([trial.denied_by_ns for trial in seen]),
        "resolution_ns": _spread([trial.resolution_ns for trial in trials]),
        "write_ns": _spread([trial.write_ns for trial in trials]),
        "samples": [{"staleness_ns": trial.staleness_ns, "resolution_ns": trial.resolution_ns,
                     "denied_by_ns": trial.denied_by_ns, "write_ns": trial.write_ns} for trial in trials],
    }


def _delegation(trials: tuple[DelegationResult, ...], requested: int, placement: str) -> dict:
    seen = [trial for trial in trials if not trial.capped]
    return {
        "placement": placement,
        "cap_ns": trials[0].cap_ns,
        "trials_requested": requested,
        "trials": len(trials),
        "capped": len(trials) - len(seen),
        "staleness_ns": _spread([trial.staleness_ns for trial in seen]),
        "denied_by_ns": _spread([trial.denied_by_ns for trial in seen]),
        "resolution_ns": _spread([trial.resolution_ns for trial in trials]),
        "write_ns": _spread([trial.write_ns for trial in trials]),
        "samples": [{"staleness_ns": trial.staleness_ns, "resolution_ns": trial.resolution_ns,
                     "denied_by_ns": trial.denied_by_ns, "write_ns": trial.write_ns,
                     "allowed_at_cap": trial.allowed_at_cap,
                     "leaf_staleness_ns": None if trial.capped else list(trial.leaf_staleness_ns),
                     "leaf_denied_by_ns": None if trial.capped else list(trial.leaf_denied_by_ns)}
                    for trial in trials],
    }


def _spread(values: list[int]) -> dict | None:
    spread = TrialSpread.of(values)
    return None if spread is None else asdict(spread)
