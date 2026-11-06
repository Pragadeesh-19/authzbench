from dataclasses import replace

import pytest

from authzbench.benchmark_model import DOCUMENT_SHARING
from authzbench.dataset import SCALES
from authzbench.engine import Consistency
from authzbench.engines.calibration import CalibrationEngine, CalibrationProfile
from authzbench.generator import generate
from authzbench.model import Computed, Definition, Model, Permission
from authzbench.runner import ConformanceError, verify_conformance


@pytest.fixture(scope="module")
def dataset():
    return generate(SCALES["S"], seed=1)


def _without_inheritance(model: Model) -> Model:
    def flatten(definition: Definition) -> Definition:
        if definition.name != "document":
            return definition
        return replace(definition, permissions=(Permission("view", (Computed("viewer"),)),))

    return Model(tuple(map(flatten, model.definitions)))


def test_nodus_agrees_with_the_reference_on_every_check(dataset):
    pytest.importorskip("nodusdb")
    from authzbench.engines.nodus import NodusEngine

    engine = NodusEngine.in_memory()
    try:
        engine.load(dataset)
        assert verify_conformance(engine, dataset) == len(dataset.expectations) + 1
        assert engine.check(dataset.revocation.probe, Consistency.FRESH)
    finally:
        engine.close()


def test_an_engine_that_misreads_the_model_is_refused_before_timing(dataset):
    engine = CalibrationEngine(CalibrationProfile())
    engine.load(replace(dataset, model=_without_inheritance(DOCUMENT_SHARING)))
    with pytest.raises(ConformanceError) as refused:
        verify_conformance(engine, dataset)
    assert refused.value.mismatches
    assert all(mismatch.allowed for mismatch in refused.value.mismatches)
