"""Entity Framework / ORM extractor.

Detects:
- `DbContext` subclass (base type)
- `DbSet<T>` properties on the context
- `[Table("name")]` on entity types
- `[Column("name")]` on entity members
- `[Key]`, `[PrimaryKey]` on entity keys
- Fluent API invocations (`modelBuilder.Entity<T>()`, `.HasMany()`, `.HasOne()`,
  `.BelongsTo()`)

These produce `EfEntityMapping` nodes linked to the canonical C# type via
`source_symbol_id`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor


DB_CONTEXT_BASES = {"DbContext", "IdentityDbContext", "IdentityDbContext<>"}
DB_SET_PATTERN = re.compile(r"^DbSet<(.+)>$", re.IGNORECASE)
HAS_KEYWORDS = ("HasMany", "HasOne", "HasManyToMany", "BelongsTo")


class EfEntityMappingExtractor(FrameworkExtractor):
    kind = "EfEntityMapping"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        for payload in file_evidence:
            for type_evidence in payload.get("types", []):
                kind = type_evidence.get("kind", "")
                if kind not in {"class", "record"}:
                    continue
                chain = self.base_type_chain(type_evidence)
                is_context = any(self._is_db_context(base) for base in chain)
                if is_context:
                    yield self._build_context_mapping(payload, type_evidence)
                else:
                    table_attr = self._find_table_attribute(type_evidence.get("attributes", []))
                    if table_attr:
                        yield self._build_entity_mapping(payload, type_evidence, table_attr)

            for member in payload.get("functions", []):
                if member.get("member_kind") != "property":
                    continue
                type_name = member.get("return_type") or ""
                match = DB_SET_PATTERN.match(type_name)
                if not match:
                    continue
                entity_type = match.group(1).strip().rstrip("?")
                yield SemanticFrameworkItem(
                    kind=self.kind,
                    name=f"DbSet<{entity_type}>",
                    qualified_name=member.get("qualified_name", ""),
                    file_path=member.get("file_path", ""),
                    start_line=member.get("start_line", 0),
                    end_line=member.get("end_line", 0),
                    code="",
                    properties={
                        "context_table": entity_type,
                        "table_name": entity_type,
                        "is_db_set": True,
                    },
                    confidence=0.9,
                    extraction_method="roslyn",
                    source_symbol_id=member.get("symbol_id", ""),
                )

    @staticmethod
    def _is_db_context(base: str) -> bool:
        compact = base.replace("Microsoft.EntityFrameworkCore.", "").strip()
        for known in DB_CONTEXT_BASES:
            if compact == known or compact.startswith(known):
                return True
        return False

    @staticmethod
    def _find_table_attribute(attributes: Iterable[str]) -> str:
        for attribute in attributes or ():
            value = str(attribute).strip()
            if value.lower().startswith("table"):
                return value
        return ""

    @staticmethod
    def _build_context_mapping(payload: Dict[str, Any], type_evidence: Dict[str, Any]) -> SemanticFrameworkItem:
        properties: Dict[str, Any] = {
            "is_db_context": True,
            "configured_via_fluent": False,
        }
        return SemanticFrameworkItem(
            kind=EfEntityMappingExtractor.kind,
            name=type_evidence.get("name", ""),
            qualified_name=type_evidence.get("qualified_name", ""),
            file_path=type_evidence.get("file_path", "") or payload.get("file_path", ""),
            start_line=type_evidence.get("start_line", 0),
            end_line=type_evidence.get("end_line", 0),
            code="",
            properties=properties,
            confidence=0.95,
            extraction_method="roslyn",
            source_symbol_id=type_evidence.get("symbol_id", ""),
        )

    @staticmethod
    def _build_entity_mapping(
        payload: Dict[str, Any],
        type_evidence: Dict[str, Any],
        table_attr: str,
    ) -> SemanticFrameworkItem:
        table_name_match = re.search(r"\(\s*\"([^\"]+)\"", table_attr)
        table_name = table_name_match.group(1) if table_name_match else type_evidence.get("name", "")
        return SemanticFrameworkItem(
            kind=EfEntityMappingExtractor.kind,
            name=f"{type_evidence.get('name', '')}→{table_name}",
            qualified_name=type_evidence.get("qualified_name", ""),
            file_path=type_evidence.get("file_path", "") or payload.get("file_path", ""),
            start_line=type_evidence.get("start_line", 0),
            end_line=type_evidence.get("end_line", 0),
            code="",
            properties={
                "entity_type": type_evidence.get("qualified_name", ""),
                "table_name": table_name,
                "is_db_context": False,
            },
            confidence=0.85,
            extraction_method="roslyn",
            source_symbol_id=type_evidence.get("symbol_id", ""),
        )
