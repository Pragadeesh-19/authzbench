import pytest

from authzbench.dataset import SCALES
from authzbench.engine import Consistency
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.measure import MeasurementError, open_loop, race_revocation, service_time
from authzbench.relationships import Change

MILLIS = 1_000_000
SECOND = 1_000 * MILLIS
SCHEDULING_TOLERANCE = 20 * MILLIS


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


def _engine(dataset, **profile) -> CalibrationEngine:
    engine = CalibrationEngine(CalibrationProfile(**profile))
    engine.load(dataset)
    return engine


def _queries(dataset):
    return [expectation.query for expectation in dataset.expectations[:200]]


def test_a_known_staleness_is_measured_within_the_stated_resolution(dataset):
    engine = _engine(dataset, staleness_ns=500 * MILLIS)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, cap_ns=5 * SECOND)
    assert 500 * MILLIS <= result.staleness_ns <= 500 * MILLIS + result.resolution_ns + SCHEDULING_TOLERANCE


def test_a_fresh_check_sees_the_revoke_at_once(dataset):
    engine = _engine(dataset, staleness_ns=500 * MILLIS)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FRESH, cap_ns=5 * SECOND)
    assert result.staleness_ns < SCHEDULING_TOLERANCE


def test_a_revoke_never_seen_inside_the_cap_is_reported_as_capped(dataset):
    engine = _engine(dataset, staleness_ns=300 * MILLIS)
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, cap_ns=100 * MILLIS)
    assert result.capped
    assert result.cap_ns == 100 * MILLIS


def test_the_race_restores_the_revoked_relationship(dataset):
    engine = _engine(dataset)
    race_revocation(engine, engine, dataset.revocation, Consistency.FRESH, cap_ns=SECOND)
    assert engine.check(dataset.revocation.probe, Consistency.FRESH)


def test_a_probe_denied_before_the_revoke_is_refused(dataset):
    engine = _engine(dataset)
    engine.write(Change.remove(dataset.revocation.revoke))
    with pytest.raises(MeasurementError, match="denied before the revoke"):
        race_revocation(engine, engine, dataset.revocation, Consistency.FRESH, cap_ns=SECOND)


def test_a_known_check_latency_is_measured(dataset):
    engine = _engine(dataset, extra_latency_ns=2 * MILLIS)
    summary = service_time(engine, _queries(dataset), Consistency.FRESH, duration_ns=SECOND // 2)
    assert 2 * MILLIS <= summary.p50_ns <= 2 * MILLIS + MILLIS // 2


def test_the_open_loop_counts_a_stall_that_the_closed_loop_hides(dataset):
    engine = _engine(dataset, stall_period_ns=200 * MILLIS, stall_duration_ns=50 * MILLIS)
    queries = _queries(dataset)
    closed = service_time(engine, queries, Consistency.FRESH, duration_ns=SECOND)
    opened = open_loop(engine, queries, Consistency.FRESH, rate_per_second=1_000, duration_ns=SECOND)
    assert closed.p99_ns < 5 * MILLIS
    assert opened.latency.p99_ns >= 30 * MILLIS
    assert opened.latency.count == 1_000
