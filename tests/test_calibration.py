import pytest

from authzbench.dataset import SCALES
from authzbench.engine import Consistency
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.measure import MeasurementError, open_loop, race_revocation, service_time
from authzbench.relationships import Change
from authzbench.timing import SYSTEM_CLOCK

MICROS = 1_000
MILLIS = 1_000 * MICROS
SECOND = 1_000 * MILLIS
CHECK_COST = 100 * MICROS
HISTOGRAM_PRECISION = 0.001


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


def _engine(dataset, clock, **profile) -> CalibrationEngine:
    engine = CalibrationEngine(CalibrationProfile(**profile), clock)
    engine.load(dataset)
    return engine


def _queries(dataset):
    return [expectation.query for expectation in dataset.expectations[:200]]


def _close_to(measured: int, expected: int) -> bool:
    return abs(measured - expected) <= expected * HISTOGRAM_PRECISION


def test_a_known_staleness_is_measured_exactly(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=500 * MILLIS, extra_latency_ns=CHECK_COST)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, 5 * SECOND, virtual_clock)
    assert result.staleness_ns == 500 * MILLIS
    assert result.resolution_ns == CHECK_COST
    assert result.denied_by_ns == 500 * MILLIS + CHECK_COST


def test_a_fresh_check_denies_on_the_first_check_after_the_acknowledgement(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=500 * MILLIS, extra_latency_ns=CHECK_COST)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FRESH, 5 * SECOND, virtual_clock)
    assert result.staleness_ns == result.resolution_ns == 0
    assert result.denied_by_ns == CHECK_COST


def test_a_revoke_never_seen_inside_the_cap_is_reported_as_capped(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=150 * MILLIS, extra_latency_ns=CHECK_COST)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, 100 * MILLIS, virtual_clock)
    assert result.capped
    assert result.resolution_ns == CHECK_COST
    assert result.denied_by_ns is None


def test_a_check_that_waits_inside_the_call_for_the_revoke_is_charged_in_denied_by(dataset, virtual_clock):
    class WaitsForTheToken:
        def check(self, query, consistency, after=None):
            if after is not None:
                virtual_clock.wait_until(virtual_clock.now() + 300 * MILLIS)
            return engine.check(query, Consistency.FRESH)

    engine = _engine(dataset, virtual_clock, extra_latency_ns=CHECK_COST)
    result = race_revocation(engine, WaitsForTheToken(), dataset.revocation, Consistency.FRESH, SECOND, virtual_clock)
    assert result.staleness_ns == 0
    assert result.denied_by_ns == 300 * MILLIS + CHECK_COST


def test_the_race_restores_the_revoked_relationship(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, extra_latency_ns=CHECK_COST)
    race_revocation(engine, engine, dataset.revocation, Consistency.FRESH, SECOND, virtual_clock)
    assert engine.check(dataset.revocation.probe, Consistency.FRESH)


def test_a_race_waits_for_the_previous_restore_to_become_visible(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, staleness_ns=300 * MILLIS, extra_latency_ns=CHECK_COST)
    race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, SECOND, virtual_clock)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, SECOND, virtual_clock)
    assert result.staleness_ns == 300 * MILLIS


def test_a_probe_denied_before_the_revoke_is_refused(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, extra_latency_ns=CHECK_COST)
    engine.write(Change.remove(dataset.revocation.revoke))
    with pytest.raises(MeasurementError, match="denied before the revoke"):
        race_revocation(engine, engine, dataset.revocation, Consistency.FRESH, SECOND, virtual_clock)


def test_a_known_check_latency_is_measured(dataset, virtual_clock):
    engine = _engine(dataset, virtual_clock, extra_latency_ns=2 * MILLIS)
    summary = service_time(engine, _queries(dataset), Consistency.FRESH, SECOND, virtual_clock)
    assert _close_to(summary.p50_ns, 2 * MILLIS)
    assert summary.count == SECOND // (2 * MILLIS)


def test_the_open_loop_counts_a_stall_that_the_closed_loop_hides(dataset, virtual_clock):
    quick_check = 10 * MICROS
    engine = _engine(dataset, virtual_clock, extra_latency_ns=quick_check,
                     stall_period_ns=150 * MILLIS, stall_duration_ns=100 * MILLIS)
    queries = _queries(dataset)
    closed = service_time(engine, queries, Consistency.FRESH, 300 * MILLIS, virtual_clock)
    opened = open_loop(engine, queries, Consistency.FRESH, 1_000, SECOND, virtual_clock)
    assert _close_to(closed.p50_ns, quick_check)
    assert 20 * MILLIS <= opened.latency.p50_ns <= 30 * MILLIS
    assert opened.latency.count == 1_000


def test_the_system_clock_measures_a_real_staleness_within_the_stated_resolution(dataset):
    engine = _engine(dataset, SYSTEM_CLOCK, staleness_ns=500 * MILLIS)
    started = SYSTEM_CLOCK.now()
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, 5 * SECOND)
    assert SYSTEM_CLOCK.now() - started >= 500 * MILLIS
    assert result.staleness_ns <= 500 * MILLIS + result.resolution_ns


def test_the_write_time_is_measured_from_issue_to_acknowledgement(dataset, virtual_clock):
    class SlowWriter:
        def write(self, change):
            virtual_clock.wait_until(virtual_clock.now() + 3 * MILLIS)
            return engine.write(change)

    engine = _engine(dataset, virtual_clock, extra_latency_ns=CHECK_COST)
    result = race_revocation(SlowWriter(), engine, dataset.revocation, Consistency.FRESH, SECOND, virtual_clock)
    assert result.write_ns == 3 * MILLIS
