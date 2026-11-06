import random

import pytest

from authzbench.benchmark_model import DOCUMENT_SHARING
from authzbench.model import Computed, ModelError
from authzbench.reference import ReferenceEvaluator, RelationshipStore
from authzbench.relationships import Change, CheckQuery, ObjectRef, Relationship, SubjectRef

USERS = [ObjectRef("user", f"u{index}") for index in range(6)]
GROUPS = [ObjectRef("group", f"g{index}") for index in range(5)]
FOLDERS = [ObjectRef("folder", f"f{index}") for index in range(5)]
DOCUMENTS = [ObjectRef("document", f"d{index}") for index in range(5)]


def _naive_holds(relationships, resource, name, subject, path=frozenset()):
    if (resource, name) in path:
        return False
    path = path | {(resource, name)}
    permission = DOCUMENT_SHARING.definition(resource.type).permission(name)
    if permission is None:
        for relationship in relationships:
            if relationship.resource == resource and relationship.relation == name:
                granted = relationship.subject
                if granted.relation is None and granted.object == subject:
                    return True
                if granted.relation is not None and _naive_holds(relationships, granted.object, granted.relation,
                                                                 subject, path):
                    return True
        return False
    for term in permission.terms:
        if isinstance(term, Computed):
            if _naive_holds(relationships, resource, term.relation, subject, path):
                return True
            continue
        for relationship in relationships:
            if relationship.resource == resource and relationship.relation == term.tupleset:
                if _naive_holds(relationships, relationship.subject.object, term.permission, subject, path):
                    return True
    return False


def _random_relationships(rng: random.Random) -> list[Relationship]:
    relationships = []
    for _ in range(rng.randint(5, 40)):
        kind = rng.randrange(4)
        if kind == 0:
            member = SubjectRef(rng.choice(USERS)) if rng.random() < 0.6 else SubjectRef(rng.choice(GROUPS), "member")
            relationships.append(Relationship(rng.choice(GROUPS), "member", member))
        elif kind == 1:
            resource = rng.choice(FOLDERS + DOCUMENTS)
            relationships.append(Relationship(resource, "parent", SubjectRef(rng.choice(FOLDERS))))
        else:
            viewer = SubjectRef(rng.choice(USERS)) if rng.random() < 0.4 else SubjectRef(rng.choice(GROUPS), "member")
            relationships.append(Relationship(rng.choice(FOLDERS + DOCUMENTS), "viewer", viewer))
    return relationships


@pytest.mark.parametrize("seed", range(40))
def test_the_evaluator_agrees_with_a_naive_one_on_random_graphs_with_cycles(seed):
    rng = random.Random(seed)
    relationships = _random_relationships(rng)
    evaluator = ReferenceEvaluator(DOCUMENT_SHARING, RelationshipStore(relationships))
    for resource in FOLDERS + DOCUMENTS:
        for user in USERS:
            query = CheckQuery(resource, "view", user)
            expected = _naive_holds(relationships, resource, "view", user)
            assert evaluator.check(query) is expected, f"seed={seed} check={query}"


def test_a_relation_is_answered_through_nested_groups():
    store = RelationshipStore([
        Relationship(GROUPS[0], "member", SubjectRef(GROUPS[1], "member")),
        Relationship(GROUPS[1], "member", SubjectRef(USERS[0])),
    ])
    evaluator = ReferenceEvaluator(DOCUMENT_SHARING, store)
    assert evaluator.check(CheckQuery(GROUPS[0], "member", USERS[0]))
    assert not evaluator.check(CheckQuery(GROUPS[0], "member", USERS[1]))


def test_a_removed_relationship_stops_granting():
    granted = Relationship(DOCUMENTS[0], "viewer", SubjectRef(USERS[0]))
    store = RelationshipStore([granted])
    evaluator = ReferenceEvaluator(DOCUMENT_SHARING, store)
    query = CheckQuery(DOCUMENTS[0], "view", USERS[0])
    assert evaluator.check(query)
    assert store.apply(Change.remove(granted))
    assert not evaluator.check(query)
    assert not store.apply(Change.remove(granted))


def test_the_store_counts_distinct_relationships_only():
    relationship = Relationship(DOCUMENTS[0], "viewer", SubjectRef(USERS[0]))
    store = RelationshipStore([relationship, relationship])
    assert len(store) == 1
    assert not store.add(relationship)


def test_an_empty_store_grants_nothing():
    evaluator = ReferenceEvaluator(DOCUMENT_SHARING, RelationshipStore())
    assert not evaluator.check(CheckQuery(DOCUMENTS[0], "view", USERS[0]))


def test_an_undefined_relation_is_refused():
    evaluator = ReferenceEvaluator(DOCUMENT_SHARING, RelationshipStore())
    with pytest.raises(ModelError, match="defines no 'owner'"):
        evaluator.check(CheckQuery(DOCUMENTS[0], "owner", USERS[0]))
