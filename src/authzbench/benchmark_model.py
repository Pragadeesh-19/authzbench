from authzbench.model import Arrow, Computed, Definition, Model, Permission, Relation, SubjectType

USER = SubjectType("user")
GROUP_MEMBERS = SubjectType("group", "member")
FOLDER = SubjectType("folder")


def _shared_resource(name: str) -> Definition:
    return Definition(
        name,
        relations=(
            Relation("parent", (FOLDER,)),
            Relation("viewer", (USER, GROUP_MEMBERS)),
        ),
        permissions=(Permission("view", (Computed("viewer"), Arrow("parent", "view"))),),
    )


DOCUMENT_SHARING = Model(
    (
        Definition("user"),
        Definition("group", relations=(Relation("member", (USER, GROUP_MEMBERS)),)),
        _shared_resource("folder"),
        _shared_resource("document"),
    )
)

DOCUMENT_SHARING_NAME = "document-sharing"
MODELS = {DOCUMENT_SHARING_NAME: DOCUMENT_SHARING}
