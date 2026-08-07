from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from tools.spring.annotation_catalog import ENTITY_ANNOTATIONS, REPOSITORY_SUPERTYPES, TRANSACTION_ANNOTATIONS
from tools.spring.extractors.common import class_owner_id, fact, first_annotation, generic_args, parse_extends_types, rel, stable_hash
from tools.spring.models import SpringFact, SpringRelationship
from tools.spring.source_scanner import SourceUnit


_DERIVED_QUERY_PREFIX_RE = re.compile(r"^(find|read|get|query|search|stream|count|exists|delete|remove)(\w*)By(\w+)")


def extract_persistence_facts(
    *,
    units: Sequence[SourceUnit],
    project_id: str,
    project_name: str,
) -> Tuple[List[SpringFact], List[SpringRelationship]]:
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    entity_by_name = {}
    repos = []

    for unit in units:
        for cls in unit.classes:
            entity_ann = first_annotation(cls.annotations, ENTITY_ANNOTATIONS)
            if entity_ann:
                entity_id = f"spring_entity::{project_id}::{stable_hash(class_owner_id(cls))}"
                entity_name = entity_ann.args.get("name") or cls.name
                facts.append(
                    fact(
                        kind="JpaEntity",
                        stable_id=entity_id,
                        name=str(entity_name),
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                        entity_class=cls.qualified_name,
                        entity_kind=entity_ann.short_name,
                        table_name=_annotation_value(cls.annotations, "Table", "name"),
                    )
                )
                relationships.append(rel("SEMANTIC_OF", "JpaEntity", entity_id, "Class", class_owner_id(cls), project_id, cls.source, "JPA entity anchor"))
                entity_by_name[cls.name] = entity_id

    for unit in units:
        for cls in unit.classes:
            supers = parse_extends_types(cls.header)
            repo_super = _repository_supertype(supers)
            if not repo_super:
                continue
            args = generic_args(repo_super)
            entity_type = args[0] if args else ""
            id_type = args[1] if len(args) > 1 else ""
            repo_id = f"spring_repo::{project_id}::{stable_hash(class_owner_id(cls))}"
            repos.append((repo_id, entity_type))
            facts.append(
                fact(
                    kind="DataRepository",
                    stable_id=repo_id,
                    name=cls.name,
                    source=cls.source,
                    project_id=project_id,
                    project_name=project_name,
                    language=cls.language,
                    source_symbol_id=class_owner_id(cls),
                    repository_class=cls.qualified_name,
                    repository_kind=repo_super.split("<", 1)[0],
                    entity_type=entity_type,
                    id_type=id_type,
                )
            )
            relationships.append(rel("SEMANTIC_OF", "DataRepository", repo_id, "Class", class_owner_id(cls), project_id, cls.source, "Spring Data repository anchor"))
            entity_id = entity_by_name.get(entity_type.rsplit(".", 1)[-1])
            if entity_id:
                relationships.append(rel("MANAGES_ENTITY", "DataRepository", repo_id, "JpaEntity", entity_id, project_id, cls.source, "Repository generic entity type"))

            for method in cls.methods:
                query_ann = first_annotation(method.annotations, {"Query"})
                if query_ann:
                    query_id = f"spring_repo_query::{project_id}::{stable_hash(method.symbol_id + query_ann.raw)}"
                    facts.append(
                        fact(
                            kind="DataRepository",
                            stable_id=query_id,
                            name=method.name,
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            repository_kind="query_method",
                            query=query_ann.args.get("value") or "",
                            native_query=query_ann.args.get("nativeQuery") or False,
                            modifying=bool(first_annotation(method.annotations, {"Modifying"})),
                        )
                    )
                    relationships.append(rel("DECLARES_QUERY", "DataRepository", repo_id, "DataRepository", query_id, project_id, method.source, "@Query repository method"))
                derived = _parse_derived_query(method.name)
                if derived:
                    derived_id = f"spring_repo_derived::{project_id}::{stable_hash(method.symbol_id)}"
                    facts.append(
                        fact(
                            kind="DataRepository",
                            stable_id=derived_id,
                            name=method.name,
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            repository_kind="derived_query_method",
                            derived_query=derived,
                            return_type=method.return_type,
                        )
                    )
                    relationships.append(rel("DERIVES_QUERY", "DataRepository", repo_id, "DataRepository", derived_id, project_id, method.source, "Spring Data derived method name"))

    for unit in units:
        for cls in unit.classes:
            tx_facts, tx_rels = _transaction_facts_for_class(cls, project_id, project_name)
            facts.extend(tx_facts)
            relationships.extend(tx_rels)
            tx_facts, tx_rels = _transaction_facts_for_methods(cls, project_id, project_name)
            facts.extend(tx_facts)
            relationships.extend(tx_rels)
    return facts, relationships


