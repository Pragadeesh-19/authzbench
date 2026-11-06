from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from authzbench.model import Computed, Model, ModelError, Term
from authzbench.relationships import Change, CheckQuery, ObjectRef, Operation, Relationship, SubjectRef

_Step = tuple[ObjectRef, str]
_RelationType = tuple[str, str]


class RelationshipStore:
    def __init__(self, relationships: Iterable[Relationship] = ()) -> None:
        self._subjects: dict[_Step, set[SubjectRef]] = defaultdict(set)
        self._holders: dict[SubjectRef, dict[_RelationType, set[ObjectRef]]] = defaultdict(lambda: defaultdict(set))
        self._count = 0
        for relationship in relationships:
            self.add(relationship)

    def __len__(self) -> int:
        return self._count

    def add(self, relationship: Relationship) -> bool:
        step = (relationship.resource, relationship.relation)
        if relationship.subject in self._subjects[step]:
            return False
        self._subjects[step].add(relationship.subject)
        self._holders[relationship.subject][_relation_type(relationship)].add(relationship.resource)
        self._count += 1
        return True

    def remove(self, relationship: Relationship) -> bool:
        if not self.relates(relationship.resource, relationship.relation, relationship.subject):
            return False
        self._subjects[(relationship.resource, relationship.relation)].remove(relationship.subject)
        self._holders[relationship.subject][_relation_type(relationship)].remove(relationship.resource)
        self._count -= 1
        return True

    def apply(self, change: Change) -> bool:
        if change.operation is Operation.ADD:
            return self.add(change.relationship)
        return self.remove(change.relationship)

    def relates(self, resource: ObjectRef, relation: str, subject: SubjectRef) -> bool:
        return subject in self._subjects.get((resource, relation), ())

    def subjects(self, resource: ObjectRef, relation: str) -> frozenset[SubjectRef]:
        return frozenset(self._subjects.get((resource, relation), ()))

    def holders(self, subject: SubjectRef, relation_type: _RelationType) -> frozenset[ObjectRef]:
        return frozenset(self._holders.get(subject, {}).get(relation_type, ()))


def _relation_type(relationship: Relationship) -> _RelationType:
    return relationship.resource.type, relationship.relation


class ReferenceEvaluator:
    """Answers checks on a union-only model.

    A subject's memberships are found by walking up through the usersets the model allows as subjects; a relation
    holds when one of those memberships is among its subjects, and permissions are evaluated top-down."""

    def __init__(self, model: Model, store: RelationshipStore) -> None:
        self._model = model
        self._store = store
        self._userset_types = frozenset(
            (subject.type, subject.relation)
            for definition in model.definitions
            for relation in definition.relations
            for subject in relation.subjects
            if subject.relation is not None
        )

    def check(self, query: CheckQuery) -> bool:
        return self._holds(query.resource, query.permission, self._memberships(query.subject), set())

    def _memberships(self, subject: ObjectRef) -> list[SubjectRef]:
        memberships = [SubjectRef(subject)]
        seen = set(memberships)
        for member in memberships:
            for relation_type in self._userset_types:
                for resource in self._store.holders(member, relation_type):
                    userset = SubjectRef(resource, relation_type[1])
                    if userset not in seen:
                        seen.add(userset)
                        memberships.append(userset)
        return memberships

    def _holds(self, resource: ObjectRef, name: str, memberships: list[SubjectRef], explored: set[_Step]) -> bool:
        step = (resource, name)
        if step in explored:
            return False
        explored.add(step)
        definition = self._model.definition(resource.type)
        permission = definition.permission(name)
        if permission is not None:
            return any(self._term_holds(resource, term, memberships, explored) for term in permission.terms)
        if definition.relation(name) is None:
            raise ModelError(f"{resource.type} defines no {name!r}")
        return any(self._store.relates(resource, name, member) for member in memberships)

    def _term_holds(self, resource: ObjectRef, term: Term, memberships: list[SubjectRef],
                    explored: set[_Step]) -> bool:
        if isinstance(term, Computed):
            return self._holds(resource, term.relation, memberships, explored)
        return any(self._holds(parent.object, term.permission, memberships, explored)
                   for parent in self._store.subjects(resource, term.tupleset) if parent.relation is None)
