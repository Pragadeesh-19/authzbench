from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from authzbench.measure import OpenLoopResult, RevocationResult
from authzbench.runner import ModeResult, RunResult

FORMAT = "authzbench.results/1"


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
        "revocation": _revocation(mode.revocation, mode.placement),
    }


def _open_loop(result: OpenLoopResult) -> dict:
    return {
        "rate_per_second": result.rate_per_second,
        "latency": asdict(result.latency),
        "schedule_lag": asdict(result.schedule_lag),
    }


def _revocation(result: RevocationResult, placement: str) -> dict:
    return {
        "placement": placement,
        "staleness_ns": result.staleness_ns,
        "resolution_ns": result.resolution_ns,
        "write_ns": result.write_ns,
        "cap_ns": result.cap_ns,
    }
