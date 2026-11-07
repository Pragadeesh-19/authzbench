import pytest

nodusdb = pytest.importorskip("nodusdb")

from authzbench.dataset import SCALES  # noqa: E402
from authzbench.engine import Consistency  # noqa: E402
from authzbench.engines.nodus import NodusEngine  # noqa: E402
from authzbench.engines.nodus_follower import NodusFollowerEngine  # noqa: E402
from authzbench.generator import generate  # noqa: E402
from authzbench.measure import race_revocation  # noqa: E402
from authzbench.remote import EngineServer, RemoteEngine, RemoteError  # noqa: E402
from authzbench.runner import verify_conformance  # noqa: E402

SECOND = 1_000_000_000
KEY_ID = 1
LOOPBACK = "127.0.0.1"


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


@pytest.fixture(scope="module")
def nodus_pair(dataset, tmp_path_factory):
    root = tmp_path_factory.mktemp("nodus-pair")
    nodusdb.generate_signing_key(root / "signing.pem", root / "signing.pub")
    shipping = nodusdb.Shipping.directory(root / "bucket", key_file=root / "signing.pem", key_id=KEY_ID,
                                          interval_ms=10)
    writer = NodusEngine.shipping(str(root / "graph"), shipping)
    writer.load(dataset)
    server = EngineServer(writer, dataset, (LOOPBACK, 0), (LOOPBACK,)).start()
    follow = nodusdb.Follower.directory(root / "bucket", public_key_file=root / "signing.pub", key_id=KEY_ID,
                                        poll_interval_ms=10, read_wait_ms=30_000)
    engine = NodusFollowerEngine(RemoteEngine(f"http://{LOOPBACK}:{server.address[1]}"), nodusdb.Graph(follow=follow))
    engine.load(dataset)
    yield engine
    engine.close()
    server.stop()
    writer.close()


def test_the_follower_agrees_with_the_reference_once_it_holds_the_load(dataset, nodus_pair):
    assert verify_conformance(nodus_pair, dataset) == len(dataset.expectations) + 1


def test_with_the_revoke_s_token_the_first_check_waits_for_the_revoke_and_denies(dataset, nodus_pair):
    result = race_revocation(nodus_pair, nodus_pair, dataset.revocation, Consistency.FRESH, 30 * SECOND)

    assert result.staleness_ns == result.resolution_ns
    assert result.denied_by_ns > result.staleness_ns


def test_without_a_token_the_follower_denies_once_it_has_applied_the_revoke(dataset, nodus_pair):
    result = race_revocation(nodus_pair, nodus_pair, dataset.revocation, Consistency.FASTEST, 30 * SECOND)

    assert not result.capped
    assert result.denied_by_ns >= result.staleness_ns


def test_a_writer_on_another_nodusdb_version_is_refused():
    class Writer:
        version = "0.0.1"

    with pytest.raises(RemoteError, match="writer runs nodusdb 0.0.1"):
        NodusFollowerEngine(Writer(), None)
