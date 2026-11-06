from __future__ import annotations

import itertools
import random
from collections.abc import Callable

from authzbench.benchmark_model import DOCUMENT_SHARING, DOCUMENT_SHARING_NAME
from authzbench.dataset import Dataset, DatasetError, RevocationScenario, Scale
from authzbench.reference import ReferenceEvaluator, RelationshipStore
from authzbench.relationships import CheckQuery, Expectation, ObjectRef, Relationship, SubjectRef

MAX_GROUP_NESTING = 6
MAX_FOLDER_DEPTH = 8
NESTING_CHAIN_DEPTH = 16

_NESTED_GROUP_SHARE = 0.3
_SHARED_FOLDER_SHARE = 0.5
_GROUP_GRANT_SHARE = 0.8
_PARETO_ALPHA = 1.5
_SAMPLING_ATTEMPTS_PER_CHECK = 20


def generate(scale: Scale, seed: int) -> Dataset:
    dataset = _Generator(scale, seed).generate()
    verify_scenarios(dataset)
    return dataset


def verify_scenarios(dataset: Dataset) -> None:
    """Raise unless the reference evaluator agrees with the nesting answer and the revoke flips the probe."""
    store = RelationshipStore(dataset.relationships)
    evaluator = ReferenceEvaluator(dataset.model, store)
    _verify(evaluator, dataset.nesting)
    _verify(evaluator, Expectation(dataset.revocation.probe, True))
    store.remove(dataset.revocation.revoke)
    _verify(evaluator, Expectation(dataset.revocation.probe, False))


def _user(index: int) -> ObjectRef:
    return ObjectRef("user", f"u{index}")


def _group(index: int) -> ObjectRef:
    return ObjectRef("group", f"g{index}")


def _folder(index: int) -> ObjectRef:
    return ObjectRef("folder", f"f{index}")


def _document(index: int) -> ObjectRef:
    return ObjectRef("document", f"d{index}")


def _members(group: ObjectRef) -> SubjectRef:
    return SubjectRef(group, "member")


def _popularity(count: int) -> list[float]:
    return list(itertools.accumulate(1 / (rank + 1) for rank in range(count)))


class _Generator:
    def __init__(self, scale: Scale, seed: int) -> None:
        self._scale = scale
        self._seed = seed
        self._random = random.Random(seed)
        self._relationships: dict[Relationship, None] = {}
        self._group_popularity = _popularity(scale.groups)
        self._folder_popularity = _popularity(scale.folders)

    def generate(self) -> Dataset:
        self._nest_groups()
        self._join_groups()
        self._build_folders()
        self._file_documents()
        revocation = self._revocation_scenario()
        nesting = self._nesting_scenario()
        store = RelationshipStore(self._relationships)
        evaluator = ReferenceEvaluator(DOCUMENT_SHARING, store)
        expectations = _Sampler(self._random, self._scale, store, evaluator).sample()
        return Dataset(DOCUMENT_SHARING_NAME, DOCUMENT_SHARING, self._scale, self._seed, tuple(self._relationships),
                       expectations, revocation, nesting)

    def _relate(self, resource: ObjectRef, relation: str, subject: SubjectRef) -> Relationship:
        relationship = Relationship(resource, relation, subject)
        self._relationships[relationship] = None
        return relationship

    def _nest_groups(self) -> None:
        depth = [0] * self._scale.groups
        for child in range(1, self._scale.groups):
            if self._random.random() >= _NESTED_GROUP_SHARE:
                continue
            parent = self._random.randrange(child)
            if depth[parent] < MAX_GROUP_NESTING:
                depth[child] = depth[parent] + 1
                self._relate(_group(parent), "member", _members(_group(child)))

    def _join_groups(self) -> None:
        for user in range(self._scale.users):
            for group in self._pick_popular(self._group_popularity, self._heavy_tailed(8)):
                self._relate(_group(group), "member", SubjectRef(_user(user)))

    def _build_folders(self) -> None:
        depth = [0] * self._scale.folders
        for folder in range(self._scale.folders):
            if folder > 0:
                parent = self._random.randrange(folder)
                if depth[parent] < MAX_FOLDER_DEPTH:
                    depth[folder] = depth[parent] + 1
                    self._relate(_folder(folder), "parent", SubjectRef(_folder(parent)))
            if self._random.random() < _SHARED_FOLDER_SHARE:
                self._grant_viewers(_folder(folder), self._random.randint(1, 3))

    def _file_documents(self) -> None:
        for document in range(self._scale.documents):
            parent = self._pick_popular(self._folder_popularity, 1)[0]
            self._relate(_document(document), "parent", SubjectRef(_folder(parent)))
            self._grant_viewers(_document(document), self._heavy_tailed(4) - 1)

    def _grant_viewers(self, resource: ObjectRef, count: int) -> None:
        for _ in range(count):
            self._relate(resource, "viewer", self._viewer())

    def _viewer(self) -> SubjectRef:
        if self._random.random() < _GROUP_GRANT_SHARE:
            return _members(_group(self._pick_popular(self._group_popularity, 1)[0]))
        return SubjectRef(_user(self._random.randrange(self._scale.users)))

    def _pick_popular(self, popularity: list[float], count: int) -> list[int]:
        picks = self._random.choices(range(len(popularity)), cum_weights=popularity, k=count)
        return sorted(set(picks))

    def _heavy_tailed(self, cap: int) -> int:
        return min(cap, int(self._random.paretovariate(_PARETO_ALPHA)))

    def _revocation_scenario(self) -> RevocationScenario:
        target = ObjectRef("user", "revocation-target")
        team = ObjectRef("group", "revocation-team")
        root = ObjectRef("folder", "revocation-root")
        document = ObjectRef("document", "revocation-target")
        revoke = self._relate(team, "member", SubjectRef(target))
        self._relate(root, "viewer", _members(team))
        self._relate(document, "parent", SubjectRef(root))
        return RevocationScenario(revoke, CheckQuery(document, "view", target))

    def _nesting_scenario(self) -> Expectation:
        chain = [ObjectRef("group", f"nesting-{level}") for level in range(NESTING_CHAIN_DEPTH)]
        leaf = ObjectRef("user", "nesting-leaf")
        document = ObjectRef("document", "nesting-target")
        self._relate(document, "viewer", _members(chain[0]))
        for outer, inner in itertools.pairwise(chain):
            self._relate(outer, "member", _members(inner))
        self._relate(chain[-1], "member", SubjectRef(leaf))
        return Expectation(CheckQuery(document, "view", leaf), True)


