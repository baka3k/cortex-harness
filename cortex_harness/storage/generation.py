"""Generation manifests, atomic publication, and reader reference tracking."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator

from .contracts import GenerationManifest, GenerationState, PhysicalTargetKey, utc_now


class GenerationManager:
    """Own one target's active-manifest selection boundary.

    This class deliberately does not open graph/vector clients.  It ensures a
    reader chooses one validated pair and allows callers to defer physical
    cleanup until all pins for that pair have been released.
    """

    def __init__(self, root: Path, target: PhysicalTargetKey, *, retain: int = 1) -> None:
        self.root = Path(root).resolve()
        self.target = target
        self.retain = retain
        self.manifest_path = self.root / "active-generation.json"
        self.generations_root = self.root / "generations"
        self.compatibility_root = self.root / "generation-compatibility"
        self._publication_lock = threading.Lock()
        self._reference_lock = threading.Lock()
        self._references: dict[str, int] = {}

    @staticmethod
    def _safe_generation_id(generation_id: str) -> str:
        value = str(generation_id or "")
        if not value or re.fullmatch(r"[A-Za-z0-9._-]+", value) is None:
            raise ValueError("generation ID is not safe for compatibility metadata")
        return value

    def _compatibility_path(self, generation_id: str) -> Path:
        return self.compatibility_root / f"{self._safe_generation_id(generation_id)}.json"

    def mark_incompatible(
        self,
        generation_id: str,
        *,
        reason: str,
        provenance: str = "unknown_or_legacy_clang_structure",
    ) -> Path:
        """Durably fence a generation without deleting or pointer-flipping it."""

        if not str(reason or "").strip():
            raise ValueError("generation incompatibility requires a reason")
        path = self._compatibility_path(generation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        payload = {
            "schema_version": "1",
            "generation_id": generation_id,
            "compatible": False,
            "reason": str(reason),
            "provenance": str(provenance),
        }
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return path

    def incompatibility(self, generation_id: str) -> dict[str, object] | None:
        try:
            payload = json.loads(
                self._compatibility_path(generation_id).read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        if payload.get("compatible") is not False:
            raise ValueError("invalid generation compatibility marker")
        return payload

    def allocate(self, source_revision: str, *, generation_id: str | None = None) -> GenerationManifest:
        generation_id = generation_id or uuid.uuid4().hex
        generation_root = self.generations_root / generation_id
        return GenerationManifest(
            generation_id=generation_id,
            target=self.target,
            source_revision=source_revision,
            graph_path=str(generation_root / "graph" / "data.rdb"),
            vector_path=str(generation_root / "vector"),
            state=GenerationState.BUILDING,
        )

    def load_active(self) -> GenerationManifest | None:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        manifest = GenerationManifest.from_dict(payload)
        if manifest.target != self.target or manifest.state is not GenerationState.PUBLISHED:
            raise ValueError("active generation manifest does not describe this published physical target")
        self._validate_paths(manifest)
        if self.incompatibility(manifest.generation_id) is not None:
            raise ValueError("active generation is structurally incompatible")
        return manifest

    def publish(self, manifest: GenerationManifest, validate: Callable[[GenerationManifest], None]) -> GenerationManifest:
        if manifest.target != self.target:
            raise ValueError("cannot publish a generation for a different physical target")
        self._validate_paths(manifest)
        if self.incompatibility(manifest.generation_id) is not None:
            raise ValueError("cannot publish a structurally incompatible generation")
        validate(manifest)
        published = replace(
            manifest,
            state=GenerationState.PUBLISHED,
            validated_at=manifest.validated_at or utc_now(),
            published_at=utc_now(),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        encoded = json.dumps(published.to_dict(), sort_keys=True, separators=(",", ":"))
        with self._publication_lock:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
            if os.name != "nt":
                # Windows cannot open a directory with os.open(O_RDONLY); the
                # durability sync is POSIX-only.
                directory_fd = os.open(str(self.root), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        return published

    def _validate_paths(self, manifest: GenerationManifest) -> None:
        generation_root = (self.generations_root / manifest.generation_id).resolve()
        graph_path = Path(manifest.graph_path).resolve()
        vector_path = Path(manifest.vector_path).resolve()
        if graph_path != generation_root / "graph" / "data.rdb" or vector_path != generation_root / "vector":
            raise ValueError("generation paths must be isolated below their generation ID")

    @contextmanager
    def pin_active(self) -> Iterator[GenerationManifest]:
        manifest = self.load_active()
        if manifest is None:
            raise RuntimeError("no active generation is available")
        with self._reference_lock:
            self._references[manifest.generation_id] = self._references.get(manifest.generation_id, 0) + 1
        try:
            yield manifest
        finally:
            with self._reference_lock:
                current = self._references.get(manifest.generation_id, 0)
                if current <= 1:
                    self._references.pop(manifest.generation_id, None)
                else:
                    self._references[manifest.generation_id] = current - 1

    def reference_count(self, generation_id: str) -> int:
        with self._reference_lock:
            return self._references.get(generation_id, 0)

    def retire(self, manifest: GenerationManifest) -> bool:
        """Remove a non-active generation only after readers have drained."""
        active = self.load_active()
        if active is not None and active.generation_id == manifest.generation_id:
            return False
        with self._reference_lock:
            if self._references.get(manifest.generation_id, 0):
                return False
        generation_root = Path(manifest.graph_path).parent.parent
        if generation_root.is_relative_to(self.generations_root):
            shutil.rmtree(generation_root, ignore_errors=True)
        return True
