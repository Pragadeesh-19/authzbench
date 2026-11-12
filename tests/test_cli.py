import json

import pytest

from authzbench import cli
from authzbench.dataset import SCALES
from authzbench.dataset_files import write_dataset
from authzbench.generator import generate

MILLIS = 1_000_000
LOOPBACK = "127.0.0.1"
SHORT_RUN = ["--rates", "100", "--service-seconds", "0.1", "--open-loop-seconds", "0.2",
             "--revocation-cap-seconds", "10", "--revocation-trials", "3"]


@pytest.fixture(scope="module")
def dataset_directory(tmp_path_factory):
    directory = tmp_path_factory.mktemp("dataset")
    write_dataset(generate(SCALES["S"], seed=1), directory)
    return directory


def serve(*arguments):
    parsed = cli.build_parser().parse_args(["serve", "--listen", f"{LOOPBACK}:0", "--allow", LOOPBACK, *arguments])
    server = cli.build_server(parsed)
    return server.start(), f"http://{LOOPBACK}:{server.address[1]}"


def run(*arguments):
    cli.main(["run", *arguments])


@pytest.mark.parametrize("engine, placement", [("calibration", "second_machine"),
                                               ("remote-calibration", "in_process")])
def test_a_placement_that_misdescribes_the_engine_is_refused(dataset_directory, tmp_path, engine, placement):
    with pytest.raises(SystemExit, match=f"--placement {placement} would misdescribe it"):
        run("--engine", engine, "--placement", placement, "--dataset", str(dataset_directory),
            "--out", str(tmp_path / "out.json"), "--writer", "http://127.0.0.1:1", *SHORT_RUN)


def test_a_remote_engine_needs_the_writer_s_address(dataset_directory, tmp_path):
    with pytest.raises(SystemExit, match="--writer is required for remote-calibration"):
        run("--engine", "remote-calibration", "--placement", "same_machine", "--dataset", str(dataset_directory),
            "--out", str(tmp_path / "out.json"), *SHORT_RUN)


def test_the_calibration_engine_served_on_another_machine_measures_its_known_staleness(dataset_directory, tmp_path):
    server, url = serve("--engine", "calibration", "--dataset", str(dataset_directory))
    out = tmp_path / "remote-calibration.json"
    try:
        run("--engine", "remote-calibration", "--writer", url, "--placement", "same_machine",
            "--dataset", str(dataset_directory), "--out", str(out), *SHORT_RUN)
    finally:
        server.stop()

    document = json.loads(out.read_text(encoding="utf-8"))
    stale = next(mode["revocation"] for mode in document["modes"] if mode["consistency"] == "fastest")
    assert {mode["revocation"]["placement"] for mode in document["modes"]} == {"same_machine"}
    assert (stale["trials"], stale["capped"]) == (3, 0)
    for trial in stale["samples"]:
        assert trial["staleness_ns"] <= 500 * MILLIS + trial["resolution_ns"]
        assert trial["denied_by_ns"] >= trial["staleness_ns"]


def test_a_nodus_writer_and_its_follower_run_as_a_pair(dataset_directory, tmp_path, capsys):
    pytest.importorskip("nodusdb")
    key, bucket, out = tmp_path / "keys" / "signing.pem", tmp_path / "bucket", tmp_path / "follower.json"
    key.parent.mkdir()
    server, url = serve("--engine", "nodus-writer", "--dataset", str(dataset_directory), "--graph",
                        str(tmp_path / "graph"), "--signing-key", str(key), "--ship-directory", str(bucket))
    assert f"copy {key.with_suffix('.pub')}" in capsys.readouterr().out
    try:
        run("--engine", "nodus-follower", "--writer", url, "--placement", "same_machine",
            "--public-key", str(key.with_suffix(".pub")), "--ship-directory", str(bucket),
            "--dataset", str(dataset_directory), "--out", str(out), *SHORT_RUN)
    finally:
        server.stop()

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["engine"]["name"] == "nodus-follower"
    assert [mode["consistency"] for mode in document["modes"]] == ["fresh", "fastest"]
    fresh = document["modes"][0]["revocation"]
    assert all(trial["staleness_ns"] == trial["resolution_ns"] for trial in fresh["samples"])


def test_a_token_run_needs_a_cap_longer_than_the_token_lifetime(dataset_directory, tmp_path):
    with pytest.raises(SystemExit, match="cap must be longer"):
        run("--engine", "tokens", "--dataset", str(dataset_directory), "--out", str(tmp_path / "out.json"),
            "--token-lifetime-seconds", "60", "--revocation-cap-seconds", "30")


def test_the_token_baseline_revokes_its_tree_within_one_token_lifetime(dataset_directory, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from authzbench.results import results_schema

    out = tmp_path / "tokens.json"
    run("--engine", "tokens", "--dataset", str(dataset_directory), "--out", str(out), *SHORT_RUN,
        "--token-lifetime-seconds", "0.2")
    document = json.loads(out.read_text(encoding="utf-8"))
    jsonschema.validate(document, results_schema())
    (mode,) = document["modes"]
    assert mode["consistency"] == "fastest" and "0.2 s lifetime" in mode["mode"]
    for race in (mode["revocation"], mode["delegation"]):
        assert (race["trials"], race["capped"]) == (3, 0)
        assert race["staleness_ns"]["max_ns"] < 400 * MILLIS
