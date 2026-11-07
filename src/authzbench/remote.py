"""An engine on another machine: a small HTTP server for the engine and a client the driver measures through.

The server holds one engine with its dataset already loaded, answers only the addresses it is told to allow, and
speaks JSON over persistent HTTP/1.1 connections, so a check costs one round trip and no connection setup."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Collection
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import urlsplit

from authzbench.dataset import Dataset
from authzbench.engine import Consistency, Engine, Revision
from authzbench.relationships import Change, CheckQuery, Operation, Relationship


class RemoteError(RuntimeError):
    pass


class ServedEngine(Engine, Protocol):
    def current_revision(self) -> Revision: ...


@dataclass(frozen=True)
class DatasetIdentity:
    model: str
    scale: str
    seed: int
    relationships: int
    checks: int

    @classmethod
    def of(cls, dataset: Dataset) -> DatasetIdentity:
        return cls(dataset.model_name, dataset.scale.name, dataset.seed, len(dataset.relationships),
                   len(dataset.expectations))


class EngineServer:
    def __init__(self, engine: ServedEngine, dataset: Dataset, address: tuple[str, int],
                 allowed: Collection[str]) -> None:
        if not allowed:
            raise ValueError("an engine server needs at least one allowed address")
        self._engine = engine
        self._identity = DatasetIdentity.of(dataset)
        self._allowed = frozenset(allowed)
        self._lock = threading.Lock()
        self._http = _AllowListServer(address, _handler_for(self), self._allowed)

    @property
    def address(self) -> tuple[str, int]:
        return self._http.server_address[:2]

    def serve_forever(self) -> None:
        self._http.serve_forever()

    def start(self) -> EngineServer:
        threading.Thread(target=self._http.serve_forever, name="engine-server", daemon=True).start()
        return self

    def stop(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def describe(self) -> dict:
        with self._lock:
            return {"engine": self._engine.name, "version": self._engine.version,
                    "modes": {consistency.value: name for consistency, name in self._engine.modes().items()},
                    "dataset": asdict(self._identity), "revision": self._engine.current_revision().value}

    def write(self, request: dict) -> dict:
        change = Change(Operation(request["operation"]), Relationship.parse(request["relationship"]))
        with self._lock:
            return {"revision": self._engine.write(change).value}

    def check(self, request: dict) -> dict:
        after = None if request.get("after") is None else Revision(request["after"])
        query = CheckQuery.parse(request["query"])
        with self._lock:
            return {"allowed": self._engine.check(query, Consistency(request["consistency"]), after)}


class RemoteEngine:
    """An engine reached over the network; every write and check is one request on a kept-alive connection."""

    def __init__(self, url: str, timeout_s: float = 60.0) -> None:
        parts = urlsplit(url)
        if parts.scheme != "http" or not parts.hostname or not parts.port:
            raise ValueError(f"an engine server address looks like http://10.0.0.5:7700, not {url!r}")
        self._connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout_s)
        self._description = self._call("GET", "/describe")
        self.name = self._description["engine"]

    @property
    def version(self) -> str:
        return self._description["version"]

    def current_revision(self) -> Revision:
        return Revision(self._call("GET", "/describe")["revision"])

    def modes(self) -> dict[Consistency, str]:
        return {Consistency(consistency): name for consistency, name in self._description["modes"].items()}

    def load(self, dataset: Dataset) -> None:
        served = self._description["dataset"]
        expected = asdict(DatasetIdentity.of(dataset))
        if served != expected:
            raise RemoteError(f"the engine server holds dataset {served}, not {expected}")

    def write(self, change: Change) -> Revision:
        body = {"operation": change.operation.value, "relationship": str(change.relationship)}
        return Revision(self._call("POST", "/write", body)["revision"])

    def check(self, query: CheckQuery, consistency: Consistency, after: Revision | None = None) -> bool:
        body = {"query": str(query), "consistency": consistency.value,
                "after": None if after is None else after.value}
        return self._call("POST", "/check", body)["allowed"]

    def close(self) -> None:
        self._connection.close()

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {} if payload is None else {"Content-Type": "application/json"}
        try:
            self._connection.request(method, path, body=payload, headers=headers)
            response = self._connection.getresponse()
            content = json.loads(response.read() or b"{}")
        except (OSError, http.client.HTTPException) as failure:
            self._connection.close()
            raise RemoteError(f"the engine server did not answer {method} {path}: {failure}") from failure
        if response.status != 200:
            raise RemoteError(f"the engine server refused {method} {path}: {content.get('error', response.status)}")
        return content


class _AllowListServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler],
                 allowed: frozenset[str]) -> None:
        self._allowed = allowed
        super().__init__(address, handler)

    def verify_request(self, request, client_address) -> bool:
        return client_address[0] in self._allowed


def _handler_for(server: EngineServer) -> type[BaseHTTPRequestHandler]:
    routes = {("GET", "/describe"): lambda _: server.describe(), ("POST", "/write"): server.write,
              ("POST", "/check"): server.check}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._answer("GET")

        def do_POST(self) -> None:
            self._answer("POST")

        def log_message(self, format, *args) -> None:
            return

        def _answer(self, method: str) -> None:
            route = routes.get((method, self.path))
            if route is None:
                self._send(404, {"error": f"no {method} {self.path}"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                request = json.loads(self.rfile.read(length)) if length else {}
                self._send(200, route(request))
            except (KeyError, ValueError) as failure:
                self._send(400, {"error": str(failure)})
            except Exception as failure:
                self._send(500, {"error": f"{type(failure).__name__}: {failure}"})

        def _send(self, status: int, content: dict) -> None:
            body = json.dumps(content).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
