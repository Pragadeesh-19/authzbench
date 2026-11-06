from pathlib import Path

import pytest

from authzbench.benchmark_model import DOCUMENT_SHARING
from authzbench.emitters import EMITTERS, NodusSchema

GENERATED = Path(__file__).resolve().parents[1] / "generated"


@pytest.mark.parametrize("emitter", EMITTERS, ids=lambda emitter: emitter.engine)
def test_the_committed_schema_is_exactly_what_the_model_generates(emitter):
    committed = (GENERATED / emitter.file_name).read_text(encoding="utf-8")
    assert committed == emitter.emit(DOCUMENT_SHARING), (
        f"generated/{emitter.file_name} differs from the model; run `authzbench emit` and never edit it by hand"
    )


def test_every_engine_writes_its_own_file():
    assert len({emitter.file_name for emitter in EMITTERS}) == len(EMITTERS)


def test_nodus_accepts_the_schema_it_is_given():
    nodusdb = pytest.importorskip("nodusdb")
    with nodusdb.Graph() as graph:
        graph.apply_schema(NodusSchema().emit(DOCUMENT_SHARING))
        assert graph.schema_version == 1
