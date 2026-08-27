"""Configuration and orchestration helpers for graph-write journaling."""

from __future__ import annotations

import hashlib
import json
import os
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, MutableMapping

from tools.common.sync_scope import scan_scope_id
from tools.graph.schema import CODE_GRAPH_SCHEMA

from .identity import canonical_json, run_id
from .models import (
    JournalError,
    JournalLimits,
    RunMetadata,
    RunStatus,
    TerminalErrorCode,
)
from .sqlite_store import SQLiteJournal


MODE_ENV = "CORTEX_GRAPH_JOURNAL_MODE"
PATH_ENV = "CORTEX_GRAPH_JOURNAL_PATH"
METADATA_ENV = "CORTEX_GRAPH_JOURNAL_METADATA"
GENERATION_ENV = "CORTEX_GRAPH_JOURNAL_GENERATION"
AUTO_RESUME_ENV = "CORTEX_GRAPH_JOURNAL_AUTO_RESUME"
LEASE_SECONDS_ENV = "CORTEX_GRAPH_JOURNAL_LEASE_SECONDS"
MAX_ATTEMPTS_ENV = "CORTEX_GRAPH_JOURNAL_MAX_ATTEMPTS"
RETRY_BASE_SECONDS_ENV = "CORTEX_GRAPH_JOURNAL_RETRY_BASE_SECONDS"
RETRY_MAX_SECONDS_ENV = "CORTEX_GRAPH_JOURNAL_RETRY_MAX_SECONDS"
_LIMIT_ENV = {
    "max_batches_per_run": "CORTEX_GRAPH_JOURNAL_MAX_BATCHES",
    "max_payload_bytes_per_run": "CORTEX_GRAPH_JOURNAL_MAX_PAYLOAD_BYTES",
    "max_artifact_bytes": "CORTEX_GRAPH_JOURNAL_MAX_ARTIFACT_BYTES",
    "max_journal_bytes": "CORTEX_GRAPH_JOURNAL_MAX_DATABASE_BYTES",
    "min_free_bytes": "CORTEX_GRAPH_JOURNAL_MIN_FREE_BYTES",
    "retention_seconds": "CORTEX_GRAPH_JOURNAL_RETENTION_SECONDS",
    "busy_timeout_ms": "CORTEX_GRAPH_JOURNAL_BUSY_TIMEOUT_MS",
    "wal_autocheckpoint_pages": "CORTEX_GRAPH_JOURNAL_WAL_CHECKPOINT_PAGES",
}
OFF_MODES = frozenset({"", "0", "false", "off", "disabled"})
SHADOW_MODES = frozenset({"shadow", "shared-shadow", "cplus-canary"})
REQUIRED_MODES = frozenset({"required", "shared-required"})


@dataclass(frozen=True)
class JournalConfig:
    mode: str
    path: Path
    metadata: RunMetadata
    limits: JournalLimits = JournalLimits()
    auto_resume: bool = True
    lease_seconds: int = 300
    max_attempts: int = 5
    retry_base_seconds: int = 1
    retry_max_seconds: int = 60

    @property
    def required(self) -> bool:
        return self.mode == "required"

    @property
    def shadow(self) -> bool:
        return self.mode == "shadow"


def _positive_int(
    source: Mapping[str, str], name: str, default: int
) -> int:
    raw = str(source.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            f"{name} must be a positive integer",
        ) from exc
    if value <= 0:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            f"{name} must be a positive integer",
        )
    return value


def _boolean(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(source.get(name) or "").strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise JournalError(
        TerminalErrorCode.INVALID_CONTRACT,
        f"{name} must be a boolean",
    )


def _normalize_mode(raw: str | None) -> str:
    value = (raw or "").strip().casefold()
    if value in OFF_MODES:
        return "off"
    if value in SHADOW_MODES:
        return "shadow"
    if value in REQUIRED_MODES:
        return "required"
    if value == "all-required":
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            "all-required rollout is not available until direct mutation inventory is complete",
        )
    raise JournalError(
        TerminalErrorCode.INVALID_CONTRACT,
        f"unsupported graph journal mode: {raw}",
    )


