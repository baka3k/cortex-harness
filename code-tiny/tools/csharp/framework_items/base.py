"""Base class and shared utilities for framework-agnostic C# extractors.

The primary C# analyzer ONLY extracts framework items that ASP.NET overlays do
not already own (DI registration, middleware, minimal API, configuration binding).
These extractors pattern-match Roslyn evidence to emit
`SemanticFrameworkItem` records.

Each extractor:
1. Receives a list of legacy-shaped type/member/attribute dicts (the output of
   `tools.csharp.models.roslyn_evidence_to_payload`) per file.
2. Pattern-matches known framework signatures.
3. Emits `SemanticFrameworkItem` records.
4. Links back to canonical C# symbols via `source_symbol_id`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Tuple


from ..models import SemanticFrameworkItem


def _normalize_attribute_name(value: str) -> str:
    """Strip the C# 'Attribute' suffix using suffix semantics, not char-set stripping.

    `'AuthorizeAttribute'.removesuffix('Attribute')` -> 'Authorize', whereas the
    naive `rstrip('Attribute')` would only strip individual characters and
    yield e.g. `'Authorize'` anyway but `''` for short names — wrong.
    """
    lowered = value.strip().lower()
    if lowered.endswith("attribute"):
        return lowered[: -len("attribute")]
    return lowered


class FrameworkExtractor(ABC):
    """Pattern-matches one framework item kind against Roslyn evidence."""

    kind: str = ""

    @abstractmethod
    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        ...

    @staticmethod
    def base_type_chain(type_evidence: Dict[str, Any]) -> List[str]:
        return [
            str(item) for item in type_evidence.get("base_types", []) or []
        ] + [
            str(item) for item in type_evidence.get("implemented_interfaces", []) or []
        ]

    @staticmethod
    def extract_attribute(attributes: Iterable[str], name: str) -> bool:
        target = _normalize_attribute_name(name)
        for attribute in attributes or ():
            if _normalize_attribute_name(str(attribute)) == target:
                return True
        return False


def run_extractors(
    files: List[Tuple[str, Dict[str, Any]]],
    extractors: List[FrameworkExtractor],
) -> List[SemanticFrameworkItem]:
    """Run each extractor across the (file_path, evidence_payload) pairs."""
    items: List[SemanticFrameworkItem] = []
    for _, payload in files:
        for extractor in extractors:
            items.extend(extractor.extract([payload]))
    return items
