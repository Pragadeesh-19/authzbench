import jsonschema
import pytest

from authzbench.dataset import SCALES
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.results import environment, results_schema, to_document
from authzbench.runner import RunPlan, run

MILLIS = 1_000_000
SHORT_PLAN = RunPlan(service_time_ns=100 * MILLIS, open_loop_rates=(500,), open_loop_ns=200 * MILLIS,
                     revocation_cap_ns=100 * MILLIS, revocation_trials=3)


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


def _document(dataset, profile: CalibrationProfile) -> dict:
    engine = CalibrationEngine(profile)
    try:
        return to_document(run(engine, dataset, SHORT_PLAN), environment())
    finally:
        engine.close()


def test_a_run_produces_a_document_that_follows_the_schema(dataset):
    document = _document(dataset, CalibrationProfile())
    jsonschema.validate(document, results_schema())
    assert [mode["consistency"] for mode in document["modes"]] == ["fresh", "fastest"]
    assert document["conformance"]["checks"] == SCALES["S"].checks + 1 + 28
    assert [baseline["rate_per_second"] for baseline in document["driver_baseline"]] == list(
        SHORT_PLAN.open_loop_rates)
    assert {opened["in_flight"] for mode in document["modes"] for opened in mode["open_loop"]} == {1}


def test_a_capped_revocation_is_written_as_null_staleness(dataset):
    document = _document(dataset, CalibrationProfile(staleness_ns=300 * MILLIS))
    jsonschema.validate(document, results_schema())
    stale = next(mode for mode in document["modes"] if mode["consistency"] == "fastest")
    assert stale["revocation"]["staleness_ns"] is None
    assert stale["revocation"]["denied_by_ns"] is None
    assert (stale["revocation"]["trials_requested"], stale["revocation"]["trials"], stale["revocation"]["capped"]) == (
        3, 1, 1)
    fresh = next(mode for mode in document["modes"] if mode["consistency"] == "fresh")
    assert (fresh["revocation"]["trials"], fresh["revocation"]["capped"], len(fresh["revocation"]["samples"])) == (
        3, 0, 3)
    assert fresh["revocation"]["denied_by_ns"]["p50_ns"] >= fresh["revocation"]["staleness_ns"]["p50_ns"]
    assert stale["revocation"]["cap_ns"] == 100 * MILLIS


def test_the_schema_rejects_a_document_with_mismatches(dataset):
    document = _document(dataset, CalibrationProfile())
    document["conformance"]["mismatches"] = 3
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(document, results_schema())


def test_a_delegation_race_is_written_beside_the_revocation_race(dataset):
    document = _document(dataset, CalibrationProfile(staleness_ns=300 * MILLIS))
    stale = next(mode for mode in document["modes"] if mode["consistency"] == "fastest")["delegation"]
    assert (stale["trials"], stale["capped"], stale["staleness_ns"]) == (1, 1, None)
    assert stale["samples"][0]["allowed_at_cap"] == len(dataset.delegation.probes)
    assert stale["samples"][0]["leaf_staleness_ns"] is None
    fresh = next(mode for mode in document["modes"] if mode["consistency"] == "fresh")["delegation"]
    assert (fresh["trials"], fresh["capped"]) == (3, 0)
    sample = fresh["samples"][0]
    assert len(sample["leaf_staleness_ns"]) == len(dataset.delegation.probes)
    assert sample["staleness_ns"] == max(sample["leaf_staleness_ns"])
    assert sample["denied_by_ns"] == max(sample["leaf_denied_by_ns"])
