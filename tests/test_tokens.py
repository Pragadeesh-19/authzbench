import pytest

from authzbench.dataset import SCALES
from authzbench.engine import Consistency
from authzbench.engines.tokens import TokenEngine
from authzbench.generator import generate
from authzbench.measure import race_delegation, race_revocation
from authzbench.relationships import Change, CheckQuery
from authzbench.runner import verify_conformance

MICROS = 1_000
MILLIS = 1_000 * MICROS
SECOND = 1_000 * MILLIS
LIFETIME = 60 * SECOND
CHECK_COST = 100 * MICROS


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


class Timed:
    """Charges every check a fixed cost on the virtual clock and remembers when each write was acknowledged."""

    def __init__(self, engine: TokenEngine, clock) -> None:
        self.engine = engine
        self.clock = clock
        self.acknowledged: list[int] = []

    def write(self, change: Change):
        revision = self.engine.write(change)
        self.acknowledged.append(self.clock.now())
        return revision

    def check(self, query, consistency, after=None) -> bool:
        allowed = self.engine.check(query, consistency, after)
        self.clock.wait_until(self.clock.now() + CHECK_COST)
        return allowed


def _engine(dataset, clock) -> TokenEngine:
    engine = TokenEngine(LIFETIME, clock)
    engine.load(dataset)
    return engine


def _delegator_query(probe: CheckQuery, delegator) -> CheckQuery:
    return CheckQuery(probe.resource, probe.permission, delegator)


def test_tokens_agree_with_the_reference_on_every_check(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    assert verify_conformance(engine, dataset) == len(dataset.expectations) + 1 + 28


def test_no_token_outlives_its_delegator_s(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    assert all(engine.check(probe, Consistency.FASTEST) for probe in dataset.delegation.probes)
    probe = dataset.delegation.probes[0]
    for delegation in dataset.delegation.delegations:
        delegate = engine.token(_delegator_query(probe, delegation.delegate))
        delegator = engine.token(_delegator_query(probe, delegation.delegator))
        assert delegate is not None and delegator is not None
        assert delegate["exp"] <= delegator["exp"]
        assert delegate["exp"] - delegate["iat"] <= LIFETIME


def test_issue_times_are_spread_over_a_lifetime_and_repeat_with_the_seed(dataset, virtual_clock):
    def expiries():
        engine = _engine(dataset, virtual_clock)
        return [engine.check(query, Consistency.FASTEST) and engine.token(query)["exp"]
                for query in (expectation.query for expectation in dataset.expectations[:400]
                              if expectation.allowed)]

    first = expiries()
    assert first == expiries()
    now = virtual_clock.now()
    assert all(now < expiry <= now + LIFETIME for expiry in first)
    assert min(first) < now + LIFETIME // 4 and max(first) > now + 3 * LIFETIME // 4


def test_a_revoke_stops_new_tokens_but_not_issued_ones(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    probes = dataset.delegation.probes
    assert all(engine.check(probe, Consistency.FASTEST) for probe in probes)
    latest = max(engine.token(probe)["exp"] for probe in probes)
    engine.write(Change.remove(dataset.delegation.revoke))
    assert all(engine.check(probe, Consistency.FASTEST) for probe in probes)
    virtual_clock.wait_until(latest)
    assert not any(engine.check(probe, Consistency.FASTEST) for probe in probes)
    assert engine.check(dataset.delegation.kept, Consistency.FASTEST)
    engine.write(Change.add(dataset.delegation.revoke))
    assert all(engine.check(probe, Consistency.FASTEST) for probe in probes)


def test_every_leaf_is_denied_on_the_first_check_after_its_token_expires(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    timed = Timed(engine, virtual_clock)
    probes = dataset.delegation.probes
    assert all(engine.check(probe, Consistency.FASTEST) for probe in probes)
    expiries = [engine.token(probe)["exp"] for probe in probes]
    result = race_delegation(timed, timed, dataset.delegation, Consistency.FASTEST, 2 * LIFETIME, virtual_clock)
    acknowledged = timed.acknowledged[0]
    sweep = len(probes) * CHECK_COST
    for expiry, staleness in zip(expiries, result.leaf_staleness_ns):
        assert expiry - acknowledged <= staleness < expiry - acknowledged + sweep
    assert 0 < result.staleness_ns <= LIFETIME + sweep


def test_a_single_revocation_lasts_until_the_user_s_token_expires(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    timed = Timed(engine, virtual_clock)
    probe = dataset.revocation.probe
    assert engine.check(probe, Consistency.FASTEST)
    expiry = engine.token(probe)["exp"]
    result = race_revocation(timed, timed, dataset.revocation, Consistency.FASTEST, 2 * LIFETIME, virtual_clock)
    assert expiry - timed.acknowledged[0] <= result.staleness_ns < expiry - timed.acknowledged[0] + CHECK_COST


def test_a_token_whose_signature_does_not_match_is_refused(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    probe = dataset.revocation.probe
    assert engine.check(probe, Consistency.FASTEST)
    header, payload, signature = engine._tokens[probe].split(".")
    engine._tokens[probe] = ".".join((header, payload, signature[::-1]))
    assert engine.token(probe) is None


def test_a_token_for_another_check_is_refused(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock)
    first, second = dataset.delegation.probes[:2]
    assert engine.check(first, Consistency.FASTEST)
    engine._tokens[second] = engine._tokens[first]
    assert engine.token(second) is None


def test_a_lifetime_must_be_positive(virtual_clock):
    with pytest.raises(ValueError, match="positive"):
        TokenEngine(0, virtual_clock)
