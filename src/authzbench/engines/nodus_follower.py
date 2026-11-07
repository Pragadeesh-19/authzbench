from __future__ import annotations

import nodusdb

from authzbench.dataset import Dataset
from authzbench.engine import Consistency, Revision
from authzbench.relationships import Change, CheckQuery
from authzbench.remote import RemoteEngine, RemoteError


class NodusFollowerEngine:
    """Nodus on a second machine: writes go to the writer's machine, checks read a follower in this process.

    A fresh check passes the latest write's token, so the follower answers only once it holds that write; a fastest
    check reads whatever the follower has applied."""

    name = "nodus-follower"

    def __init__(self, writer: RemoteEngine, follower: nodusdb.Graph) -> None:
        if writer.version != nodusdb.__version__:
            raise RemoteError(f"the writer runs nodusdb {writer.version} and this follower {nodusdb.__version__}")
        self._writer = writer
        self._follower = follower
        self._latest: Revision | None = None

    @property
    def version(self) -> str:
        return nodusdb.__version__

    def modes(self) -> dict[Consistency, str]:
        return {Consistency.FRESH: "follower, with the latest write's token",
                Consistency.FASTEST: "follower, without a token"}

    def load(self, dataset: Dataset) -> None:
        self._writer.load(dataset)
        self._latest = self._writer.current_revision()

    def write(self, change: Change) -> Revision:
        self._latest = self._writer.write(change)
        return self._latest

    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool:
        token = after or self._latest
        at_least = tuple(token.value) if consistency is Consistency.FRESH and token is not None else None
        return self._follower.check(str(query.resource), query.permission, str(query.subject), at_least=at_least)

    def close(self) -> None:
        self._follower.close()
        self._writer.close()
