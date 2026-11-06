import pytest

from authzbench.benchmark_model import DOCUMENT_SHARING
from authzbench.model import Arrow, Computed, Definition, Model, ModelError, Permission, Relation, SubjectType

USER = Definition("user")


def _model(*definitions: Definition) -> Model:
    return Model((USER, *definitions))


def test_the_benchmark_model_is_valid():
    assert DOCUMENT_SHARING.definition("document").permission("view") is not None


@pytest.mark.parametrize(
    ("definitions", "message"),
    [
        ((USER,), "type 'user' is defined twice"),
        ((Definition("doc", relations=(Relation("viewer", (SubjectType("team"),)),)),), "unknown type 'team'"),
        ((Definition("doc", relations=(Relation("viewer", ()),)),), "allows no subject types"),
        ((Definition("doc", relations=(Relation("viewer", (SubjectType("user", "member"),)),)),), "not a relation"),
        ((Definition("doc", permissions=(Permission("view", ()),)),), "has no terms"),
        ((Definition("doc", permissions=(Permission("view", (Computed("owner"),)),)),), "cannot use"),
        ((Definition("doc", permissions=(Permission("view", (Computed("view"),)),)),), "cannot use"),
        ((Definition("doc", permissions=(Permission("view", (Arrow("parent", "view"),)),)),), "unknown relation"),
    ],
)
def test_an_invalid_model_is_refused_with_the_reason(definitions, message):
    with pytest.raises(ModelError, match=message):
        _model(*definitions)


def test_a_relation_and_a_permission_cannot_share_a_name():
    viewer = Relation("view", (SubjectType("user"),))
    with pytest.raises(ModelError, match="defined twice"):
        _model(Definition("doc", relations=(viewer,), permissions=(Permission("view", (Computed("view"),)),)))


def test_an_arrow_needs_a_followable_relation_with_the_permission_on_its_targets():
    members = Definition("team", relations=(Relation("member", (SubjectType("user"),)),))
    via_userset = Definition("doc", relations=(Relation("parent", (SubjectType("team", "member"),)),),
                             permissions=(Permission("view", (Arrow("parent", "member"),)),))
    with pytest.raises(ModelError, match="cannot be followed"):
        _model(members, via_userset)
    to_missing = Definition("doc", relations=(Relation("parent", (SubjectType("team"),)),),
                            permissions=(Permission("view", (Arrow("parent", "view"),)),))
    with pytest.raises(ModelError, match="defines no 'view'"):
        _model(members, to_missing)


def test_an_unknown_type_is_named_when_looked_up():
    with pytest.raises(ModelError, match="unknown type 'robot'"):
        DOCUMENT_SHARING.definition("robot")