def _metadata_from_mapping(value: Mapping[str, object]) -> RunMetadata:
    return RunMetadata(
        project_id=str(value["project_id"]),
        scope_id=str(value["scope_id"]),
        source_revision=str(value["source_revision"]),
        source_snapshot=str(value["source_snapshot"]),
        physical_target=str(value["physical_target"]),
        generation=str(value["generation"]),
        parser=str(value["parser"]),
        parser_version=str(value["parser_version"]),
        schema_fingerprint=str(value["schema_fingerprint"]),
        query_shape_version=str(value["query_shape_version"]),
        operation_versions=dict(value.get("operation_versions") or {}),
        contract_version=int(value.get("contract_version") or 1),
    )


def journal_config_from_env(
    env: Mapping[str, str] | None = None,
) -> JournalConfig | None:
    source = os.environ if env is None else env
    mode = _normalize_mode(source.get(MODE_ENV))
    if mode == "off":
        return None
    path_text = str(source.get(PATH_ENV) or "").strip()
    metadata_text = str(source.get(METADATA_ENV) or "").strip()
    if not path_text or not metadata_text:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            "journal mode requires an absolute path and stable run metadata",
        )
    try:
        metadata_value = json.loads(metadata_text)
        if not isinstance(metadata_value, dict):
            raise TypeError("metadata must be an object")
        metadata = _metadata_from_mapping(metadata_value)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            f"invalid graph journal metadata: {exc}",
        ) from exc
    defaults = JournalLimits()
    limits = JournalLimits(
        **{
            field: _positive_int(source, env_name, getattr(defaults, field))
            for field, env_name in _LIMIT_ENV.items()
        }
    )
    lease_seconds = _positive_int(source, LEASE_SECONDS_ENV, 300)
    max_attempts = _positive_int(source, MAX_ATTEMPTS_ENV, 5)
    retry_base_seconds = _positive_int(source, RETRY_BASE_SECONDS_ENV, 1)
    retry_max_seconds = _positive_int(source, RETRY_MAX_SECONDS_ENV, 60)
    if retry_base_seconds > retry_max_seconds:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            f"{RETRY_BASE_SECONDS_ENV} must not exceed {RETRY_MAX_SECONDS_ENV}",
        )
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            "graph journal path must be absolute",
        )
    return JournalConfig(
        mode=mode,
        path=path.resolve(),
        metadata=metadata,
        limits=limits,
        auto_resume=_boolean(source, AUTO_RESUME_ENV, True),
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
        retry_base_seconds=retry_base_seconds,
        retry_max_seconds=retry_max_seconds,
    )


def attach_journal_config(driver: object, args: Namespace) -> JournalConfig | None:
    """Attach validated process configuration at the driver creation boundary."""

    config = journal_config_from_env()
    if config is not None:
        setattr(driver, "journal_config", config)
        if config.required:
            from .guard import install_required_write_guard

            install_required_write_guard(driver)
    return config


def configure_journal_env(
    env: MutableMapping[str, str],
    *,
    root: str | Path,
    project_id: str,
    parser: str,
    source_revision: str,
    source_snapshot: str,
    physical_target: str,
    cache_dir: str | Path | None = None,
    mode: str = "shared-required",
    generation: str | None = None,
) -> JournalConfig:
    """Populate stable child-process metadata shared across outer retries."""

    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "off":
        raise ValueError("configure_journal_env requires an enabled mode")
    canonical_root = Path(root).expanduser().resolve()
    scope_id = scan_scope_id(project_id, str(canonical_root))
    journal_root = (
        Path(cache_dir).expanduser().resolve()
        if cache_dir
        else canonical_root / ".cache"
    ) / "graph-write-journal" / scope_id
    safe_parser = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in parser
    )
    path = journal_root / f"{safe_parser}.sqlite3"
    run_generation = generation or env.get(GENERATION_ENV) or source_snapshot
    metadata = RunMetadata(
        project_id=project_id,
        scope_id=scope_id,
        source_revision=source_revision or source_snapshot,
        source_snapshot=source_snapshot,
        physical_target=physical_target,
        generation=run_generation,
        parser=parser,
        parser_version="1",
        schema_fingerprint=CODE_GRAPH_SCHEMA.fingerprint,
        query_shape_version="language-writer-v1",
        operation_versions={"graph-write": 1},
    )
    env[MODE_ENV] = normalized_mode
    env[PATH_ENV] = str(path)
    env[GENERATION_ENV] = run_generation
    env[METADATA_ENV] = canonical_json(metadata.to_dict()).decode("utf-8")
    config = journal_config_from_env(env)
    assert config is not None
    if normalized_mode == "required" and path.is_file():
        with SQLiteJournal(path, limits=config.limits) as journal:
            journal.quarantine_legacy_targets(
                metadata,
                (legacy_physical_target_from_env(env),),
            )
            resumable = journal.find_resumable_run(metadata)
        if resumable is not None:
            if not config.auto_resume:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "a compatible incomplete journal exists but automatic resume is disabled",
                )
            metadata = resumable.metadata
            run_generation = metadata.generation
            env[GENERATION_ENV] = run_generation
            env[METADATA_ENV] = canonical_json(metadata.to_dict()).decode("utf-8")
            config = journal_config_from_env(env)
            assert config is not None
    return config


