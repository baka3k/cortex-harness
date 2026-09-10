"""NuGet dependency extractor.

Converts the worker-emitted project metadata (PackageReference list) into
`NuGetDependency` framework items. The extractor runs once per project, not
per file.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor


class NuGetDependencyExtractor(FrameworkExtractor):
    kind = "NuGetDependency"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        # The project metadata lives at the analyzer level, not on a per-file
        # payload. The orchestrator (roslyn_integration) folds it into a
        # single-element list before calling the extractor, so this method
        # only sees one fake "payload" dict with project metadata attached.
        for payload in file_evidence:
            metadata = payload.get("_project_metadata") or {}
            for package in metadata.get("packages", []) or []:
                yield SemanticFrameworkItem(
                    kind=self.kind,
                    name=f"{package.get('name', '')}@{package.get('version', '')}",
                    qualified_name=package.get("name", ""),
                    file_path=metadata.get("project_path", ""),
                    start_line=0,
                    end_line=0,
                    code="",
                    properties={
                        "package_name": package.get("name", ""),
                        "version": package.get("version", ""),
                        "is_development": bool(package.get("is_development", False)),
                    },
                    confidence=1.0,
                    extraction_method="roslyn",
                    source_symbol_id="",
                )
