from __future__ import annotations

from collections.abc import Iterator, Sequence

import nodusdb

from authzbench.dataset import Dataset
from authzbench.emitters import NodusSchema
from authzbench.engine import Consistency, Revision
from authzbench.relationships import Change, CheckQuery, Operation, Relationship

_LOAD_BATCH = 2_000


def _arguments(relationship: Relationship) -> tuple[str, str, str, str | None]:
    subject = relationship.subject
    return str(relationship.resource), relationship.relation, str(subject.object), subject.relation


def _batches(items: Sequence[Relationship], size: int) -> Iterator[Sequence[Relationship]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class NodusEngine:
    """Nodus in the benchmark's own process: every check reads the state the last write produced."""

    name = "nodus"

    def __init__(self, graph: nodusdb.Graph) -> None:
        self._graph = graph

    @classmethod
    def in_memory(cls) -> NodusEngine:
        return cls(nodusdb.Graph())

    @classmethod
    def shipping(cls, path: str, shipping: nodusdb.Shipping) -> NodusEngine:
        return cls(nodusdb.Graph(path=path, sync_mode="sync", shipping=shipping))

    @property
    def version(self) -> str:
        return nodusdb.__version__

    def modes(self) -> dict[Consistency, str]:
        return {Consistency.FRESH: "in process"}

    def load(self, dataset: Dataset) -> None:
        self._graph.apply_schema(NodusSchema().emit(dataset.model))
        for batch in _batches(dataset.relationships, _LOAD_BATCH):
            transaction = nodusdb.Transaction()
            for relationship in batch:
                transaction.add(*_arguments(relationship))
            self._graph.write(transaction)

    def write(self, change: Change) -> Revision:
        transaction = nodusdb.Transaction()
        if change.operation is Operation.ADD:
            transaction.add(*_arguments(change.relationship))
        else:
            transaction.remove(*_arguments(change.relationship))
        return Revision(self._graph.write(transaction))

    def current_revision(self) -> Revision:
        return Revision(self._graph.token)

    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool:
        at_least = after.value if after is not None else None
        return self._graph.check(str(query.resource), query.permission, str(query.subject), at_least=at_least)

    def close(self) -> None:
        self._graph.close()
