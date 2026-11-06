from __future__ import annotations

from authzbench.engine import Consistency, Revision
from authzbench.relationships import CheckQuery


class NoopChecker:
    """Answers instantly, so an open loop against it measures the driver alone."""

    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool:
        return True