def physical_target_from_env(env: Mapping[str, str]) -> str:
    """Return the effective graph-target fingerprint for journal identity.

    Prefer the descriptor produced by :func:`storage_overlay`; reconstruct a
    canonical descriptor for older callers.  Reconstruction is URI-aware, so
    two remote servers can never collapse onto the old ``embedded`` token.
    """

    from cortex_harness.storage.targets import (
        ENV_EFFECTIVE_GRAPH_FINGERPRINT,
        effective_graph_target_from_env,
    )

    try:
        target = effective_graph_target_from_env(env)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            f"invalid effective graph target: {exc}",
        ) from exc
    supplied = str(env.get(ENV_EFFECTIVE_GRAPH_FINGERPRINT) or "").strip()
    if supplied and supplied != target.fingerprint:
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            "effective graph target descriptor does not match its fingerprint",
        )
    return target.fingerprint


def legacy_physical_target_from_env(env: Mapping[str, str]) -> str:
    """Reconstruct the pre-v1 identity only for explicit quarantine."""

    provider = (
        env.get("CODE_GRAPH_PROVIDER")
        or env.get("GRAPH_PROVIDER")
        or "falkordb"
    ).casefold()
    if provider in {"neo4j", "neo"}:
        return f"neo4j:{env.get('NEO4J_URI', '')}:{env.get('NEO4J_DB', 'neo4j')}"
    path = str(env.get("FALKORDB_PATH") or "embedded")
    graph = str(
        env.get("FALKORDB_GRAPH")
        or env.get("FALKORDB_DATABASE")
        or "hyper_graph"
    )
    return f"falkordb:{path}:{graph}"


def snapshot_for_paths(
    *, source_revision: str, changed: list[str], deleted: list[str]
) -> str:
    payload = {
        "source_revision": source_revision,
        "changed": sorted(changed),
        "deleted": sorted(deleted),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def finalize_journal_from_env(env: Mapping[str, str]) -> RunStatus | None:
    """Close production after a successful child and return its durable status."""

    config = journal_config_from_env(env)
    if config is None or config.shadow:
        return None
    if not config.path.is_file():
        raise JournalError(
            TerminalErrorCode.INVALID_TRANSITION,
            "required graph journal was not created by the analyzer",
        )
    with SQLiteJournal(config.path, limits=config.limits) as journal:
        run_id_value = run_id(config.metadata)
        if journal.get_run(run_id_value) is None:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "required graph journal run was not opened by the analyzer",
            )
        return journal.close_run_production(run_id_value).status


def journal_status_from_env(env: Mapping[str, str]) -> dict[str, object] | None:
    """Read one expected run's payload-free status without creating it."""

    config = journal_config_from_env(env)
    if config is None or config.shadow or not config.path.is_file():
        return None
    with SQLiteJournal(config.path, limits=config.limits) as journal:
        run_id_value = run_id(config.metadata)
        if journal.get_run(run_id_value) is None:
            return None
        return journal.status_summary(run_id_value)
