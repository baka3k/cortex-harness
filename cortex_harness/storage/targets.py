"""Canonical, credential-free identities for effective storage targets.

The backend selector is only an operator request.  Journal and generation
compatibility must instead bind the targets that a run will actually mutate,
including mixed-mode fallback and the emergency force-local override.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit


TARGET_SCHEMA_VERSION = 1
ENV_EFFECTIVE_GRAPH_TARGET = "CORTEX_EFFECTIVE_GRAPH_TARGET"
ENV_EFFECTIVE_GRAPH_FINGERPRINT = "CORTEX_EFFECTIVE_GRAPH_TARGET_FINGERPRINT"
ENV_EFFECTIVE_VECTOR_TARGET = "CORTEX_EFFECTIVE_VECTOR_TARGET"
ENV_EFFECTIVE_VECTOR_FINGERPRINT = "CORTEX_EFFECTIVE_VECTOR_TARGET_FINGERPRINT"
ENV_EFFECTIVE_TOPOLOGY = "CORTEX_EFFECTIVE_STORAGE_TOPOLOGY"
ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT = "CORTEX_EFFECTIVE_STORAGE_TOPOLOGY_FINGERPRINT"

_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
    "redis": 6379,
    "rediss": 6379,
    "falkor": 6379,
    "falkors": 6379,
    "bolt": 7687,
    "bolt+s": 7687,
    "neo4j": 7687,
    "neo4j+s": 7687,
    "neo4j+ssc": 7687,
}
_TLS_SCHEMES = frozenset({"https", "rediss", "falkors", "bolt+s", "neo4j+s", "neo4j+ssc"})


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _credential_fingerprint(*values: Optional[str]) -> Optional[str]:
    material = "\0".join(str(value or "") for value in values)
    return f"sha256:{_digest(material)}" if material.strip("\0") else None


def _role_value(role: object) -> str:
    value = getattr(role, "value", role)
    normalized = str(value or "").strip().casefold()
    if normalized == "document":
        normalized = "doc"
    if normalized not in {"code", "doc"}:
        raise ValueError(f"storage role must be 'code' or 'doc'; got {role!r}")
    return normalized


def canonical_local_target(path: str | Path) -> str:
    """Return an absolute canonical path without requiring it to exist."""

    return str(Path(path).expanduser().resolve())


def canonical_remote_endpoint(value: str, *, default_scheme: str) -> tuple[str, Optional[str]]:
    """Normalize an endpoint and remove userinfo, query strings, and fragments.

    The returned principal is suitable only as input to a one-way tenancy
    fingerprint.  It is never included in the canonical endpoint itself.
    """

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("remote storage endpoint must not be empty")
    candidate = raw if "://" in raw else f"{default_scheme}://{raw}"
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.casefold()
    if not scheme:
        raise ValueError(f"remote storage endpoint has no scheme: {value!r}")
    if scheme == "unix":
        if not parsed.path:
            raise ValueError("unix storage endpoint requires an absolute socket path")
        return urlunsplit((scheme, "", canonical_local_target(parsed.path), "", "")), parsed.username

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"remote storage endpoint has no host: {value!r}")
    host = hostname.casefold().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"remote storage endpoint has an invalid port: {value!r}") from exc
    port = port or _DEFAULT_PORTS.get(scheme)
    netloc = f"{host}:{port}" if port is not None else host
    path = parsed.path.rstrip("/")
    canonical = urlunsplit((scheme, netloc, path, "", ""))
    principal_material = "\0".join(
        item for item in (parsed.username, parsed.password) if item is not None
    ) or None
    return canonical, principal_material


def endpoint_uses_tls(endpoint: str, *, explicit: bool = False) -> bool:
    scheme = urlsplit(endpoint if "://" in endpoint else f"//{endpoint}").scheme.casefold()
    return bool(explicit or scheme in _TLS_SCHEMES)


@dataclass(frozen=True)
class EffectiveStorageTarget:
    """One effective graph or vector materialization target."""

    component: str
    provider: str
    mode: str
    location: str
    namespace: str
    role: str
    tls: bool = False
    principal_fingerprint: Optional[str] = None
    capability_fingerprint: Optional[str] = None
    schema_fingerprint: Optional[str] = None
    schema_version: int = TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.component not in {"graph", "vector"}:
            raise ValueError("storage target component must be graph or vector")
        if self.mode not in {"file", "remote"}:
            raise ValueError("storage target mode must be file or remote")
        if not self.provider.strip() or not self.location.strip() or not self.namespace.strip():
            raise ValueError("storage target provider, location, and namespace must not be empty")
        object.__setattr__(self, "provider", self.provider.strip().casefold())
        object.__setattr__(self, "role", _role_value(self.role))

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EffectiveStorageTarget":
        return cls(**dict(value))

    @classmethod
    def from_json(cls, value: str) -> "EffectiveStorageTarget":
        payload = json.loads(value)
        if not isinstance(payload, Mapping):
            raise ValueError("effective storage target must be a JSON object")
        return cls.from_dict(payload)

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def fingerprint(self) -> str:
        return f"storage-target:v{self.schema_version}:{_digest(self.canonical_json)}"


@dataclass(frozen=True)
class EffectiveStorageTopology:
    """The complete graph/vector topology resolved before run creation."""

    project_scope: str
    requested_backend: str
    forced_local: bool
    graph: EffectiveStorageTarget
    vector: EffectiveStorageTarget
    generation_id: str = "unbound"
    schema_version: int = TARGET_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_scope": self.project_scope,
            "requested_backend": self.requested_backend,
            "forced_local": self.forced_local,
            "generation_id": self.generation_id,
            "graph": self.graph.to_dict(),
            "vector": self.vector.to_dict(),
        }

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def fingerprint(self) -> str:
        return f"storage-topology:v{self.schema_version}:{_digest(self.canonical_json)}"

    @property
    def graph_fingerprint(self) -> str:
        return self.graph.fingerprint

    @property
    def vector_fingerprint(self) -> str:
        return self.vector.fingerprint


def remote_graph_target(
    uri: str,
    *,
    graph: str,
    role: object,
    password: Optional[str] = None,
    principal: Optional[str] = None,
    ssl: bool = False,
    provider: str = "falkordb",
) -> EffectiveStorageTarget:
    default_scheme = "redis" if provider.casefold() == "falkordb" else "bolt"
    location, uri_principal = canonical_remote_endpoint(uri, default_scheme=default_scheme)
    return EffectiveStorageTarget(
        component="graph",
        provider=provider,
        mode="remote",
        location=location,
        namespace=str(graph),
        role=_role_value(role),
        tls=endpoint_uses_tls(location, explicit=ssl),
        principal_fingerprint=_credential_fingerprint(principal or uri_principal, password),
    )


def local_graph_target(path: str | Path, *, graph: str, role: object) -> EffectiveStorageTarget:
    return EffectiveStorageTarget(
        component="graph",
        provider="falkordb",
        mode="file",
        location=canonical_local_target(path),
        namespace=str(graph),
        role=_role_value(role),
    )


def remote_vector_target(
    url: str,
    *,
    collection: str,
    role: object,
    api_key: Optional[str] = None,
) -> EffectiveStorageTarget:
    location, principal = canonical_remote_endpoint(url, default_scheme="http")
    return EffectiveStorageTarget(
        component="vector",
        provider="qdrant",
        mode="remote",
        location=location,
        namespace=str(collection),
        role=_role_value(role),
        tls=endpoint_uses_tls(location),
        principal_fingerprint=_credential_fingerprint(principal, api_key),
    )


def local_vector_target(path: str | Path, *, collection: str, role: object) -> EffectiveStorageTarget:
    return EffectiveStorageTarget(
        component="vector",
        provider="qdrant",
        mode="file",
        location=canonical_local_target(path),
        namespace=str(collection),
        role=_role_value(role),
    )


def effective_graph_target_from_env(env: Mapping[str, str]) -> EffectiveStorageTarget:
    """Resolve the graph target from a sanitized descriptor or legacy env shape."""

    descriptor = str(env.get(ENV_EFFECTIVE_GRAPH_TARGET) or "").strip()
    if descriptor:
        return EffectiveStorageTarget.from_json(descriptor)
    role = env.get("CORTEX_STORAGE_OWNER") or "code"
    provider = (env.get("CODE_GRAPH_PROVIDER") or env.get("GRAPH_PROVIDER") or "falkordb").casefold()
    if provider in {"neo4j", "neo"}:
        return remote_graph_target(
            env.get("NEO4J_URI") or "bolt://localhost:7687",
            graph=env.get("NEO4J_DB") or "neo4j",
            role=role,
            password=env.get("NEO4J_PASSWORD"),
            principal=env.get("NEO4J_USER"),
            provider="neo4j",
        )
    graph = env.get("FALKORDB_GRAPH") or env.get("FALKORDB_DATABASE") or "hyper_graph"
    uri = str(env.get("FALKORDB_URI") or "").strip()
    if uri:
        return remote_graph_target(
            uri,
            graph=graph,
            role=role,
            password=env.get("FALKORDB_PASSWORD"),
            ssl=str(env.get("FALKORDB_SSL") or "").strip().casefold() not in {"", "0", "false", "no", "off"},
        )
    return local_graph_target(env.get("FALKORDB_PATH") or "embedded", graph=graph, role=role)


__all__ = [
    "ENV_EFFECTIVE_GRAPH_FINGERPRINT",
    "ENV_EFFECTIVE_GRAPH_TARGET",
    "ENV_EFFECTIVE_TOPOLOGY",
    "ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT",
    "ENV_EFFECTIVE_VECTOR_FINGERPRINT",
    "ENV_EFFECTIVE_VECTOR_TARGET",
    "EffectiveStorageTarget",
    "EffectiveStorageTopology",
    "TARGET_SCHEMA_VERSION",
    "canonical_local_target",
    "canonical_remote_endpoint",
    "effective_graph_target_from_env",
    "endpoint_uses_tls",
    "local_graph_target",
    "local_vector_target",
    "remote_graph_target",
    "remote_vector_target",
]
