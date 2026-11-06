import pytest

from authzbench.relationships import CheckQuery, Expectation, NotationError, ObjectRef, Relationship, SubjectRef


@pytest.mark.parametrize(
    "line",
    ["document:d1#viewer@user:u1", "document:d1#viewer@group:g1#member", "folder:revocation-root#parent@folder:F_9"],
)
def test_a_relationship_survives_a_round_trip(line):
    assert str(Relationship.parse(line)) == line


def test_a_relationship_parses_into_its_parts():
    relationship = Relationship.parse("document:d1#viewer@group:g1#member")
    assert relationship == Relationship(ObjectRef("document", "d1"), "viewer",
                                        SubjectRef(ObjectRef("group", "g1"), "member"))


@pytest.mark.parametrize("line", ["document:d1#view@user:u1 allow", "document:d1#view@user:u1 deny"])
def test_a_check_survives_a_round_trip(line):
    assert str(Expectation.parse(line)) == line


@pytest.mark.parametrize(
    "line",
    ["", "document:d1#viewer", "document#viewer@user:u1", "Document:d1#viewer@user:u1", "document:d 1#viewer@user:u1"],
)
def test_a_malformed_relationship_is_refused(line):
    with pytest.raises(NotationError):
        Relationship.parse(line)


@pytest.mark.parametrize(
    "line",
    ["document:d1#view@user:u1", "document:d1#view@user:u1 maybe", "document:d1#view@group:g1#member allow"],
)
def test_a_malformed_check_is_refused(line):
    with pytest.raises(NotationError):
        Expectation.parse(line)


def test_a_check_query_parses_without_an_answer():
    assert CheckQuery.parse("document:d1#view@user:u1") == CheckQuery(ObjectRef("document", "d1"), "view",
                                                                       ObjectRef("user", "u1"))
