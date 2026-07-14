"""Staged canonical Dart graph pipeline."""

from __future__ import annotations

from typing import Any, Dict

from .models import AnalysisFacts
from .normalizer import CanonicalBatch, normalize_facts


async def write_canonical_batch(writer: Any, batch: CanonicalBatch) -> Dict[str, int]:
    """Write a fully validated batch through the provider-neutral language writer."""
    return await writer.write_all(
        files=batch.files,
        classes=batch.classes,
        types=batch.types,
        functions=batch.functions,
        fields=batch.fields,
        relations=batch.relations,
        use_full_writers=True,
        files_variant="with_imports",
    )


async def normalize_and_write(
    writer: Any,
    facts: AnalysisFacts,
    *,
    project_name: str | None = None,
    repo: str = "",
    build_system: str = "flutter",
) -> tuple[CanonicalBatch, Dict[str, int]]:
    batch = normalize_facts(
        facts,
        project_name=project_name,
        repo=repo,
        build_system=build_system,
    )
    counts = await write_canonical_batch(writer, batch)
    return batch, counts
