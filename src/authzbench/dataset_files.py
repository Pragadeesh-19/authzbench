from __future__ import annotations

import json
from pathlib import Path

from authzbench.benchmark_model import MODELS
from authzbench.dataset import (Dataset, DatasetError, Delegation, DelegationScenario, RevocationScenario,
                               scale_named)
from authzbench.relationships import CheckQuery, Expectation, ObjectRef, Relationship

FORMAT = "authzbench.dataset/2"
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
        "delegation": {
            "person": str(dataset.delegation.person),
            "revoke": str(dataset.delegation.revoke),
            "probes": [str(probe) for probe in dataset.delegation.probes],
            "delegations": [str(delegation) for delegation in dataset.delegation.delegations],
        },
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
                   expectations, revocation, Expectation.parse(manifest["nesting"]),
                   _delegation(manifest["delegation"]))


def _delegation(section: dict) -> DelegationScenario:
    return DelegationScenario(
        _object(section["person"]),
        Relationship.parse(section["revoke"]),
        tuple(map(CheckQuery.parse, section["probes"])),
        tuple(Delegation(*map(_object, pair.split(" "))) for pair in section["delegations"]),
    )


def _object(text: str) -> ObjectRef:
    kind, _, identifier = text.partition(":")
    if not kind or not identifier:
        raise DatasetError(f"not an object: {text!r}")
    return ObjectRef(kind, identifier)


def _write_lines(path: Path, lines) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for line in lines:
            file.write(line + "\n")


def _read_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


def _require_count(manifest: dict, key: str, items: tuple) -> None:
    if manifest[key] != len(items):
        raise DatasetError(f"the manifest promises {manifest[key]} {key} but the file holds {len(items)}")
