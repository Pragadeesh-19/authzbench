import pytest


class VirtualClock:
    """A clock that moves only when something waits, so timing logic can be checked against exact values."""

    def __init__(self) -> None:
        self._now = 0

    def now(self) -> int:
        return self._now

    def wait_until(self, deadline_ns: int) -> None:
        self._now = max(self._now, deadline_ns)


@pytest.fixture
def virtual_clock() -> VirtualClock:
    return VirtualClock()