def _repository_supertype(supers: Sequence[str]) -> str:
    for item in supers:
        short = item.split("<", 1)[0].rsplit(".", 1)[-1].strip()
        if short in REPOSITORY_SUPERTYPES:
            return item
    return ""


def _parse_derived_query(name: str) -> dict:
    match = _DERIVED_QUERY_PREFIX_RE.match(name or "")
    if not match:
        return {}
    return {
        "action": match.group(1),
        "subject": match.group(2) or "",
        "predicate": match.group(3),
        "tokens": re.split(r"(And|Or|Between|LessThan|GreaterThan|Like|In|OrderBy)", match.group(3)),
    }


def _transaction_facts_for_class(cls, project_id: str, project_name: str):
    ann = first_annotation(cls.annotations, TRANSACTION_ANNOTATIONS)
    if not ann:
        return [], []
    tx_id = f"spring_tx::{project_id}::{stable_hash(class_owner_id(cls))}:{ann.line}:0"
    fact_row = fact(
        kind="TransactionBoundary",
        stable_id=tx_id,
        name=cls.name,
        source=cls.source,
        project_id=project_id,
        project_name=project_name,
        language=cls.language,
        source_symbol_id=class_owner_id(cls),
        propagation=ann.args.get("propagation") or "",
        isolation=ann.args.get("isolation") or "",
        read_only=ann.args.get("readOnly") or False,
        target_kind="class",
    )
    return [fact_row], [rel("APPLIES_TO", "TransactionBoundary", tx_id, "Class", class_owner_id(cls), project_id, cls.source, "Class @Transactional")]


def _transaction_facts_for_methods(cls, project_id: str, project_name: str):
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    for idx, method in enumerate(cls.methods):
        ann = first_annotation(method.annotations, TRANSACTION_ANNOTATIONS)
        if not ann and "TransactionTemplate" not in method.code:
            continue
        tx_id = f"spring_tx::{project_id}::{stable_hash(method.symbol_id)}:{method.source.start_line}:{idx}"
        facts.append(
            fact(
                kind="TransactionBoundary",
                stable_id=tx_id,
                name=f"{cls.name}.{method.name}",
                source=method.source,
                project_id=project_id,
                project_name=project_name,
                language=method.language,
                source_symbol_id=method.symbol_id,
                propagation=ann.args.get("propagation") if ann else "",
                isolation=ann.args.get("isolation") if ann else "",
                read_only=ann.args.get("readOnly") if ann else False,
                target_kind="method",
                extraction_method="transaction_template" if not ann else "annotation",
            )
        )
        relationships.append(rel("APPLIES_TO", "TransactionBoundary", tx_id, "Function", method.symbol_id, project_id, method.source, "Transactional boundary applies to method"))
    return facts, relationships


def _annotation_value(annotations, ann_name: str, arg_name: str) -> str:
    ann = first_annotation(annotations, {ann_name})
    if not ann:
        return ""
    value = ann.args.get(arg_name) or ann.args.get("value") or ""
    return str(value)
