"""Compile-context registry, dependency invalidation, semantic cache, and
bounded semantic scheduling.

Compile databases are validated data only; nothing in this module executes a
compile command, response file, plugin, or build hook. Semantic cache identity
covers every input that can change Clang semantics so unchanged analysis hits
cache and every changed input causes exact affected invalidation.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from tools.common.parse_quality import atomic_write_json
from tools.cplus.parse_recovery import CompileContext
from tools.cplus.semantic_worker import (
    PINNED_LIBCLANG_VERSION,
    SEMANTIC_BACKEND_ID,
    SEMANTIC_REQUEST_SCHEMA,
    SEMANTIC_WORKER_PROTOCOL_VERSION,
)

CONTEXT_REGISTRY_VERSION = "1"
DEP_MANIFEST_VERSION = "1"
SEMANTIC_CACHE_VERSION = "1"
SEMANTIC_POLICY_VERSION = "1"
BASELINE_REPORT_VERSION = "1"

# Bounded registry/manifest budgets.
MAX_REGISTRY_CONTEXTS = 200_000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_TARGETS = 100_000
MAX_DEP_ENTRIES_PER_TU = 20_000
MAX_HEADER_VARIANTS = 4
DEFAULT_CONFIG_VARIANT_CAP = 3


class CoverageState(str, Enum):
    """How faithfully a context reproduces the real build configuration."""

    FAITHFUL = "faithful"  # declared by a validated compile database entry
    SYNTHETIC = "synthetic"  # synthesized flags; may resolve wrong branch
    INHERITED = "inherited"  # borrowed from a sibling TU of the same project

    @property
    def eligible_for_publication(self) -> bool:
        return self is CoverageState.FAITHFUL


class RejectionReason(str, Enum):
    NOT_REJECTED = "not_rejected"
    UNSAFE_ARGUMENTS = "unsafe_arguments"
    PATH_ESCAPES_ROOT = "path_escapes_root"
    DATABASE_OVERSIZED = "database_oversized"
    TOKEN_CAP_EXCEEDED = "token_cap_exceeded"
    MISSING_CONTEXT = "missing_context"
    VARIANT_CAP_EXCEEDED = "variant_cap_exceeded"
    SYNTHETIC_CONTEXT = "synthetic_context"
    STALE_PROC_LAYER = "stale_proc_layer"


def _digest(parts: Iterable[str]) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def file_sha256(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


@dataclass(frozen=True)
class RegisteredContext:
    """One normalized compile context for one translation unit variant."""

    project: str
    rel_path: str
    config_fingerprint: str
    arguments_digest: str
    working_dir_rel: str
    target_identity: str
    sysroot_identity: str
    resource_dir_identity: str
    toolchain_version: str
    coverage: CoverageState
    rejection_reason: RejectionReason
    source_fingerprint: str = ""

    def __post_init__(self) -> None:
        # Enum fields may arrive as raw JSON strings; normalize so identity
        # comparisons stay exact.
        if not isinstance(self.coverage, CoverageState):
            object.__setattr__(self, "coverage", CoverageState(str(self.coverage)))
        if not isinstance(self.rejection_reason, RejectionReason):
            object.__setattr__(
                self, "rejection_reason", RejectionReason(str(self.rejection_reason))
            )

    @property
    def eligible(self) -> bool:
        return (
            self.rejection_reason is RejectionReason.NOT_REJECTED
            and self.coverage.eligible_for_publication
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            "project": self.project,
            "rel_path": self.rel_path,
            "config_fingerprint": self.config_fingerprint,
            "arguments_digest": self.arguments_digest,
            "working_dir_rel": self.working_dir_rel,
            "target_identity": self.target_identity,
            "sysroot_identity": self.sysroot_identity,
            "resource_dir_identity": self.resource_dir_identity,
            "toolchain_version": self.toolchain_version,
            "coverage": self.coverage.value,
            "rejection_reason": self.rejection_reason.value,
            "source_fingerprint": self.source_fingerprint,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "RegisteredContext":
        return cls(
            project=str(data["project"]),
            rel_path=str(data["rel_path"]),
            config_fingerprint=str(data["config_fingerprint"]),
            arguments_digest=str(data.get("arguments_digest", "")),
            working_dir_rel=str(data.get("working_dir_rel", "")),
            target_identity=str(data.get("target_identity", "")),
            sysroot_identity=str(data.get("sysroot_identity", "")),
            resource_dir_identity=str(data.get("resource_dir_identity", "")),
            toolchain_version=str(data.get("toolchain_version", "")),
            coverage=CoverageState(str(data.get("coverage", "faithful"))),
            rejection_reason=RejectionReason(str(data.get("rejection_reason", "not_rejected"))),
            source_fingerprint=str(data.get("source_fingerprint", "")),
        )


def _extract_identity(args: Sequence[str], flag: str) -> str:
    """Return a redacted identity for a path-like flag value, '' when absent."""

    prefix = f"{flag}="
    for index, token in enumerate(args):
        value = ""
        if token == flag and index + 1 < len(args):
            value = args[index + 1]
        elif token.startswith(prefix):
            value = token[len(prefix) :]
        if value:
            return _digest((flag, os.path.normpath(value)))
    return ""


def _toolchain_identity() -> str:
    return _digest((SEMANTIC_BACKEND_ID, PINNED_LIBCLANG_VERSION))


def build_registered_context(
    compile_context: CompileContext,
    *,
    project: str,
    root: str,
    working_dir: str,
    coverage: CoverageState,
    rejection_reason: RejectionReason = RejectionReason.NOT_REJECTED,
    source_fingerprint: str = "",
) -> RegisteredContext:
    """Normalize a sanitized CompileContext into a registry entry."""

    root_real = os.path.realpath(os.path.abspath(root))
    try:
        working_dir_rel = os.path.relpath(
            os.path.realpath(os.path.abspath(working_dir)), root_real
        ).replace("\\", "/")
    except ValueError:
        working_dir_rel = ""
    return RegisteredContext(
        project=project,
        rel_path=compile_context.file_path,
        config_fingerprint=compile_context.fingerprint,
        arguments_digest=_digest(compile_context.arguments),
        working_dir_rel=working_dir_rel,
        target_identity=_extract_identity(compile_context.arguments, "--target"),
        sysroot_identity=_extract_identity(compile_context.arguments, "--sysroot"),
        resource_dir_identity=_extract_identity(compile_context.arguments, "-resource-dir"),
        toolchain_version=_toolchain_identity(),
        coverage=coverage,
        rejection_reason=rejection_reason,
        source_fingerprint=source_fingerprint,
    )


class ContextRegistry:
    """Normalized compile-context registry keyed by (project, TU, config).

    Multiple legitimate configurations for the same TU are preserved as
    distinct variants; no implicit winner is chosen at registration time.
    """

    def __init__(self) -> None:
        self._contexts: Dict[Tuple[str, str, str], RegisteredContext] = {}
        self._lock = threading.Lock()

    def register(self, context: RegisteredContext) -> RegisteredContext:
        if context.rejection_reason is RejectionReason.NOT_REJECTED and not context.coverage.eligible_for_publication:
            # Synthetic/inherited contexts may resolve the wrong conditional
            # branch; they can never certify semantic publication.
            context = RegisteredContext(
                project=context.project,
                rel_path=context.rel_path,
                config_fingerprint=context.config_fingerprint,
                arguments_digest=context.arguments_digest,
                working_dir_rel=context.working_dir_rel,
                target_identity=context.target_identity,
                sysroot_identity=context.sysroot_identity,
                resource_dir_identity=context.resource_dir_identity,
                toolchain_version=context.toolchain_version,
                coverage=context.coverage,
                rejection_reason=RejectionReason.SYNTHETIC_CONTEXT,
                source_fingerprint=context.source_fingerprint,
            )
        key = (context.project, context.rel_path, context.config_fingerprint)
        with self._lock:
            if len(self._contexts) >= MAX_REGISTRY_CONTEXTS and key not in self._contexts:
                raise ValueError("compile-context registry exceeds cap")
            self._contexts[key] = context
        return context

    def variants(self, project: str, rel_path: str) -> List[RegisteredContext]:
        with self._lock:
            return sorted(
                (
                    context
                    for (proj, path, _fingerprint), context in self._contexts.items()
                    if proj == project and path == rel_path
                ),
                key=lambda context: context.config_fingerprint,
            )

    def select(
        self,
        project: str,
        rel_path: str,
        *,
        variant_cap: int = DEFAULT_CONFIG_VARIANT_CAP,
        explicit_profiles: Optional[Sequence[str]] = None,
    ) -> List[RegisteredContext]:
        """Bounded configuration-selection policy.

        Explicit profiles (config fingerprints) win when provided; otherwise
        all declared variants are returned up to the cap, faithful first.
        """

        variants = self.variants(project, rel_path)
        if explicit_profiles is not None:
            wanted = set(explicit_profiles)
            return [context for context in variants if context.config_fingerprint in wanted]
        faithful = [context for context in variants if context.coverage is CoverageState.FAITHFUL]
        other = [context for context in variants if context.coverage is not CoverageState.FAITHFUL]
        return (faithful + other)[: max(variant_cap, 0)]

    def rejection_summary(self) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        with self._lock:
            for context in self._contexts.values():
                reason = context.rejection_reason.value
                summary[reason] = summary.get(reason, 0) + 1
        return summary

    def to_json(self) -> Dict[str, Any]:
        with self._lock:
            entries = [context.to_json() for context in sorted(self._contexts.values(), key=sorted_context_key)]
        return {"version": CONTEXT_REGISTRY_VERSION, "contexts": entries}

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ContextRegistry":
        if data.get("version") != CONTEXT_REGISTRY_VERSION:
            raise ValueError("compile-context registry version mismatch")
        registry = cls()
        for entry in data.get("contexts", []):
            context = RegisteredContext.from_json(entry)
            registry._contexts[(context.project, context.rel_path, context.config_fingerprint)] = context
        return registry

    def save(self, path: str) -> None:
        atomic_write_json(path, self.to_json())

    @classmethod
    def load(cls, path: str) -> "ContextRegistry":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_json(json.load(handle))


def sorted_context_key(context: RegisteredContext) -> Tuple[str, str, str]:
    return (context.project, context.rel_path, context.config_fingerprint)


# ---------------------------------------------------------------------------
# Clang dependency manifests and reverse invalidation
# ---------------------------------------------------------------------------


def parse_dependency_manifest(
    path: str,
    *,
    max_bytes: int = MAX_MANIFEST_BYTES,
    max_entries: int = MAX_DEP_ENTRIES_PER_TU,
) -> List[str]:
    """Parse one Clang makefile-style ``.d`` manifest into bounded target list.

    Only dependency data is read; the file is never executed or fed to make.
    """

    if not path or not os.path.isfile(path):
        return []
    if os.path.getsize(path) > max_bytes:
        raise ValueError("dependency manifest exceeds size cap")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read(max_bytes + 1)
    if len(text) > max_bytes:
        raise ValueError("dependency manifest exceeds size cap")

    targets: List[str] = []
    seen: Set[str] = set()
    body = text.replace("\\\n", " ")
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        for token in tokens:
            token = token.rstrip(":")
            if not token or token in seen:
                continue
            if token.endswith(".o") or token.endswith(".d"):
                continue  # output artifacts are not semantic inputs
            seen.add(token)
            targets.append(token)
            if len(targets) >= max_entries:
                return targets
    return targets


class ReverseInvalidationIndex:
    """Maps each dependency file to the semantic identities that consume it."""

    def __init__(self) -> None:
        self._consumers: Dict[str, Set[str]] = {}

    def add(self, semantic_key: str, dependencies: Iterable[str]) -> None:
        consumers = self._consumers
        for dep in dependencies:
            consumers.setdefault(dep, set()).add(semantic_key)
            if len(consumers[dep]) > MAX_MANIFEST_TARGETS:
                raise ValueError("reverse invalidation index exceeds target cap")

    def impacted(self, changed_files: Iterable[str]) -> Set[str]:
        impacted: Set[str] = set()
        for changed in changed_files:
            impacted.update(self._consumers.get(changed, ()))
        return impacted

    def fanout(self, dependency: str) -> int:
        return len(self._consumers.get(dependency, ()))

    def dependency_count(self) -> int:
        return len(self._consumers)

    def fanout_items(self) -> List[Tuple[str, int]]:
        return sorted(
            ((dep, len(keys)) for dep, keys in self._consumers.items()),
            key=lambda item: item[1],
            reverse=True,
        )

    def to_json(self) -> Dict[str, List[str]]:
        return {
            dep: sorted(keys)
            for dep, keys in sorted(self._consumers.items())
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "ReverseInvalidationIndex":
        index = cls()
        for dep, keys in data.items():
            index._consumers[str(dep)] = {str(key) for key in keys}
        return index


def lexical_include_closure(source_path: str, *, root: str, max_depth: int = 8) -> List[str]:
    """Conservative fallback closure of ``#include`` targets."""

    root_real = os.path.realpath(os.path.abspath(root))
    closure: Set[str] = set()
    frontier = [source_path]
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: List[str] = []
        for current in frontier:
            try:
                with open(current, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read(1 << 20)
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not (line.startswith("#") and "include" in line):
                    continue
                target = line.split('"')[1] if '"' in line else ""
                if not target:
                    continue
                candidate = target if os.path.isabs(target) else os.path.join(
                    os.path.dirname(current), target
                )
                try:
                    resolved = os.path.realpath(os.path.abspath(candidate))
                    os.path.commonpath((root_real, resolved))
                except (OSError, ValueError):
                    continue
                if resolved in closure:
                    continue
                closure.add(resolved)
                next_frontier.append(resolved)
        frontier = next_frontier
        depth += 1
    return sorted(closure)


# ---------------------------------------------------------------------------
# Semantic cache identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticCacheIdentity:
    """Complete identity of one semantic observation."""

    source_rel_path: str
    source_fingerprint: str
    dependency_fingerprints: Tuple[str, ...]
    config_fingerprint: str
    coverage: CoverageState
    proc_source_map_version: str = ""
    working_dir_rel: str = ""
    target_identity: str = ""
    sysroot_identity: str = ""
    resource_dir_identity: str = ""
    toolchain_version: str = ""

    def fingerprint(self) -> str:
        return _digest(
            (
                SEMANTIC_CACHE_VERSION,
                SEMANTIC_POLICY_VERSION,
                SEMANTIC_WORKER_PROTOCOL_VERSION,
                SEMANTIC_REQUEST_SCHEMA,
                SEMANTIC_BACKEND_ID,
                PINNED_LIBCLANG_VERSION,
                self.source_rel_path,
                self.source_fingerprint,
                *self.dependency_fingerprints,
                self.config_fingerprint,
                self.coverage.value,
                self.proc_source_map_version,
                self.working_dir_rel,
                self.target_identity,
                self.sysroot_identity,
                self.resource_dir_identity,
                self.toolchain_version,
            )
        )


def build_cache_identity(
    context: RegisteredContext,
    *,
    dependency_fingerprints: Sequence[str],
    proc_source_map_version: str = "",
) -> SemanticCacheIdentity:
    return SemanticCacheIdentity(
        source_rel_path=context.rel_path,
        source_fingerprint=context.source_fingerprint,
        dependency_fingerprints=tuple(sorted(dependency_fingerprints)),
        config_fingerprint=context.config_fingerprint,
        coverage=context.coverage,
        proc_source_map_version=proc_source_map_version,
        working_dir_rel=context.working_dir_rel,
        target_identity=context.target_identity,
        sysroot_identity=context.sysroot_identity,
        resource_dir_identity=context.resource_dir_identity,
        toolchain_version=context.toolchain_version,
    )


class SemanticCache:
    """Per-identity JSON cache with version/policy-driven invalidation."""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, identity: SemanticCacheIdentity) -> str:
        digest = identity.fingerprint()
        return os.path.join(self.root, f"{digest[:32]}.json")

    def load(self, identity: SemanticCacheIdentity) -> Optional[Dict[str, Any]]:
        path = self._path(identity)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("version") != SEMANTIC_CACHE_VERSION:
            return None
        if data.get("identity_fingerprint") != identity.fingerprint():
            return None
        return data.get("payload")

    def store(self, identity: SemanticCacheIdentity, payload: Mapping[str, Any]) -> None:
        path = self._path(identity)
        temp = f"{path}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": SEMANTIC_CACHE_VERSION,
                    "identity_fingerprint": identity.fingerprint(),
                    "payload": payload,
                },
                handle,
            )
        os.replace(temp, path)


