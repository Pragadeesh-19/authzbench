from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import secrets

from authzbench.dataset import Dataset
from authzbench.engine import Consistency, Revision
from authzbench.reference import ReferenceEvaluator, RelationshipStore
from authzbench.relationships import Change, CheckQuery, ObjectRef
from authzbench.timing import NANOS_PER_SECOND, SYSTEM_CLOCK, Clock


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


_HEADER = _encode(json.dumps({"alg": "HS256", "typ": "at+jwt"}, separators=(",", ":")).encode("ascii"))


class TokenEngine:
    """The token baseline: an authorization server issues short-lived signed access tokens, and every check verifies
    the agent's token locally, as a resource server verifies a JWT: signature, expiry, audience and scope.

    An agent gets its token by exchanging its delegator's token (RFC 8693). The server issues one only while the graph
    grants the agent, and never with an expiry later than the delegator's token's, so a tree dies no later than its
    root's token. A revoke stops new tokens at once and leaves every issued token valid until it expires; a check
    without a valid token asks the server again. Every token lives one lifetime from its issue time, and its issue
    time is a seeded draw within the lifetime before the moment it was asked for, as if each agent had started at a
    random moment; the race could otherwise only ever start just after every token was issued. Expiry and issue time
    are on the driver's clock, in nanoseconds."""

    name = "tokens"
    version = "1"

    def __init__(self, lifetime_ns: int, clock: Clock = SYSTEM_CLOCK) -> None:
        if lifetime_ns <= 0:
            raise ValueError(f"a token lifetime must be positive, not {lifetime_ns} ns")
        self._lifetime_ns = lifetime_ns
        self._clock = clock
        self._key = secrets.token_bytes(32)
        self._store = RelationshipStore()
        self._evaluator: ReferenceEvaluator | None = None
        self._delegators: dict[ObjectRef, ObjectRef] = {}
        self._ages = random.Random(0)
        self._tokens: dict[CheckQuery, str] = {}
        self._revision = 0

    def modes(self) -> dict[Consistency, str]:
        return {Consistency.FASTEST: f"signed token, {self._lifetime_ns / NANOS_PER_SECOND:g} s lifetime, "
                                     f"checked locally"}

    def load(self, dataset: Dataset) -> None:
        self._store = RelationshipStore(dataset.relationships)
        self._evaluator = ReferenceEvaluator(dataset.model, self._store)
        self._delegators = {delegation.delegate: delegation.delegator for delegation in dataset.delegation.delegations}
        self._ages = random.Random(dataset.seed)
        self._tokens.clear()

    def write(self, change: Change) -> Revision:
        self._store.apply(change)
        self._revision += 1
        return Revision(self._revision)

    def current_revision(self) -> Revision:
        return Revision(self._revision)

    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool:
        return self._claims(query) is not None

    def close(self) -> None:
        self._tokens.clear()

    def token(self, query: CheckQuery) -> dict | None:
        """The claims of the agent's current token for this check, if it holds a valid one."""
        return self._verify(self._tokens.get(query), query)

    def _claims(self, query: CheckQuery) -> dict | None:
        claims = self._verify(self._tokens.get(query), query)
        return claims if claims is not None else self._exchange(query)

    def _exchange(self, query: CheckQuery) -> dict | None:
        self._tokens.pop(query, None)
        if not self._require_loaded().check(query):
            return None
        now = self._clock.now()
        issued = now - self._ages.randrange(self._lifetime_ns)
        expires = issued + self._lifetime_ns
        delegator = self._delegators.get(query.subject)
        if delegator is not None:
            parent = self._claims(CheckQuery(query.resource, query.permission, delegator))
            if parent is None:
                return None
            expires = min(expires, parent["exp"])
        claims = {"sub": str(query.subject), "aud": str(query.resource), "scope": query.permission,
                  "iat": issued, "exp": expires}
        self._tokens[query] = self._sign(claims)
        return claims

    def _sign(self, claims: dict) -> str:
        payload = _encode(json.dumps(claims, separators=(",", ":")).encode("ascii"))
        signature = hmac.new(self._key, f"{_HEADER}.{payload}".encode("ascii"), hashlib.sha256).digest()
        return f"{_HEADER}.{payload}.{_encode(signature)}"

    def _verify(self, token: str | None, query: CheckQuery) -> dict | None:
        if token is None:
            return None
        header, payload, signature = token.split(".")
        expected = hmac.new(self._key, f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            return None
        claims = json.loads(_decode(payload))
        if claims["exp"] <= self._clock.now():
            return None
        if (claims["sub"], claims["aud"], claims["scope"]) != (str(query.subject), str(query.resource),
                                                                query.permission):
            return None
        return claims

    def _require_loaded(self) -> ReferenceEvaluator:
        if self._evaluator is None:
            raise RuntimeError("load a dataset before checking")
        return self._evaluator
