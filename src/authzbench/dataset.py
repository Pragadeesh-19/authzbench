from __future__ import annotations

from dataclasses import dataclass

from authzbench.model import Model
from authzbench.relationships import CheckQuery, Expectation, Relationship


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class Scale:
    name: str
    users: int
    groups: int
    folders: int
    documents: int
    checks: int


SCALES = {
    scale.name: scale
    for scale in (
        Scale("S", users=1_000, groups=100, folders=200, documents=2_000, checks=2_000),
        Scale("M", users=10_000, groups=1_000, folders=2_000, documents=20_000, checks=10_000),
        Scale("L", users=100_000, groups=10_000, folders=20_000, documents=200_000, checks=50_000),
    )
}


def scale_named(name: str) -> Scale:
    try:
        return SCALES[name]
    except KeyError:
        raise DatasetError(f"unknown scale {name!r}; choose one of {', '.join(SCALES)}") from None


@dataclass(frozen=True)
class RevocationScenario:
    revoke: Relationship
    probe: CheckQuery


@dataclass(frozen=True)
class Dataset:
    model_name: str
    model: Model
    scale: Scale
    seed: int
    relationships: tuple[Relationship, ...]
    expectations: tuple[Expectation, ...]
    revocation: RevocationScenario
    nesting: Expectation
