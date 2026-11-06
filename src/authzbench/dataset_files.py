from __future__ import annotations

import json
from pathlib import Path

from authzbench.benchmark_model import MODELS
from authzbench.dataset import Dataset, DatasetError, RevocationScenario, scale_named
from authzbench.relationships import CheckQuery, Expectation, Relationship

FORMAT = "authzbench.dataset/1"
MANIFEST = "dataset.json"
RELATIONSHIPS = "relationships.txt"
CHECKS = "checks.txt"


def write_dataset(dataset: Dataset, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _write_lines(directory / RELATIONSHIPS, map(str, dataset.relationships))
    _write_lines(directory / CHECKS, map(str, dataset.expectations))
    manifest = {
        "format": FORMAT,
        "model": dataset.model_name,
        "scale": dataset.scale.name,
        "seed": dataset.seed,
        "relationships": len(dataset.relationships),
        "checks": len(dataset.expectations),
        "revocation": {"revoke": str(dataset.revocation.revoke), "probe": str(dataset.revocation.probe)},
        "nesting": str(dataset.nesting),
    }
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def read_dataset(directory: Path) -> Dataset:
    manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise DatasetError(f"{directory / MANIFEST} is not an {FORMAT} manifest")
    relationships = tuple(map(Relationship.parse, _read_lines(directory / RELATIONSHIPS)))
    expectations = tuple(map(Expectation.parse, _read_lines(directory / CHECKS)))
    _require_count(manifest, "relationships", relationships)
    _require_count(manifest, "checks", expectations)
    revocation = RevocationScenario(
        Relationship.parse(manifest["revocation"]["revoke"]),
        CheckQuery.parse(manifest["revocation"]["probe"]),
    )
    model_name = manifest["model"]
    if model_name not in MODELS:
        raise DatasetError(f"unknown model {model_name!r}")
    return Dataset(model_name, MODELS[model_name], scale_named(manifest["scale"]), manifest["seed"], relationships,
                   expectations, revocation, Expectation.parse(manifest["nesting"]))


def _write_lines(path: Path, lines) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for line in lines:
            file.write(line + "\n")


def _read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _require_count(manifest: dict, key: str, items: tuple) -> None:
    if manifest[key] != len(items):
        raise DatasetError(f"the manifest promises {manifest[key]} {key} but the file holds {len(items)}")
