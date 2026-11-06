from dataclasses import replace

import pytest

from authzbench.dataset import SCALES, DatasetError, scale_named
from authzbench.dataset_files import read_dataset, write_dataset
from authzbench.generator import NESTING_CHAIN_DEPTH, generate, verify_scenarios
from authzbench.reference import ReferenceEvaluator, RelationshipStore
from authzbench.relationships import Change, Expectation

SMALL = SCALES["S"]


@pytest.fixture(scope="module")
def dataset():
    return generate(SMALL, seed=1)


def test_the_same_seed_gives_the_same_dataset(dataset):
    assert generate(SMALL, seed=1) == dataset


def test_a_different_seed_gives_different_relationships(dataset):
    assert generate(SMALL, seed=2).relationships != dataset.relationships


def test_half_the_checks_are_allowed_and_none_repeat(dataset):
    assert len(dataset.expectations) == SMALL.checks
    assert sum(expectation.allowed for expectation in dataset.expectations) == SMALL.checks // 2
    assert len({expectation.query for expectation in dataset.expectations}) == SMALL.checks


def test_every_expected_answer_is_the_reference_answer(dataset):
    evaluator = ReferenceEvaluator(dataset.model, RelationshipStore(dataset.relationships))
    for expectation in dataset.expectations:
        assert evaluator.check(expectation.query) is expectation.allowed, str(expectation)


def test_relationships_are_distinct(dataset):
    assert len(set(dataset.relationships)) == len(dataset.relationships)


def test_removing_the_revoke_denies_the_probe(dataset):
    store = RelationshipStore(dataset.relationships)
    evaluator = ReferenceEvaluator(dataset.model, store)
    assert evaluator.check(dataset.revocation.probe)
    store.apply(Change.remove(dataset.revocation.revoke))
    assert not evaluator.check(dataset.revocation.probe)


def test_the_nesting_check_crosses_the_whole_chain(dataset):
    chain = [relationship for relationship in dataset.relationships
             if relationship.resource.type == "group" and relationship.resource.id.startswith("nesting-")]
    assert len(chain) == NESTING_CHAIN_DEPTH
    assert dataset.nesting.allowed


def test_a_nesting_answer_the_reference_disagrees_with_is_refused(dataset):
    flipped = replace(dataset, nesting=Expectation(dataset.nesting.query, False))
    with pytest.raises(DatasetError, match="disagrees"):
        verify_scenarios(flipped)


def test_a_revoke_that_changes_nothing_is_refused(dataset):
    unrelated = next(relationship for relationship in dataset.relationships
                     if relationship.resource.id.startswith("g"))
    with pytest.raises(DatasetError, match="disagrees"):
        verify_scenarios(replace(dataset, revocation=replace(dataset.revocation, revoke=unrelated)))


def test_a_dataset_survives_its_files(dataset, tmp_path):
    write_dataset(dataset, tmp_path)
    assert read_dataset(tmp_path) == dataset


def test_a_truncated_file_is_refused(dataset, tmp_path):
    write_dataset(dataset, tmp_path)
    lines = (tmp_path / "checks.txt").read_text(encoding="utf-8").splitlines()
    (tmp_path / "checks.txt").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="promises 2000 checks"):
        read_dataset(tmp_path)


def test_an_unknown_scale_is_named():
    with pytest.raises(DatasetError, match="unknown scale 'XL'"):
        scale_named("XL")
