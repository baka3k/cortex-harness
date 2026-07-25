"""Safe identity-level adapters for common ecosystem manifests."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from ..models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    DiagnosticCode,
    ParseDepth,
    safe_summary,
)
from .common import evidence, module_path_for_file


_NAME_PATTERNS = (
    re.compile(r'(?m)^\s*(?:name|module|package)\s*[=:]\s*["\']?([^"\'\s,}]+)'),
    re.compile(r'(?m)^\s*module\s+([^\s]+)'),
)


def parse_identity_manifest(
    *,
    project_id: str,
    path: str,
    text: str,
    parser: str = "manifest",
    role: DescriptorRole = DescriptorRole.IDENTITY,
    parse_depth: ParseDepth = ParseDepth.IDENTITY,
    secret_bearing: bool = False,
    generated: bool = False,
) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics = []
    properties: Dict[str, Any] = {}
    name = ""
    if path.lower().endswith(".json"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                name = str(payload.get("name") or payload.get("module") or "")
                for key in ("version", "type", "private", "workspaces"):
                    if key in payload and key != "workspaces":
                        properties[key] = payload[key]
                if isinstance(payload.get("workspaces"), list):
                    properties["declared_modules"] = sorted(
                        str(item) for item in payload["workspaces"] if isinstance(item, str)
                    )
        except (ValueError, TypeError) as exc:
            diagnostics.append(
                AnalysisDiagnostic(
                    DiagnosticCode.MALFORMED_DESCRIPTOR,
                    f"Malformed JSON manifest: {exc}",
                    file_path=path,
                    module_path=module_path,
                )
            )
    if not name:
        for pattern in _NAME_PATTERNS:
            match = pattern.search(text)
            if match:
                name = match.group(1)
                break
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.PACKAGE_MANIFEST,
        role=role,
        parser=parser,
        parse_depth=parse_depth,
        generated=generated,
        canonical=not generated,
        secret_bearing=secret_bearing,
        redacted=secret_bearing,
        summary=(
            "[redacted configuration]"
            if secret_bearing
            else safe_summary(f"{parser} manifest {name or path}")
        ),
        properties={"name": name, **properties},
        confidence=ConfidenceLevel.MEDIUM if diagnostics else ConfidenceLevel.HIGH,
        evidence=evidence(path),
        diagnostics=tuple(diagnostics),
    )
    return DescriptorParseOutput(
        descriptor=descriptor,
        diagnostics=tuple(diagnostics),
    )
