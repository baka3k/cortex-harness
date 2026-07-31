from __future__ import annotations

from typing import Dict, List

from ..models import (
    ConfidenceLevel,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    ParseDepth,
    SourceEvidence,
    safe_summary,
)
from .common import module_path_for_file


def parse_ini(*, project_id: str, path: str, text: str) -> DescriptorParseOutput:
    entries: List[Dict[str, object]] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        entries.append({"key": key.strip(), "value": value.strip(), "line": line_number})
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path_for_file(path),
        path=path,
        descriptor_type=DescriptorType.RUNTIME_CONFIG,
        role=DescriptorRole.CONFIGURATION,
        parser="ini",
        parse_depth=ParseDepth.IDENTITY,
        summary=safe_summary(f"Flat INI configuration with {len(entries)} entries"),
        properties={"entries": entries, "format": "key-value-colon"},
        confidence=ConfidenceLevel.HIGH,
        evidence=(SourceEvidence(path, 1),),
    )
    return DescriptorParseOutput(descriptor=descriptor, dependencies=(), diagnostics=())