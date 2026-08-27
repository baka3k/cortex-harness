"""Canonical local-storage configuration for Cortex Harness.

Physical storage is application data owned by a harness instance and a stable
process owner.  It is deliberately independent from the source-project path;
``project_id`` only selects logical graphs and collections inside an owner's
store.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .contracts import PerformanceProfile
from .targets import canonical_remote_endpoint, environment_flag_enabled


STORAGE_SCHEMA_VERSION = "v1"
DEFAULT_INSTANCE_ID = "default"
DEFAULT_DATA_DIRNAME = ".cortext-harness"
DEFAULT_QDRANT_PATH = Path(STORAGE_SCHEMA_VERSION) / "instances" / DEFAULT_INSTANCE_ID / "qdrant"
DEFAULT_FALKORDB_PATH = (
    Path(STORAGE_SCHEMA_VERSION) / "instances" / DEFAULT_INSTANCE_ID / "falkordb" / "code" / "data.rdb"
)

ENV_DATA_HOME = "CORTEX_DATA_HOME"
ENV_INSTANCE = "CORTEX_STORAGE_INSTANCE"
ENV_CODE_OWNER = "CORTEX_CODE_STORAGE_OWNER"
ENV_DOC_OWNER = "CORTEX_DOC_STORAGE_OWNER"
ENV_QDRANT_BASE = "QDRANT_PATH"
ENV_QDRANT_CODE = "QDRANT_CODE_PATH"
ENV_QDRANT_DOC = "QDRANT_DOC_PATH"
ENV_FALKORDB_PATH = "FALKORDB_PATH"
ENV_FALKORDB_CODE = "FALKORDB_CODE_PATH"
ENV_FALKORDB_DOC = "FALKORDB_DOC_PATH"
ENV_PERFORMANCE_PROFILE = "CORTEX_STORAGE_PROFILE"

CFG_DATA_HOME = ENV_DATA_HOME
CFG_INSTANCE = ENV_INSTANCE
CFG_CODE_OWNER = ENV_CODE_OWNER
CFG_DOC_OWNER = ENV_DOC_OWNER
CFG_QDRANT_BASE = ENV_QDRANT_BASE
CFG_QDRANT_CODE = ENV_QDRANT_CODE
CFG_QDRANT_DOC = ENV_QDRANT_DOC
CFG_FALKORDB_PATH = ENV_FALKORDB_PATH
CFG_FALKORDB_CODE = ENV_FALKORDB_CODE
CFG_FALKORDB_DOC = ENV_FALKORDB_DOC
CFG_PERFORMANCE_PROFILE = ENV_PERFORMANCE_PROFILE

LEGACY_REMOTE_KEYS: tuple[str, ...] = (
    "QDRANT_URL", "QDRANT_HOST", "QDRANT_PORT", "QDRANT_API_KEY",
    "FALKORDB_URI", "FALKORDB_URL", "FALKORDB_HOST", "FALKORDB_PORT",
    "FALKORDB_USER", "FALKORDB_PASSWORD", "FALKORDB_SSL",
)

_IDENTITY_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")


class LegacyRemoteConfigurationError(ValueError):
    """A local configuration also contains an ambiguous remote endpoint."""


class InvalidStorageIdentityError(ValueError):
    """An instance or owner identifier is not a stable filesystem slug."""


class StorageRole(str, Enum):
    CODE = "code"
    DOCUMENT = "doc"


class QdrantStorageRole(str, Enum):
    CODE = "code"
    DOCUMENT = "doc"


class BackendMode(str, Enum):
    """Storage backend selection per project.

    ``LOCAL`` (default) keeps all state on the local filesystem using the
    existing Qdrant local mode + FalkorDBLite ``.rdb``. ``REMOTE`` connects to
    user-supplied Qdrant and/or FalkorDB server endpoints. The factory in
    :mod:`cortex_harness.storage.factory` resolves these into concrete
    backend instances.
    """

    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class RemoteStorageConfig:
    """Connection details for the remote backend.

    ``__repr__`` deliberately redacts credentials; any log line that prints
    a ``RemoteStorageConfig`` must never leak API keys or passwords.
    """

    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    falkordb_uri: Optional[str] = None
    falkordb_password: Optional[str] = None
    falkordb_ssl: bool = False

    def __repr__(self) -> str:
        def safe_endpoint(value: Optional[str], default_scheme: str) -> Optional[str]:
            if not value:
                return None
            try:
                endpoint, _ = canonical_remote_endpoint(value, default_scheme=default_scheme)
                return endpoint
            except ValueError:
                return "<invalid endpoint>"

        return (
            f"RemoteStorageConfig(qdrant_url={safe_endpoint(self.qdrant_url, 'http')!r}, "
            f"qdrant_api_key=***, "
            f"falkordb_uri={safe_endpoint(self.falkordb_uri, 'redis')!r}, "
            f"falkordb_password=***, falkordb_ssl={self.falkordb_ssl})"
        )


def validate_backend_config(
    backend: str,
    remote: Optional[Mapping[str, Any]],
) -> tuple[BackendMode, Optional[RemoteStorageConfig]]:
    """Validate ``storage_backend`` and remote config completeness.

    Returns the resolved :class:`BackendMode` and a
    :class:`RemoteStorageConfig` when the mode is ``REMOTE``. ``remote`` may
    be ``None`` when the mode is ``LOCAL``. When the mode is ``REMOTE`` the
    caller must supply at least one of ``qdrant_url`` or ``falkordb_uri``;
    an empty remote section raises ``ValueError``.
    """
    try:
        mode = BackendMode(backend)
    except ValueError as exc:
        raise InvalidStorageIdentityError(
            f"storage_backend must be 'local' or 'remote'; got {backend!r}"
        ) from exc
    if mode == BackendMode.LOCAL:
        return mode, None
    if remote is None:
        raise ValueError(
            "storage_backend='remote' requires a 'remote' section "
            "with at least qdrant_url or falkordb_uri"
        )
    config = RemoteStorageConfig(
        qdrant_url=_nonempty_or(remote.get("qdrant_url")),
        qdrant_api_key=_nonempty_or(remote.get("qdrant_api_key")),
        falkordb_uri=_nonempty_or(remote.get("falkordb_uri")),
        falkordb_password=_nonempty_or(remote.get("falkordb_password")),
        falkordb_ssl=bool(remote.get("falkordb_ssl", False)),
    )
    if not config.qdrant_url and not config.falkordb_uri:
        raise ValueError(
            "remote config must specify at least qdrant_url or falkordb_uri"
        )
    return mode, config


def _nonempty_or(value: object) -> Optional[str]:
    """Return ``None`` for empty/whitespace strings, otherwise the trimmed str."""
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def validate_storage_identity(value: object, *, field_name: str) -> str:
    candidate = str(value or "").strip().casefold()
    if not _IDENTITY_RE.fullmatch(candidate):
        raise InvalidStorageIdentityError(
            f"{field_name} must be a stable lowercase slug containing only letters, "
            "digits, '-' or '_' (1-64 characters); got {value!r}"
        )
    return candidate


def default_data_home() -> Path:
    """Return the centralized per-account data root.

    ``Path.home`` is evaluated at call time so tests and managed runtimes can
    replace the account home without import-time leakage.
    """
    return Path.home() / DEFAULT_DATA_DIRNAME


def resolve_performance_profile(
    *, config: Optional[Mapping[str, object]] = None, profile: object = None
) -> PerformanceProfile:
    """Resolve the owner performance profile without scattered env defaults.

    ``balanced`` is accepted as an explicit operator choice; the default is
    always the one-handle-per-lane ``safe`` profile until benchmark evidence
    promotes another configuration.
    """
    cfg = dict(config or {})
    selected = str(_select(profile, _nonempty(cfg, CFG_PERFORMANCE_PROFILE), os.getenv(ENV_PERFORMANCE_PROFILE), "safe")).casefold()
    if selected not in {"safe", "balanced", "custom"}:
        raise ValueError("CORTEX_STORAGE_PROFILE must be safe, balanced, or custom")
    values = {"name": selected}
    if selected == "custom":
        field_map = {
            "graph_readers": "CORTEX_STORAGE_GRAPH_READERS",
            "vector_readers": "CORTEX_STORAGE_VECTOR_READERS",
            "writer_slots": "CORTEX_STORAGE_WRITER_SLOTS",
            "control_slots": "CORTEX_STORAGE_CONTROL_SLOTS",
            "max_queue_items": "CORTEX_STORAGE_MAX_QUEUE_ITEMS",
            "max_queue_bytes": "CORTEX_STORAGE_MAX_QUEUE_BYTES",
            "request_timeout_seconds": "CORTEX_STORAGE_REQUEST_TIMEOUT_SECONDS",
            "disk_safety_fraction": "CORTEX_STORAGE_DISK_SAFETY_FRACTION",
        }
        for field_name, key in field_map.items():
            raw = _select(_nonempty(cfg, key), os.getenv(key))
            if raw is None:
                continue
            converter = float if field_name in {"request_timeout_seconds", "disk_safety_fraction"} else int
            try:
                values[field_name] = converter(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} is invalid: {raw!r}") from exc
    return PerformanceProfile(**values)


@dataclass(frozen=True)
class ResolvedStorage:
    project_root: Path
    qdrant_base: Path
    qdrant_code_path: Path
    qdrant_doc_path: Path
    falkordb_path: Path
    code_graph: Optional[str] = None
    doc_graph: Optional[str] = None
    code_collection: Optional[str] = None
    doc_collection: Optional[str] = None
    data_root: Optional[Path] = None
    schema_version: str = STORAGE_SCHEMA_VERSION
    instance_id: str = DEFAULT_INSTANCE_ID
    code_owner_id: str = StorageRole.CODE.value
    doc_owner_id: str = StorageRole.DOCUMENT.value
    instance_root: Optional[Path] = None
    falkordb_code_path: Optional[Path] = None
    falkordb_doc_path: Optional[Path] = None
    manifest_path: Optional[Path] = None
    backups_path: Optional[Path] = None
    path_provenance: str = "derived-default"
    backend_mode: BackendMode = BackendMode.LOCAL
    remote: Optional[RemoteStorageConfig] = None
    _legacy_keys: tuple[str, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        # Compatibility for callers/tests that still instantiate the original
        # five-path shape directly.
        data_root = self.data_root or self.qdrant_base.parent
        instance_root = self.instance_root or self.qdrant_base.parent
        falkor_code = self.falkordb_code_path or self.falkordb_path
        falkor_doc = self.falkordb_doc_path or (self.falkordb_path.parent.parent / "doc" / "data.rdb")
        object.__setattr__(self, "data_root", Path(data_root))
        object.__setattr__(self, "instance_root", Path(instance_root))
        object.__setattr__(self, "falkordb_code_path", Path(falkor_code))
        object.__setattr__(self, "falkordb_doc_path", Path(falkor_doc))
        object.__setattr__(self, "manifest_path", self.manifest_path or Path(instance_root) / "manifest.json")
        object.__setattr__(self, "backups_path", self.backups_path or Path(instance_root) / "backups")

    @property
    def has_legacy_keys(self) -> bool:
        return bool(self._legacy_keys)

    @property
    def qdrant_path(self) -> Path:
        return self.qdrant_code_path

    def path_for_role(self, role: QdrantStorageRole | StorageRole | str) -> Path:
        value = role.value if isinstance(role, Enum) else str(role)
        if value == StorageRole.CODE.value:
            return self.qdrant_code_path
        if value in {StorageRole.DOCUMENT.value, "document"}:
            return self.qdrant_doc_path
        raise ValueError(f"Unknown storage role: {role!r}")

    def falkordb_path_for_role(self, role: StorageRole | QdrantStorageRole | str) -> Path:
        value = role.value if isinstance(role, Enum) else str(role)
        if value == StorageRole.CODE.value:
            return Path(self.falkordb_code_path)
        if value in {StorageRole.DOCUMENT.value, "document"}:
            return Path(self.falkordb_doc_path)
        raise ValueError(f"Unknown storage role: {role!r}")

    def ensure_directories(self) -> None:
        self.qdrant_code_path.mkdir(parents=True, exist_ok=True)
        self.qdrant_doc_path.mkdir(parents=True, exist_ok=True)
        Path(self.falkordb_code_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.falkordb_doc_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.backups_path).mkdir(parents=True, exist_ok=True)


def _nonempty(mapping: Mapping[str, object], key: str) -> Optional[str]:
    value = mapping.get(key)
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _select(*values: object) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        rendered = str(value).strip()
        if rendered:
            return rendered
    return None


def _resolve_override(value: Optional[str], project_root: Path) -> Optional[Path]:
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _legacy_keys(config: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(key for key in LEGACY_REMOTE_KEYS if _nonempty(config, key))


def resolve_storage(
    project_root: Path,
    *,
    config: Optional[Mapping[str, object]] = None,
    data_home: object = None,
    instance_id: object = None,
    code_owner_id: object = None,
    doc_owner_id: object = None,
    qdrant_base: object = None,
    qdrant_code_path: object = None,
    qdrant_doc_path: object = None,
    falkordb_path: object = None,
    falkordb_code_path: object = None,
    falkordb_doc_path: object = None,
    code_graph: Optional[str] = None,
    doc_graph: Optional[str] = None,
    code_collection: Optional[str] = None,
    doc_collection: Optional[str] = None,
) -> ResolvedStorage:
    """Resolve local paths using CLI > config > environment > derived default."""
    root = Path(project_root).resolve()
    cfg = dict(config or {})
    legacy = _legacy_keys(cfg)
    local_keys = (CFG_DATA_HOME, CFG_QDRANT_BASE, CFG_QDRANT_CODE, CFG_QDRANT_DOC,
                  CFG_FALKORDB_PATH, CFG_FALKORDB_CODE, CFG_FALKORDB_DOC)
    backend_raw = _nonempty(cfg, "storage_backend") or "local"
    remote_section = cfg.get("remote")
    backend_mode, remote_config = validate_backend_config(backend_raw, remote_section)
    if backend_mode == BackendMode.REMOTE and legacy:
        # User supplied both legacy and the new remote section. Legacy keys remain
        # rejected to avoid mixing two endpoint sources; surface them clearly.
        mode = "mixed local/remote" if any(_nonempty(cfg, key) for key in local_keys) else "remote-only"
        raise LegacyRemoteConfigurationError(
            f"{mode} database configuration is unsupported for the local runtime: "
            + ", ".join(legacy)
            + ". Remove endpoint/credential fields or move them to the 'remote' "
              "section of your project config."
        )
    if legacy:
        mode = "mixed local/remote" if any(_nonempty(cfg, key) for key in local_keys) else "remote-only"
        raise LegacyRemoteConfigurationError(
            f"{mode} database configuration is unsupported for the local runtime: "
            + ", ".join(legacy)
            + ". Export or re-ingest remote data, remove endpoint/credential fields, "
              "then configure CORTEX_DATA_HOME or owner-specific local paths."
        )

    data_raw = _select(data_home, _nonempty(cfg, CFG_DATA_HOME), os.environ.get(ENV_DATA_HOME))
    if data_raw:
        expanded = Path(data_raw).expanduser()
        if expanded.is_absolute():
            data_root = expanded.resolve()
            provenance = "explicit-absolute-override"
        else:
            # A bare name like ``"sampledb"`` MUST NOT be silently anchored to
            # ``project_root``; that traps data inside source trees (regression
            # introduced by commit 2704da8 in 2026-08) and breaks the
            # centralized per-account data-home contract documented at the
            # top of this module. Mirror ``dev init``'s "blank = account
            # default" prompt contract: treat relative names as sub-paths
            # under ``~/.cortext-harness`` so siblings stay co-located.
            data_root = (default_data_home() / expanded).resolve()
            provenance = "explicit-relative-anchored-to-home"
    else:
        data_root = default_data_home().resolve()
        provenance = "account-home-default"

    instance = validate_storage_identity(
        _select(instance_id, _nonempty(cfg, CFG_INSTANCE), os.environ.get(ENV_INSTANCE), DEFAULT_INSTANCE_ID),
        field_name="instance_id",
    )
    code_owner = validate_storage_identity(
        _select(code_owner_id, _nonempty(cfg, CFG_CODE_OWNER), os.environ.get(ENV_CODE_OWNER), StorageRole.CODE.value),
        field_name="code_owner_id",
    )
    doc_owner = validate_storage_identity(
        _select(doc_owner_id, _nonempty(cfg, CFG_DOC_OWNER), os.environ.get(ENV_DOC_OWNER), StorageRole.DOCUMENT.value),
        field_name="doc_owner_id",
    )
    if code_owner == doc_owner:
        raise InvalidStorageIdentityError("code_owner_id and doc_owner_id must be distinct")

    instance_root = data_root / STORAGE_SCHEMA_VERSION / "instances" / instance
    q_base_raw = _select(qdrant_base, _nonempty(cfg, CFG_QDRANT_BASE), os.environ.get(ENV_QDRANT_BASE))
    q_base = _resolve_override(q_base_raw, root) if q_base_raw else instance_root / "qdrant"
    q_code_raw = _select(qdrant_code_path, _nonempty(cfg, CFG_QDRANT_CODE), os.environ.get(ENV_QDRANT_CODE))
    q_doc_raw = _select(qdrant_doc_path, _nonempty(cfg, CFG_QDRANT_DOC), os.environ.get(ENV_QDRANT_DOC))
    q_code = _resolve_override(q_code_raw, root) if q_code_raw else q_base / code_owner
    q_doc = _resolve_override(q_doc_raw, root) if q_doc_raw else q_base / doc_owner

    shared_falkor = _select(falkordb_path, _nonempty(cfg, CFG_FALKORDB_PATH), os.environ.get(ENV_FALKORDB_PATH))
    f_code_raw = _select(falkordb_code_path, _nonempty(cfg, CFG_FALKORDB_CODE), os.environ.get(ENV_FALKORDB_CODE), shared_falkor)
    f_doc_raw = _select(falkordb_doc_path, _nonempty(cfg, CFG_FALKORDB_DOC), os.environ.get(ENV_FALKORDB_DOC))
    f_code = _resolve_override(f_code_raw, root) if f_code_raw else instance_root / "falkordb" / code_owner / "data.rdb"
    f_doc = _resolve_override(f_doc_raw, root) if f_doc_raw else instance_root / "falkordb" / doc_owner / "data.rdb"

    return ResolvedStorage(
        project_root=root, data_root=data_root, schema_version=STORAGE_SCHEMA_VERSION,
        instance_id=instance, code_owner_id=code_owner, doc_owner_id=doc_owner,
        instance_root=instance_root, qdrant_base=q_base, qdrant_code_path=q_code,
        qdrant_doc_path=q_doc, falkordb_path=f_code, falkordb_code_path=f_code,
        falkordb_doc_path=f_doc, manifest_path=instance_root / "manifest.json",
        backups_path=instance_root / "backups", path_provenance=provenance,
        code_graph=code_graph, doc_graph=doc_graph,
        code_collection=code_collection, doc_collection=doc_collection,
        backend_mode=backend_mode, remote=remote_config,
        _legacy_keys=legacy,
    )


def storage_overlay(
    resolved: ResolvedStorage,
    *,
    owner: StorageRole | QdrantStorageRole | str = StorageRole.CODE,
    graph_provider: str = "falkordb",
    code_collection: Optional[str] = None,
    doc_collection: Optional[str] = None,
    code_graph: Optional[str] = None,
    doc_graph: Optional[str] = None,
) -> dict[str, str]:
    selected = owner.value if isinstance(owner, Enum) else str(owner)
    selected = "doc" if selected == "document" else selected
    falkor_selected = resolved.falkordb_path_for_role(selected)
    overlay = {
        ENV_DATA_HOME: str(resolved.data_root), ENV_INSTANCE: resolved.instance_id,
        ENV_CODE_OWNER: resolved.code_owner_id, ENV_DOC_OWNER: resolved.doc_owner_id,
        ENV_QDRANT_BASE: str(resolved.qdrant_base),
        ENV_QDRANT_CODE: str(resolved.qdrant_code_path), ENV_QDRANT_DOC: str(resolved.qdrant_doc_path),
        ENV_FALKORDB_CODE: str(resolved.falkordb_code_path), ENV_FALKORDB_DOC: str(resolved.falkordb_doc_path),
        ENV_FALKORDB_PATH: str(falkor_selected), "CORTEX_STORAGE_OWNER": selected,
    }
    remote = resolved.remote
    force_local = environment_flag_enabled(os.getenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL"))
    if resolved.backend_mode == BackendMode.REMOTE and remote is not None and not force_local:
        if remote.qdrant_url:
            overlay["QDRANT_URL"] = remote.qdrant_url
            if remote.qdrant_api_key:
                overlay["QDRANT_API_KEY"] = remote.qdrant_api_key
            # Analyzer ``--qdrant-url`` defaults read from QDRANT_CODE_PATH;
            # override the local-path value with the remote URL so the
            # embedding pass targets the configured remote Qdrant instead of
            # silently falling back to the local filesystem store.
            overlay[ENV_QDRANT_CODE] = remote.qdrant_url
            overlay[ENV_QDRANT_DOC] = remote.qdrant_url
        if remote.falkordb_uri:
            # A remote FalkorDB server replaces the embedded store for this
            # project. Drop the local path keys so children cannot silently
            # fall back to FalkorDBLite (unavailable on win32) and pass the
            # connection details through instead. The FalkorDBDriver accepts
            # both ``scheme://host:port`` URIs and bare ``host:port`` values.
            overlay["FALKORDB_URI"] = remote.falkordb_uri
            for key in (ENV_FALKORDB_PATH, ENV_FALKORDB_CODE, ENV_FALKORDB_DOC):
                overlay.pop(key, None)
            if remote.falkordb_password:
                overlay["FALKORDB_PASSWORD"] = remote.falkordb_password
            if remote.falkordb_ssl:
                overlay["FALKORDB_SSL"] = "1"
    if code_collection or resolved.code_collection:
        overlay["QDRANT_COLLECTION"] = code_collection or str(resolved.code_collection)
        overlay["QDRANT_COLLECTION_CODE"] = overlay["QDRANT_COLLECTION"]
    if doc_collection or resolved.doc_collection:
        overlay["QDRANT_COLLECTION_DOC"] = doc_collection or str(resolved.doc_collection)
    if code_graph or resolved.code_graph:
        overlay["FALKORDB_GRAPH"] = code_graph or str(resolved.code_graph)
    if doc_graph or resolved.doc_graph:
        overlay["DOC_FALKORDB_GRAPH"] = doc_graph or str(resolved.doc_graph)
        if selected == "doc":
            overlay["FALKORDB_GRAPH"] = overlay["DOC_FALKORDB_GRAPH"]

    # Propagate the resolved, credential-free descriptors so journal setup
    # never needs to guess a remote target from a missing local path.  Keep the
    # legacy env reconstruction path for callers that have not yet adopted the
    # storage overlay.
    graph_name = overlay.get("FALKORDB_GRAPH")
    collection_name = (
        overlay.get("QDRANT_COLLECTION_DOC")
        if selected == StorageRole.DOCUMENT.value
        else overlay.get("QDRANT_COLLECTION")
    )
    if graph_name and collection_name and str(graph_provider).casefold() in {
        "falkor",
        "falkordb",
        "local",
        "embedded",
    }:
        from .factory import StorageFactory
        from .targets import (
            ENV_EFFECTIVE_GRAPH_FINGERPRINT,
            ENV_EFFECTIVE_GRAPH_TARGET,
            ENV_EFFECTIVE_TOPOLOGY,
            ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT,
            ENV_EFFECTIVE_VECTOR_FINGERPRINT,
            ENV_EFFECTIVE_VECTOR_TARGET,
        )

        factory = StorageFactory(
            backend_mode=resolved.backend_mode,
            resolved=resolved,
            remote=remote,
            code_graph=code_graph or resolved.code_graph,
            doc_graph=doc_graph or resolved.doc_graph,
            code_collection=code_collection or resolved.code_collection,
            doc_collection=doc_collection or resolved.doc_collection,
        )
        topology = factory.effective_topology(
            graph_name=graph_name,
            collection_name=collection_name,
            role=selected,
        )
        overlay[ENV_EFFECTIVE_GRAPH_TARGET] = topology.graph.canonical_json
        overlay[ENV_EFFECTIVE_GRAPH_FINGERPRINT] = topology.graph_fingerprint
        overlay[ENV_EFFECTIVE_VECTOR_TARGET] = topology.vector.canonical_json
        overlay[ENV_EFFECTIVE_VECTOR_FINGERPRINT] = topology.vector_fingerprint
        overlay[ENV_EFFECTIVE_TOPOLOGY] = topology.canonical_json
        overlay[ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT] = topology.fingerprint
    return overlay


def env_to_config(env: Iterable[tuple[str, object]]) -> dict[str, object]:
    return {str(key): value for key, value in env}
