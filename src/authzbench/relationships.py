from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_NAME = r"[a-z][a-z0-9_]*"
_ID = r"[A-Za-z0-9_\-]+"


def _object_pattern(role: str) -> str:
    return rf"(?P<{role}_type>{_NAME}):(?P<{role}_id>{_ID})"


_RELATIONSHIP = re.compile(
    rf"^{_object_pattern('resource')}#(?P<relation>{_NAME})"
    rf"@{_object_pattern('subject')}(?:#(?P<subject_relation>{_NAME}))?$"
)
_QUERY = re.compile(rf"^{_object_pattern('resource')}#(?P<permission>{_NAME})@{_object_pattern('subject')}$")
_ANSWERS = {"allow": True, "deny": False}


def _object_from(match: re.Match, role: str) -> ObjectRef:
    return ObjectRef(match[f"{role}_type"], match[f"{role}_id"])


class NotationError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class ObjectRef:
    type: str
    id: str

    def __str__(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True, order=True)
class SubjectRef:
    object: ObjectRef
    relation: str | None = None

    def __str__(self) -> str:
        return str(self.object) if self.relation is None else f"{self.object}#{self.relation}"


@dataclass(frozen=True, order=True)
class Relationship:
    resource: ObjectRef
    relation: str
    subject: SubjectRef

    def __str__(self) -> str:
        return f"{self.resource}#{self.relation}@{self.subject}"

    @classmethod
    def parse(cls, line: str) -> Relationship:
        match = _RELATIONSHIP.match(line.strip())
        if match is None:
            raise NotationError(f"not a relationship: {line!r}")
        subject = SubjectRef(_object_from(match, "subject"), match["subject_relation"])
        return cls(_object_from(match, "resource"), match["relation"], subject)


@dataclass(frozen=True, order=True)
class CheckQuery:
    resource: ObjectRef
    permission: str
    subject: ObjectRef

    def __str__(self) -> str:
        return f"{self.resource}#{self.permission}@{self.subject}"

    @classmethod
    def parse(cls, text: str) -> CheckQuery:
        match = _QUERY.match(text.strip())
        if match is None:
            raise NotationError(f"not a check: {text!r}")
        return cls(_object_from(match, "resource"), match["permission"], _object_from(match, "subject"))


@dataclass(frozen=True)
class Expectation:
    query: CheckQuery
    allowed: bool

    def __str__(self) -> str:
        return f"{self.query} {'allow' if self.allowed else 'deny'}"

    @classmethod
    def parse(cls, line: str) -> Expectation:
        query, _, answer = line.strip().rpartition(" ")
        if answer not in _ANSWERS:
            raise NotationError(f"a check ends with allow or deny: {line!r}")
        return cls(CheckQuery.parse(query), _ANSWERS[answer])


class Operation(Enum):
    ADD = "add"
    REMOVE = "remove"


@dataclass(frozen=True)
class Change:
    operation: Operation
    relationship: Relationship

    @classmethod
    def add(cls, relationship: Relationship) -> Change:
        return cls(Operation.ADD, relationship)

    @classmethod
    def remove(cls, relationship: Relationship) -> Change:
        return cls(Operation.REMOVE, relationship)
