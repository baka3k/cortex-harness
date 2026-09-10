"""Auth/Authorization extractor (framework-agnostic part).

The ASP.NET overlay already handles HTTP-specific authorization wiring. This
extractor picks up generic `[Authorize]` / `[AllowAnonymous]` attributes on
types and methods, plus `[Authorize(Policy = "...")]` policies.

ASP.NET's `AuthorizationPolicy` builder registration lives in the overlay.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ..models import SemanticFrameworkItem
from .base import FrameworkExtractor, _normalize_attribute_name


AUTHORIZE_POLICY_PATTERN = re.compile(r"Policy\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
AUTHORIZE_ROLES_PATTERN = re.compile(r"Roles\s*=\s*\"([^\"]+)\"", re.IGNORECASE)


class AuthPolicyExtractor(FrameworkExtractor):
    kind = "AuthPolicy"

    def extract(
        self, file_evidence: List[Dict[str, Any]]
    ) -> Iterable[SemanticFrameworkItem]:
        for payload in file_evidence:
            for type_evidence in payload.get("types", []):
                yield from self._iter_for_attributes(
                    payload, type_evidence, type_evidence.get("attributes", []),
                    scope="type",
                )

            for member in payload.get("functions", []):
                yield from self._iter_for_attributes(
                    payload, member, member.get("attributes", []),
                    scope="method",
                )

    def _iter_for_attributes(
        self,
        payload: Dict[str, Any],
        source: Dict[str, Any],
        attributes: Iterable[str],
        *,
        scope: str,
    ) -> Iterable[SemanticFrameworkItem]:
        for attribute in attributes or ():
            attr = str(attribute).strip()
            normalized = _normalize_attribute_name(attr)
            if normalized == "authorize":
                yield self._build(payload, source, attr, scope, source.get("qualified_name", ""))
            elif normalized == "allowanonymous":
                yield SemanticFrameworkItem(
                    kind=self.kind,
                    name=f"AllowAnonymous@{source.get('name', '')}",
                    qualified_name=source.get("qualified_name", ""),
                    file_path=source.get("file_path", "") or payload.get("file_path", ""),
                    start_line=source.get("start_line", 0),
                    end_line=source.get("end_line", 0),
                    code="",
                    properties={"scope": scope, "policy_name": "", "schemes": [], "is_allow_anonymous": True},
                    confidence=0.9,
                    extraction_method="roslyn",
                    source_symbol_id=source.get("symbol_id", ""),
                )

    def _build(
        self,
        payload: Dict[str, Any],
        source: Dict[str, Any],
        attr: str,
        scope: str,
        qualified_name: str,
    ) -> SemanticFrameworkItem:
        policy_match = AUTHORIZE_POLICY_PATTERN.search(attr)
        roles_match = AUTHORIZE_ROLES_PATTERN.search(attr)
        return SemanticFrameworkItem(
            kind=self.kind,
            name=f"Authorize@{source.get('name', '')}",
            qualified_name=qualified_name,
            file_path=source.get("file_path", "") or payload.get("file_path", ""),
            start_line=source.get("start_line", 0),
            end_line=source.get("end_line", 0),
            code="",
            properties={
                "scope": scope,
                "policy_name": policy_match.group(1) if policy_match else "",
                "roles": roles_match.group(1) if roles_match else "",
                "schemes": [],
                "is_allow_anonymous": False,
            },
            confidence=0.85,
            extraction_method="roslyn",
            source_symbol_id=source.get("symbol_id", ""),
        )
