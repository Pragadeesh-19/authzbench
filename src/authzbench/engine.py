from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from authzbench.dataset import Dataset
from authzbench.relationships import Change, CheckQuery


class Consistency(Enum):
    FRESH = "fresh"
    FASTEST = "fastest"


@dataclass(frozen=True)
class Revision:
    value: object


class Writer(Protocol):
    def write(self, change: Change) -> Revision: ...


class Checker(Protocol):
    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool: ...


class Engine(Writer, Checker, Protocol):
    name: str

    @property
    def version(self) -> str: ...

    def modes(self) -> dict[Consistency, str]: ...

    def load(self, dataset: Dataset) -> None: ...

    def close(self) -> None: ...
