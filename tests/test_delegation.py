from dataclasses import replace

import pytest

from authzbench.dataset import SCALES, DatasetError
from authzbench.engine import Consistency
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import DELEGATION_DEPTH, DELEGATION_FANOUT, _Generator, generate, verify_scenarios
from authzbench.measure import MeasurementError, race_delegation, race_delegations
from authzbench.relationships import Change

MICROS = 1_000
MILLIS = 1_000 * MICROS
SECOND = 1_000 * MILLIS
CHECK_COST = 100 * MICROS
LEAVES = DELEGATION_FANOUT ** (DELEGATION_DEPTH - 1)


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


def _engine(dataset, clock, **profile) -> CalibrationEngine:
    engine = CalibrationEngine(CalibrationProfile(**profile), clock)
    engine.load(dataset)
    return engine


def test_the_tree_has_one_agent_under_the_person_and_fans_out_below_it(dataset):
    delegation = dataset.delegation
    assert len(delegation.probes) == LEAVES == 27
    assert len(delegation.delegations) == sum(DELEGATION_FANOUT ** level for level in range(DELEGATION_DEPTH)) == 40
    assert [str(item) for item in delegation.delegations[:2]] == [
        "user:delegation-person user:delegation-agent", "user:delegation-agent user:delegation-agent-0"]
    assert str(delegation.revoke) == "group:delegation-person#member@group:delegation-agent#member"
    assert {probe.subject.id.count("-") for probe in delegation.probes} == {DELEGATION_DEPTH}


def test_a_revoke_that_leaves_a_leaf_allowed_is_refused(dataset):
    lower = next(relationship for relationship in dataset.relationships
                 if str(relationship) == "group:delegation-agent#member@group:delegation-agent-0#member")
    with pytest.raises(DatasetError, match="disagrees"):
        verify_scenarios(replace(dataset, delegation=replace(dataset.delegation, revoke=lower)))


def test_the_tree_draws_nothing_from_the_seed(dataset, monkeypatch):
    monkeypatch.setattr(_Generator, "_delegation_scenario", lambda self: None)
    without = _Generator(SCALES["S"], 1).generate()
    assert without.expectations == dataset.expectations
    assert dataset.relationships[:len(without.relationships)] == without.relationships


def test_a_fresh_check_denies_every_leaf_on_its_first_check(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=500 * MILLIS, extra_latency_ns=CHECK_COST)
    result = race_delegation(engine, engine, dataset.delegation, Consistency.FRESH, SECOND, virtual_clock)
    assert result.leaf_staleness_ns == tuple(leaf * CHECK_COST for leaf in range(LEAVES))
    assert result.leaf_denied_by_ns == tuple((leaf + 1) * CHECK_COST for leaf in range(LEAVES))
    assert (result.staleness_ns, result.resolution_ns) == ((LEAVES - 1) * CHECK_COST, (LEAVES - 1) * CHECK_COST)


def test_a_known_staleness_ends_on_a_known_sweep(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=500 * MILLIS, extra_latency_ns=CHECK_COST)
    result = race_delegation(engine, engine, dataset.delegation, Consistency.FASTEST, SECOND, virtual_clock)
    visible = 500 * MILLIS // CHECK_COST
    sweep_of = {leaf: -(-(visible - leaf) // LEAVES) for leaf in range(LEAVES)}
    assert result.leaf_staleness_ns == tuple((sweep_of[leaf] * LEAVES + leaf) * CHECK_COST for leaf in range(LEAVES))
    assert result.staleness_ns == 5026 * CHECK_COST
    assert result.resolution_ns == LEAVES * CHECK_COST
    assert result.denied_by_ns == 5026 * CHECK_COST + CHECK_COST


def test_a_tree_still_allowed_at_the_cap_is_capped_with_every_leaf_counted(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=150 * MILLIS, extra_latency_ns=CHECK_COST)
    result = race_delegation(engine, engine, dataset.delegation, Consistency.FASTEST, 100 * MILLIS, virtual_clock)
    assert result.capped
    assert (result.staleness_ns, result.denied_by_ns, result.allowed_at_cap) == (None, None, LEAVES)


def test_a_capped_trial_ends_the_delegation_trials(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=300 * MILLIS, extra_latency_ns=CHECK_COST)
    trials = race_delegations(engine, engine, dataset.delegation, Consistency.FASTEST, 100 * MILLIS, 30,
                              virtual_clock)
    assert len(trials) == 1 and trials[0].capped


def test_repeated_delegation_races_each_wait_for_the_restore(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=300 * MILLIS, extra_latency_ns=CHECK_COST)
    trials = race_delegations(engine, engine, dataset.delegation, Consistency.FASTEST, SECOND, 3, virtual_clock)
    assert len(trials) == 3
    assert all(300 * MILLIS <= trial.staleness_ns < 300 * MILLIS + LEAVES * CHECK_COST for trial in trials)


def test_the_race_restores_the_delegation(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, extra_latency_ns=CHECK_COST)
    race_delegation(engine, engine, dataset.delegation, Consistency.FRESH, SECOND, virtual_clock)
    assert all(engine.check(probe, Consistency.FRESH) for probe in dataset.delegation.probes)


def test_a_leaf_denied_before_the_revoke_is_refused(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, extra_latency_ns=CHECK_COST)
    engine.write(Change.remove(dataset.delegation.revoke))
    with pytest.raises(MeasurementError, match="denied before the revoke"):
        race_delegation(engine, engine, dataset.delegation, Consistency.FRESH, SECOND, virtual_clock)


def test_nodus_denies_every_leaf_once_the_revoke_returns(dataset):
    pytest.importorskip("nodusdb")
    from authzbench.engines.nodus import NodusEngine

    engine = NodusEngine.in_memory()
    try:
        engine.load(dataset)
        result = race_delegation(engine, engine, dataset.delegation, Consistency.FRESH, SECOND)
        assert not result.capped
        assert result.staleness_ns < 50 * MILLIS
        assert all(engine.check(probe, Consistency.FRESH) for probe in dataset.delegation.probes)
    finally:
        engine.close()
