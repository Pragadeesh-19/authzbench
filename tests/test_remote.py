import http.client
import json
from urllib.parse import urlsplit

import pytest

from authzbench.dataset import SCALES
from authzbench.engine import Consistency
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.measure import race_revocation
from authzbench.relationships import Change
from authzbench.remote import EngineServer, RemoteEngine, RemoteError
from authzbench.timing import SYSTEM_CLOCK

MILLIS = 1_000_000
SECOND = 1_000 * MILLIS
LOOPBACK = "127.0.0.1"


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


@pytest.fixture
def serve(dataset):
    servers = []

    def start(profile=CalibrationProfile(), allowed=(LOOPBACK,)):
        engine = CalibrationEngine(profile)
        engine.load(dataset)
        server = EngineServer(engine, dataset, (LOOPBACK, 0), allowed).start()
        servers.append(server)
        return f"http://{LOOPBACK}:{server.address[1]}"

    yield start
    for server in servers:
        server.stop()


def test_a_remote_engine_answers_as_the_engine_it_serves(dataset, serve):
    engine = RemoteEngine(serve())
    engine.load(dataset)
    expectation = dataset.expectations[0]

    assert (engine.name, engine.version) == ("calibration", "1")
    assert engine.modes() == {Consistency.FRESH: "fresh", Consistency.FASTEST: "stale"}
    assert engine.check(expectation.query, Consistency.FRESH) is expectation.allowed
    assert engine.write(Change.remove(dataset.revocation.revoke)).value == 1
    assert engine.current_revision().value == 1
    assert engine.check(dataset.revocation.probe, Consistency.FRESH) is False


def test_a_known_staleness_is_measured_across_the_wire_within_the_stated_resolution(dataset, serve):
    engine = RemoteEngine(serve(CalibrationProfile(staleness_ns=500 * MILLIS)))
    engine.load(dataset)
    started = SYSTEM_CLOCK.now()
    result = race_revocation(engine, engine, dataset.revocation, Consistency.FASTEST, 5 * SECOND)

    assert SYSTEM_CLOCK.now() - started >= 500 * MILLIS
    assert result.staleness_ns <= 500 * MILLIS + result.resolution_ns
    assert result.denied_by_ns >= result.staleness_ns


def test_an_address_the_server_does_not_allow_is_refused(serve):
    with pytest.raises(RemoteError, match="did not answer"):
        RemoteEngine(serve(allowed=("127.0.0.2",)))


def test_a_server_holding_another_dataset_is_refused(serve):
    engine = RemoteEngine(serve())
    with pytest.raises(RemoteError, match="holds dataset"):
        engine.load(generate(SCALES["S"], seed=2))


def test_a_malformed_request_is_refused_with_its_reason(dataset, serve):
    url = urlsplit(serve())
    connection = http.client.HTTPConnection(url.hostname, url.port)
    body = json.dumps({"query": str(dataset.revocation.probe), "consistency": "sometimes"})
    connection.request("POST", "/check", body=body, headers={"Content-Type": "application/json"})
    response = connection.getresponse()

    assert response.status == 400
    assert "'sometimes' is not a valid Consistency" in json.loads(response.read())["error"]
    connection.close()


def test_an_engine_server_must_allow_someone(dataset):
    with pytest.raises(ValueError, match="at least one allowed address"):
        EngineServer(CalibrationEngine(CalibrationProfile()), dataset, (LOOPBACK, 0), ())


def test_an_address_that_is_not_an_engine_server_url_is_refused():
    with pytest.raises(ValueError, match="http://10.0.0.5:7700"):
        RemoteEngine("10.0.0.5:7700")