def _verify(evaluator: ReferenceEvaluator, expectation: Expectation) -> None:
    if evaluator.check(expectation.query) is not expectation.allowed:
        raise DatasetError(f"the reference evaluator disagrees with {expectation}")


class _Sampler:
    def __init__(self, rng: random.Random, scale: Scale, store: RelationshipStore,
                 evaluator: ReferenceEvaluator) -> None:
        self._random = rng
        self._scale = scale
        self._store = store
        self._evaluator = evaluator
        self._ordered_members: dict[SubjectRef, list[SubjectRef]] = {}

    def sample(self) -> tuple[Expectation, ...]:
        allowed = self._collect(self._allowed_candidate, True, self._scale.checks // 2)
        denied = self._collect(self._random_candidate, False, self._scale.checks - len(allowed))
        expectations = allowed + denied
        self._random.shuffle(expectations)
        return tuple(expectations)

    def _collect(self, propose: Callable[[], CheckQuery | None], answer: bool, count: int) -> list[Expectation]:
        found: dict[CheckQuery, Expectation] = {}
        for _ in range(count * _SAMPLING_ATTEMPTS_PER_CHECK):
            if len(found) == count:
                break
            query = propose()
            if query is not None and query not in found and self._evaluator.check(query) is answer:
                found[query] = Expectation(query, answer)
        if len(found) < count:
            raise DatasetError(f"found {len(found)} of {count} {'allowed' if answer else 'denied'} checks")
        return list(found.values())

    def _random_candidate(self) -> CheckQuery:
        resource = _document(self._random.randrange(self._scale.documents))
        return CheckQuery(resource, "view", _user(self._random.randrange(self._scale.users)))

    def _allowed_candidate(self) -> CheckQuery | None:
        resource = _document(self._random.randrange(self._scale.documents))
        grants = sorted(self._viewers_up_the_folders(resource), key=str)
        if not grants:
            return None
        user = self._user_behind(self._random.choice(grants))
        return None if user is None else CheckQuery(resource, "view", user)

    def _viewers_up_the_folders(self, resource: ObjectRef) -> set[SubjectRef]:
        viewers: set[SubjectRef] = set()
        current: ObjectRef | None = resource
        while current is not None:
            viewers |= self._store.subjects(current, "viewer")
            parents = self._store.subjects(current, "parent")
            current = min(parents).object if parents else None
        return viewers

    def _user_behind(self, subject: SubjectRef) -> ObjectRef | None:
        for _ in range(MAX_GROUP_NESTING + 2):
            if subject.relation is None:
                return subject.object
            members = self._members_in_order(subject)
            if not members:
                return None
            subject = self._random.choice(members)
        return None

    def _members_in_order(self, userset: SubjectRef) -> list[SubjectRef]:
        if userset not in self._ordered_members:
            members = self._store.subjects(userset.object, userset.relation)
            self._ordered_members[userset] = sorted(members, key=str)
        return self._ordered_members[userset]
