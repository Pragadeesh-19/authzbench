from __future__ import annotations

from typing import Protocol

from authzbench.model import Arrow, Computed, Definition, Model, Term


class SchemaEmitter(Protocol):
    engine: str
    file_name: str

    def emit(self, model: Model) -> str: ...


def _zanzibar_term(term: Term) -> str:
    if isinstance(term, Computed):
        return term.relation
    return f"{term.tupleset}->{term.permission}"


def _is_empty(definition: Definition) -> bool:
    return not definition.relations and not definition.permissions


class NodusSchema:
    engine = "nodus"
    file_name = "model.nodus"

    def emit(self, model: Model) -> str:
        lines = ["schema 1"]
        for definition in model.definitions:
            lines += self._definition(definition)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _definition(definition: Definition) -> list[str]:
        if _is_empty(definition):
            return [f"type {definition.name}"]
        body = [f"  relation {relation.name}: {' | '.join(map(str, relation.subjects))}"
                for relation in definition.relations]
        body += [f"  permission {permission.name} = {' + '.join(map(_zanzibar_term, permission.terms))}"
                 for permission in definition.permissions]
        return [f"type {definition.name} {{", *body, "}"]


class SpiceDbSchema:
    engine = "spicedb"
    file_name = "model.zed"

    def emit(self, model: Model) -> str:
        return "\n\n".join(self._definition(definition) for definition in model.definitions) + "\n"

    @staticmethod
    def _definition(definition: Definition) -> str:
        if _is_empty(definition):
            return f"definition {definition.name} {{}}"
        body = [f"\trelation {relation.name}: {' | '.join(map(str, relation.subjects))}"
                for relation in definition.relations]
        body += [f"\tpermission {permission.name} = {' + '.join(map(_zanzibar_term, permission.terms))}"
                 for permission in definition.permissions]
        return "\n".join([f"definition {definition.name} {{", *body, "}"])


class OpenFgaSchema:
    engine = "openfga"
    file_name = "model.fga"

    def emit(self, model: Model) -> str:
        blocks = ["model\n  schema 1.1"]
        blocks += [self._definition(definition) for definition in model.definitions]
        return "\n\n".join(blocks) + "\n"

    @staticmethod
    def _definition(definition: Definition) -> str:
        if _is_empty(definition):
            return f"type {definition.name}"
        body = [f"    define {relation.name}: [{', '.join(map(str, relation.subjects))}]"
                for relation in definition.relations]
        body += [f"    define {permission.name}: {' or '.join(map(OpenFgaSchema._term, permission.terms))}"
                 for permission in definition.permissions]
        return "\n".join([f"type {definition.name}", "  relations", *body])

    @staticmethod
    def _term(term: Term) -> str:
        if isinstance(term, Arrow):
            return f"{term.permission} from {term.tupleset}"
        return term.relation


EMITTERS: tuple[SchemaEmitter, ...] = (NodusSchema(), SpiceDbSchema(), OpenFgaSchema())
