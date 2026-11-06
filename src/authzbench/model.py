from __future__ import annotations

from dataclasses import dataclass
from typing import Union


class ModelError(ValueError):
    pass


@dataclass(frozen=True)
class SubjectType:
    type: str
    relation: str | None = None

    def __str__(self) -> str:
        return self.type if self.relation is None else f"{self.type}#{self.relation}"


@dataclass(frozen=True)
class Relation:
    name: str
    subjects: tuple[SubjectType, ...]


@dataclass(frozen=True)
class Computed:
    relation: str


@dataclass(frozen=True)
class Arrow:
    tupleset: str
    permission: str


Term = Union[Computed, Arrow]


@dataclass(frozen=True)
class Permission:
    name: str
    terms: tuple[Term, ...]


@dataclass(frozen=True)
class Definition:
    name: str
    relations: tuple[Relation, ...] = ()
    permissions: tuple[Permission, ...] = ()

    def relation(self, name: str) -> Relation | None:
        return next((relation for relation in self.relations if relation.name == name), None)

    def permission(self, name: str) -> Permission | None:
        return next((permission for permission in self.permissions if permission.name == name), None)

    def defines(self, name: str) -> bool:
        return self.relation(name) is not None or self.permission(name) is not None


@dataclass(frozen=True)
class Model:
    definitions: tuple[Definition, ...]

    def __post_init__(self) -> None:
        _require_unique((definition.name for definition in self.definitions), "type")
        for definition in self.definitions:
            _validate_definition(self, definition)

    def definition(self, name: str) -> Definition:
        for definition in self.definitions:
            if definition.name == name:
                return definition
        raise ModelError(f"unknown type {name!r}")

    def has_type(self, name: str) -> bool:
        return any(definition.name == name for definition in self.definitions)


def _require_unique(names, kind: str) -> None:
    seen = set()
    for name in names:
        if name in seen:
            raise ModelError(f"{kind} {name!r} is defined twice")
        seen.add(name)


def _validate_definition(model: Model, definition: Definition) -> None:
    members = [relation.name for relation in definition.relations]
    members += [permission.name for permission in definition.permissions]
    _require_unique((f"{definition.name}#{name}" for name in members), "relation or permission")
    for relation in definition.relations:
        _validate_relation(model, definition, relation)
    for permission in definition.permissions:
        _validate_permission(model, definition, permission)


def _validate_relation(model: Model, definition: Definition, relation: Relation) -> None:
    if not relation.subjects:
        raise ModelError(f"{definition.name}#{relation.name} allows no subject types")
    for subject in relation.subjects:
        if not model.has_type(subject.type):
            raise ModelError(f"{definition.name}#{relation.name} allows unknown type {subject.type!r}")
        if subject.relation is not None and model.definition(subject.type).relation(subject.relation) is None:
            raise ModelError(f"{definition.name}#{relation.name} allows {subject}, which is not a relation")


def _validate_permission(model: Model, definition: Definition, permission: Permission) -> None:
    if not permission.terms:
        raise ModelError(f"{definition.name}#{permission.name} has no terms")
    for term in permission.terms:
        if isinstance(term, Computed):
            _validate_computed(definition, permission, term)
        else:
            _validate_arrow(model, definition, permission, term)


def _validate_computed(definition: Definition, permission: Permission, term: Computed) -> None:
    if term.relation == permission.name or not definition.defines(term.relation):
        raise ModelError(f"{definition.name}#{permission.name} refers to {term.relation!r}, which it cannot use")


def _validate_arrow(model: Model, definition: Definition, permission: Permission, term: Arrow) -> None:
    tupleset = definition.relation(term.tupleset)
    if tupleset is None:
        raise ModelError(f"{definition.name}#{permission.name} follows unknown relation {term.tupleset!r}")
    for subject in tupleset.subjects:
        if subject.relation is not None:
            raise ModelError(f"{definition.name}#{term.tupleset} allows {subject}, so it cannot be followed")
        if not model.definition(subject.type).defines(term.permission):
            raise ModelError(f"{subject.type} defines no {term.permission!r} for {definition.name}#{permission.name}")
