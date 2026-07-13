from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from tools.spring.annotation_catalog import SECURITY_METHOD_ANNOTATIONS
from tools.spring.extractors.common import fact, first_annotation, rel, stable_hash
from tools.spring.models import SpringFact, SpringRelationship
from tools.spring.source_scanner import SourceUnit
from tools.spring.value_resolver import extract_string_literals, list_arg


def extract_security_facts(
    *,
    units: Sequence[SourceUnit],
    project_id: str,
    project_name: str,
) -> Tuple[List[SpringFact], List[SpringRelationship]]:
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    for unit in units:
        for cls in unit.classes:
            for method in cls.methods:
                if "SecurityFilterChain" in method.return_type or "HttpSecurity" in method.params or "authorizeHttpRequests" in method.code:
                    chain_id = f"spring_security_chain::{project_id}::{stable_hash(method.symbol_id)}"
                    facts.append(
                        fact(
                            kind="SecurityFilterChain",
                            stable_id=chain_id,
                            name=f"{cls.name}.{method.name}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            matcher=_first_matcher(method.code),
                            chain_bean=method.name,
                        )
                    )
                    relationships.append(rel("SEMANTIC_OF", chain_id, method.symbol_id, project_id, method.source, "SecurityFilterChain bean anchor"))
                    for idx, rule in enumerate(_security_rules(method.code)):
                        rule_id = f"spring_security_rule::{chain_id}::{idx}"
                        facts.append(
                            fact(
                                kind="SecurityRule",
                                stable_id=rule_id,
                                name=rule.get("matcher") or "anyRequest",
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                raw_value=rule.get("raw", ""),
                                matcher=rule.get("matcher", ""),
                                decision=rule.get("decision", ""),
                                order=idx,
                            )
                        )
                        relationships.append(rel("HAS_RULE", chain_id, rule_id, project_id, method.source, "Ordered Spring Security rule", order=idx))
                        for authority_kind, authority_value in _authorities_from_expression(rule.get("raw", "")):
                            auth_id = _authority_id(project_id, authority_kind, authority_value)
                            facts.append(
                                fact(
                                    kind="Authority",
                                    stable_id=auth_id,
                                    name=authority_value,
                                    source=method.source,
                                    project_id=project_id,
                                    project_name=project_name,
                                    language=method.language,
                                    authority_kind=authority_kind,
                                    canonical_value=authority_value,
                                    raw_value=authority_value,
                                )
                            )
                            relationships.append(rel("REQUIRES_AUTHORITY", rule_id, auth_id, project_id, method.source, "Security rule authority"))

                method_sec = first_annotation(method.annotations, SECURITY_METHOD_ANNOTATIONS)
                if method_sec:
                    rule_id = f"spring_security_rule::{project_id}::{stable_hash(method.symbol_id + method_sec.raw)}"
                    facts.append(
                        fact(
                            kind="SecurityRule",
                            stable_id=rule_id,
                            name=f"{cls.name}.{method.name}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            raw_value=method_sec.raw_args,
                            decision=method_sec.short_name,
                            expression=method_sec.args.get("value") or "",
                            method_rule=True,
                        )
                    )
                    relationships.append(rel("PROTECTS", rule_id, method.symbol_id, project_id, method.source, "Method security annotation"))
                    for authority_kind, authority_value in _authorities_from_annotation(method_sec):
                        auth_id = _authority_id(project_id, authority_kind, authority_value)
                        facts.append(
                            fact(
                                kind="Authority",
                                stable_id=auth_id,
                                name=authority_value,
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                authority_kind=authority_kind,
                                canonical_value=authority_value,
                                raw_value=authority_value,
                            )
                        )
                        relationships.append(rel("REQUIRES_AUTHORITY", rule_id, auth_id, project_id, method.source, "Method security authority"))
    return facts, relationships


def _security_rules(code: str) -> List[dict]:
    rules: List[dict] = []
    for match in re.finditer(r"(requestMatchers|securityMatcher)\s*\(([^)]*)\)(?P<trail>[\s\S]{0,220}?)(permitAll|denyAll|authenticated|anonymous|hasRole|hasAnyRole|hasAuthority|hasAnyAuthority)\s*(?:\(([^)]*)\))?", code or ""):
        matcher_values = extract_string_literals(match.group(2))
        rules.append({
            "matcher": matcher_values[0] if matcher_values else match.group(2).strip(),
            "decision": match.group(4),
            "raw": match.group(0),
        })
    if "anyRequest()" in (code or ""):
        terminal = re.search(r"anyRequest\(\)(?P<trail>[\s\S]{0,120}?)(permitAll|denyAll|authenticated|anonymous)", code or "")
        rules.append({"matcher": "anyRequest", "decision": terminal.group(2) if terminal else "unknown", "raw": terminal.group(0) if terminal else "anyRequest()"})
    return rules


def _first_matcher(code: str) -> str:
    match = re.search(r"securityMatcher\s*\(([^)]*)\)", code or "")
    return match.group(1).strip() if match else ""


def _authorities_from_expression(raw: str):
    out = []
    for func, args in re.findall(r"(hasRole|hasAnyRole|hasAuthority|hasAnyAuthority)\s*\(([^)]*)\)", raw or ""):
        kind = "role" if "Role" in func else "authority"
        for value in extract_string_literals(args):
            out.append((kind, _canonical_authority(kind, value)))
    return out


def _authorities_from_annotation(annotation):
    if annotation.short_name in {"Secured", "RolesAllowed"}:
        return [("role", _canonical_authority("role", value)) for value in list_arg(annotation.args, "value")]
    return _authorities_from_expression(str(annotation.args.get("value") or annotation.raw_args))


def _canonical_authority(kind: str, value: str) -> str:
    text = str(value or "").strip()
    if kind == "role" and not text.startswith("ROLE_"):
        return "ROLE_" + text
    return text


def _authority_id(project_id: str, kind: str, value: str) -> str:
    return f"spring_authority::{project_id}::{kind}::{stable_hash(value)}"
