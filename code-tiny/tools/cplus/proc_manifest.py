"""Redacted Pro*C precompiler artifact/context manifests and exact
invalidation for the complete original -> generated replacement set.

The manifest proves how one original ``.pc``/``.pcc`` source became a
generated C/C++ translation unit: original/generated/map hashes, language
mode, redacted precompiler identity, normalized approved option fingerprint,
include/macro context, and eligibility. No credentials or raw commands are
ever persisted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Set

from tools.common.parse_quality import atomic_write_json
from tools.cplus.proc_analyzer import prepare_proc_path
from tools.cplus.proc_source_map import (
    MASK_POLICY_VERSION,
    PROC_SOURCE_MAP_VERSION as _SOURCE_MAP_CONTRACT_VERSION,
)
from tools.cplus.semantic_context import (
    PINNED_LIBCLANG_VERSION,
    SEMANTIC_REQUEST_SCHEMA,
    SEMANTIC_WORKER_PROTOCOL_VERSION,
    RejectionReason,
    ReverseInvalidationIndex,
    _digest,
    file_sha256,
)

PROC_MANIFEST_VERSION = "1"
PROC_SOURCE_MAP_VERSION = _SOURCE_MAP_CONTRACT_VERSION
PROC_MAPPING_POLICY_VERSION = MASK_POLICY_VERSION

MAX_PROC_DEPENDENCIES = 20_000
MAX_EXEC_SQL_INCLUDES = 256

# Option shapes that carry credentials and must only ever appear as a
# redacted fingerprint, never verbatim. Exact names only: semantic options
# like SQLCHECK must keep their identity because they change generated code.
_REDACTED_OPTION_NAMES = frozenset({"userid", "user", "password", "pwd"})


def redact_proc_option(token: str) -> str:
    """Map one precompiler option to a redacted, order-preserving identity."""

    name = token.split("=", 1)[0].lstrip("/-").lower()
    if name in _REDACTED_OPTION_NAMES:
        return f"{name}=<redacted>"
    return token


def normalized_option_fingerprint(options: Iterable[str]) -> str:
    """Fingerprint of the sorted, redacted, approved option set."""

    redacted = sorted(redact_proc_option(token) for token in options)
    return _digest(("proc-options", PROC_MANIFEST_VERSION, *redacted))


def extract_exec_sql_includes(original_path: str) -> List[str]:
    """Resolve ``EXEC SQL INCLUDE name`` targets from the prepared source."""

    if not original_path or not os.path.isfile(original_path):
        return []
    prepared = prepare_proc_path(original_path)
    targets: List[str] = []
    seen: Set[str] = set()
    for region in prepared.regions:
        if region.operation_upper != "INCLUDE":
            continue
        text = (region.raw_text or "").strip()
        parts = text.split()
        try:
            include_at = next(
                index
                for index, token in enumerate(parts)
                if token.upper() == "INCLUDE"
            )
            target = parts[include_at + 1]
        except (StopIteration, IndexError):
            continue
        target = target.strip('"\'').rstrip(";")
        if not target:
            continue
        if target.lower() in {"sqlca", "sqlda", "oraca"}:
            # Oracle-shipped declare headers are not project dependencies.
            continue
        if target in seen:
            continue
        seen.add(target)
        targets.append(target)
        if len(targets) >= MAX_EXEC_SQL_INCLUDES:
            break
    return targets


@dataclass(frozen=True)
class ProcArtifactManifest:
    """Redacted proof of one original -> generated Pro*C replacement."""

    original_rel_path: str
    original_sha256: str
    generated_rel_path: str
    generated_sha256: str
    source_map_id: str
    source_map_rel_path: str
    source_map_sha256: str
    language_mode: str
    precompiler_fingerprint: str
    option_fingerprint: str
    mapping_policy: str
    include_names: Tuple[str, ...]
    eligibility: RejectionReason

    @property
    def eligible(self) -> bool:
        return self.eligibility is RejectionReason.NOT_REJECTED

    def fingerprint(self) -> str:
        return _digest(
            (
                PROC_MANIFEST_VERSION,
                PROC_SOURCE_MAP_VERSION,
                self.original_rel_path,
                self.original_sha256,
                self.generated_rel_path,
                self.generated_sha256,
                self.source_map_id,
                self.source_map_rel_path,
                self.source_map_sha256,
                self.language_mode,
                self.precompiler_fingerprint,
                self.option_fingerprint,
                self.mapping_policy,
                *self.include_names,
            )
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": PROC_MANIFEST_VERSION,
            "original_rel_path": self.original_rel_path,
            "original_sha256": self.original_sha256,
            "generated_rel_path": self.generated_rel_path,
            "generated_sha256": self.generated_sha256,
            "source_map_id": self.source_map_id,
            "source_map_rel_path": self.source_map_rel_path,
            "source_map_sha256": self.source_map_sha256,
            "language_mode": self.language_mode,
            "precompiler_fingerprint": self.precompiler_fingerprint,
            "option_fingerprint": self.option_fingerprint,
            "mapping_policy": self.mapping_policy,
            "include_names": list(self.include_names),
            "eligibility": self.eligibility.value,
            "fingerprint": self.fingerprint(),
        }


def build_proc_manifest(
    *,
    root: str,
    original_path: str,
    generated_path: str,
    source_map_id: str,
    source_map_path: str,
    language_mode: str,
    precompiler_identity: str,
    options: Sequence[str],
) -> ProcArtifactManifest:
    """Build the redacted manifest; fails closed when a layer is missing."""

    root_real = os.path.realpath(os.path.abspath(root))

    def rel(path: str) -> str:
        return os.path.relpath(os.path.realpath(os.path.abspath(path)), root_real).replace("\\", "/")

    eligibility = RejectionReason.NOT_REJECTED
    original_sha = ""
    generated_sha = ""
    map_sha = ""
    if os.path.isfile(original_path):
        original_sha = file_sha256(original_path)
    else:
        eligibility = RejectionReason.STALE_PROC_LAYER
    if os.path.isfile(generated_path):
        generated_sha = file_sha256(generated_path)
    else:
        eligibility = RejectionReason.STALE_PROC_LAYER
    map_rel = ""
    if source_map_path and os.path.isfile(source_map_path):
        map_sha = file_sha256(source_map_path)
        map_rel = rel(source_map_path)
    elif source_map_path:
        eligibility = RejectionReason.STALE_PROC_LAYER

    return ProcArtifactManifest(
        original_rel_path=rel(original_path),
        original_sha256=original_sha,
        generated_rel_path=rel(generated_path),
        generated_sha256=generated_sha,
        source_map_id=source_map_id,
        source_map_rel_path=map_rel,
        source_map_sha256=map_sha,
        language_mode=language_mode,
        precompiler_fingerprint=_digest(("proc-precompiler", precompiler_identity)),
        option_fingerprint=normalized_option_fingerprint(options),
        mapping_policy=PROC_MAPPING_POLICY_VERSION,
        include_names=tuple(extract_exec_sql_includes(original_path)),
        eligibility=eligibility,
    )


class ProcDependencyIndex:
    """Reverse invalidation over the full Pro*C replacement set."""

    def __init__(self) -> None:
        self._index = ReverseInvalidationIndex()
        self._manifests: Dict[str, ProcArtifactManifest] = {}

    def add_manifest(
        self,
        manifest: ProcArtifactManifest,
        *,
        generated_dependencies: Iterable[str] = (),
    ) -> None:
        """Register one bundle; edges cover original, generated, map, and
        C/C++ includes of the generated unit, resolved EXEC SQL INCLUDEs."""

        self._manifests[manifest.original_rel_path] = manifest
        key = manifest.original_rel_path
        deps: Set[str] = {
            manifest.original_rel_path,
            manifest.generated_rel_path,
        }
        if manifest.source_map_rel_path:
            deps.add(manifest.source_map_rel_path)
        elif manifest.source_map_id:
            deps.add(f"map:{manifest.source_map_id}")
        for dep in generated_dependencies:
            deps.add(str(dep))
            if len(deps) >= MAX_PROC_DEPENDENCIES:
                raise ValueError("proc dependency set exceeds cap")
        for include in manifest.include_names:
            if include:
                deps.add(f"proc-include:{include}")
        self._index.add(key, sorted(deps))

    def impacted_originals(self, changed_files: Iterable[str]) -> Set[str]:
        """Original ``.pc``/``.pcc`` paths whose whole replacement set is
        invalidated by the given changed files.

        Real watcher/git paths are expanded so that a changed header such as
        ``inc/myapp_common.h`` also matches the ``EXEC SQL INCLUDE
        myapp_common`` edge of every bundle.
        """

        changed = {str(path) for path in changed_files}
        for path in list(changed):
            stem = os.path.splitext(os.path.basename(path))[0]
            if stem:
                changed.add(f"proc-include:{stem}")
        return self._index.impacted(changed)

    def manifests(self) -> List[ProcArtifactManifest]:
        return sorted(self._manifests.values(), key=lambda m: m.original_rel_path)

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": PROC_MANIFEST_VERSION,
            "manifests": [m.to_json() for m in self.manifests()],
            "reverse": self._index.to_json(),
        }

    def save(self, path: str) -> None:
        atomic_write_json(path, self.to_json())


def proc_cache_fingerprint(manifest: ProcArtifactManifest, config_fingerprint: str) -> str:
    """Cache identity of one Pro*C semantic bundle: original, masked,
    generated, map, compiler context, precompiler, and policy inputs."""

    return _digest(
        (
            "proc-semantic-cache",
            manifest.fingerprint(),
            config_fingerprint,
            manifest.mapping_policy,
            SEMANTIC_WORKER_PROTOCOL_VERSION,
            SEMANTIC_REQUEST_SCHEMA,
            PINNED_LIBCLANG_VERSION,
        )
    )


def classify_proc_downgrade(
    manifest: ProcArtifactManifest,
    *,
    changed_files: Iterable[str],
) -> str:
    """Decide replacement handling for a changed original.

    Returns one of ``semantic_complete``, ``sql_only``, or ``unchanged``:

    - An eligible bundle whose original changed keeps the strong lane alive
      (semantic_complete) once the precompiler layer re-runs; SQL and masked
      tree-sitter coverage always stay intact.
    - A missing/stale generated or map layer, or an ineligible manifest,
      downgrades publication to sql_only.
    - Unaffected bundles are unchanged.
    """

    if not manifest.eligible:
        return "sql_only"
    changed = {str(path) for path in changed_files}
    bundle_paths = {
        manifest.original_rel_path,
        manifest.generated_rel_path,
        manifest.source_map_rel_path,
    }
    if not changed & bundle_paths:
        return "unchanged"
    return "semantic_complete"