# ---------------------------------------------------------------------------
# Bounded semantic scheduler
# ---------------------------------------------------------------------------


class OverloadedError(RuntimeError):
    """Retryable admission rejection: queue item or byte budget exceeded."""


class CircuitOpenError(RuntimeError):
    """Non-yield circuit breaker tripped; lane paused."""


@dataclass
class SemanticLaneLimits:
    concurrency: int = 1
    max_queue_items: int = 500
    max_queue_bytes: int = 32 * 1024 * 1024
    per_tu_timeout_seconds: int = 30
    max_memory_mb: int = 1024
    # Capacity deliberately reserved for MCP/control work outside this lane.
    reserved_cpu_share: float = 0.25


@dataclass
class SemanticTask:
    identity: str
    payload_bytes: int
    context: RegisteredContext


@dataclass
class SchedulerMetrics:
    admitted: int = 0
    rejected_overloaded: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    non_yield_streak: int = 0
    circuit_open: bool = False
    queued_items: int = 0
    queued_bytes: int = 0
    latencies: List[float] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        ordered = sorted(self.latencies)
        p50 = ordered[len(ordered) // 2] if ordered else 0.0
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0.0
        return {
            "admitted": self.admitted,
            "rejected_overloaded": self.rejected_overloaded,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "non_yield_streak": self.non_yield_streak,
            "circuit_open": self.circuit_open,
            "queued_items": self.queued_items,
            "queued_bytes": self.queued_bytes,
            "latency_p50_seconds": round(p50, 6),
            "latency_p95_seconds": round(p95, 6),
        }


class BoundedSemanticScheduler:
    """Single-owner admission lane for eligible semantic work.

    Uses the gateway-style item/byte admission contract; capacity is shared
    with control work via ``reserved_cpu_share`` bounding concurrency.
    """

    MAX_NON_YIELD_STREAK = 20

    def __init__(self, limits: Optional[SemanticLaneLimits] = None):
        self.limits = limits or SemanticLaneLimits()
        if self.limits.concurrency < 1:
            raise ValueError("semantic lane concurrency must be positive")
        # Leave CPU capacity for MCP/control work outside this lane.
        share = min(max(self.limits.reserved_cpu_share, 0.0), 0.9)
        self.concurrency = max(1, int(self.limits.concurrency * (1.0 - share)))
        self._queue: List[SemanticTask] = []
        self._queue_bytes = 0
        self._cancelled: Set[str] = set()
        self._metrics = SchedulerMetrics()
        self._lock = threading.Lock()

    def submit(self, task: SemanticTask) -> None:
        with self._lock:
            if self._metrics.circuit_open:
                raise CircuitOpenError("semantic lane circuit breaker open")
            if (
                len(self._queue) >= self.limits.max_queue_items
                or self._queue_bytes + task.payload_bytes > self.limits.max_queue_bytes
            ):
                self._metrics.rejected_overloaded += 1
                raise OverloadedError("semantic lane queue budget exceeded")
            self._queue.append(task)
            self._queue_bytes += task.payload_bytes
            self._metrics.admitted += 1
            self._metrics.queued_items = len(self._queue)
            self._metrics.queued_bytes = self._queue_bytes

    def cancel(self, identity: str) -> None:
        with self._lock:
            self._cancelled.add(identity)
            remaining = [task for task in self._queue if task.identity != identity]
            removed = [task for task in self._queue if task.identity == identity]
            for task in removed:
                self._queue_bytes -= task.payload_bytes
                self._metrics.cancelled += 1
            self._queue[:] = remaining
            self._metrics.queued_items = len(self._queue)
            self._metrics.queued_bytes = self._queue_bytes

    def cancellation_checkpoint(self, identity: str) -> bool:
        """Cooperative checkpoint; True when the task should stop work."""

        with self._lock:
            return identity in self._cancelled

    def drain(self, handler) -> SchedulerMetrics:
        """Pop queued tasks one at a time; handler(task) -> bool yielded.

        ``yielded`` means the task produced new semantic evidence. A rolling
        non-yield streak trips the circuit breaker and fails closed.
        """

        while True:
            with self._lock:
                if not self._queue:
                    break
                task = self._queue.pop(0)
                self._queue_bytes -= task.payload_bytes
                self._metrics.queued_items = len(self._queue)
                self._metrics.queued_bytes = self._queue_bytes
                cancelled = task.identity in self._cancelled
            started = time.monotonic()
            if cancelled:
                with self._lock:
                    self._metrics.cancelled += 1
                continue
            try:
                yielded = bool(handler(task))
            except CircuitOpenError:
                raise
            except Exception:
                with self._lock:
                    self._metrics.failed += 1
                    self._metrics.non_yield_streak += 1
                    self._check_circuit()
                continue
            elapsed = time.monotonic() - started
            with self._lock:
                self._cancelled.discard(task.identity)
                self._metrics.latencies.append(elapsed)
                if yielded:
                    self._metrics.completed += 1
                    self._metrics.non_yield_streak = 0
                else:
                    self._metrics.non_yield_streak += 1
                    self._check_circuit()
        return self._metrics

    def _check_circuit(self) -> None:
        if self._metrics.non_yield_streak >= self.MAX_NON_YIELD_STREAK:
            self._metrics.circuit_open = True

    def reset_circuit(self) -> None:
        """Owner-initiated half-open retry after investigating the trip."""

        with self._lock:
            self._metrics.circuit_open = False
            self._metrics.non_yield_streak = 0

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._metrics.snapshot()


# ---------------------------------------------------------------------------
# Baseline report
# ---------------------------------------------------------------------------


def build_baseline_report(
    registry: ContextRegistry,
    invalidation_index: ReverseInvalidationIndex,
    *,
    scheduler_status: Optional[Mapping[str, Any]] = None,
    cache_hits: int = 0,
    cache_misses: int = 0,
    changed_tu_latencies: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Quantify coverage, failure reasons, variant cost, cache, fan-out."""

    contexts = registry.to_json()["contexts"]
    per_tu: Dict[str, int] = {}
    coverage_counts: Dict[str, int] = {}
    for entry in contexts:
        per_tu[entry["rel_path"]] = per_tu.get(entry["rel_path"], 0) + 1
        coverage_counts[entry["coverage"]] = coverage_counts.get(entry["coverage"], 0) + 1
    duplicate_tus = {path: count for path, count in per_tu.items() if count > 1}
    latencies = sorted(changed_tu_latencies or [])
    top_fanout = invalidation_index.fanout_items()[:20]
    total = max(cache_hits + cache_misses, 1)
    return {
        "version": BASELINE_REPORT_VERSION,
        "context_count": len(contexts),
        "coverage_counts": coverage_counts,
        "rejection_summary": registry.rejection_summary(),
        "tu_count": len(per_tu),
        "duplicate_variant_tus": len(duplicate_tus),
        "duplicate_variant_cost": duplicate_tus,
        "dependency_count": invalidation_index.dependency_count(),
        "top_fanout": [{"dependency": dep, "consumer_count": count} for dep, count in top_fanout],
        "cache_hit_rate": round(cache_hits / total, 4),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "changed_tu_latency_p50_seconds": round(
            latencies[len(latencies) // 2], 6
        ) if latencies else 0.0,
        "changed_tu_latency_p95_seconds": round(
            latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 6
        ) if latencies else 0.0,
        "scheduler": dict(scheduler_status or {}),
    }
