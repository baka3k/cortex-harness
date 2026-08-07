from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from tools.spring.annotation_catalog import (
    APPLICATION_ANNOTATIONS,
    BEAN_ANNOTATIONS,
    COMPONENT_ANNOTATIONS,
    CONFIGURATION_ANNOTATIONS,
    CONTROLLER_ANNOTATIONS,
    HTTP_MAPPING_ANNOTATIONS,
    HTTP_MAPPING_METHODS,
    INJECTION_ANNOTATIONS,
    REPOSITORY_ANNOTATIONS,
    SERVICE_ANNOTATIONS,
    VALUE_ANNOTATIONS,
)
from tools.spring.extractors.common import (
    annotation_map,
    bean_name,
    class_owner_id,
    fact,
    first_annotation,
    has_annotation,
    method_owner_id,
    rel,
    stable_hash,
)
from tools.spring.models import SpringFact, SpringRelationship
from tools.spring.source_scanner import SourceClass, SourceMethod, SourceUnit
from tools.spring.value_resolver import combine_paths, first_arg, list_arg, normalize_path


def extract_core_facts(
    *,
    units: Sequence[SourceUnit],
    project_id: str,
    project_name: str,
) -> Tuple[List[SpringFact], List[SpringRelationship]]:
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    for unit in units:
        for cls in unit.classes:
            class_anns = annotation_map(cls.annotations)
            class_nodes: List[Tuple[str, str]] = []

            app_ann = first_annotation(cls.annotations, APPLICATION_ANNOTATIONS)
            if app_ann or "SpringApplication.run" in cls.code:
                app_id = f"spring_app::{project_id}::{cls.qualified_name}"
                facts.append(
                    fact(
                        kind="SpringApplication",
                        stable_id=app_id,
                        name=cls.name,
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                        application_class=cls.qualified_name,
                        scan_packages=list_arg(app_ann.args if app_ann else {}, "scanBasePackages", "scanBasePackageClasses"),
                        exclusions=list_arg(app_ann.args if app_ann else {}, "exclude", "excludeName"),
                    )
                )
                class_nodes.append(("SpringApplication", app_id))

            config_ann = first_annotation(cls.annotations, CONFIGURATION_ANNOTATIONS)
            if config_ann:
                config_id = f"spring_config::{project_id}::{stable_hash(class_owner_id(cls))}"
                facts.append(
                    fact(
                        kind="SpringConfiguration",
                        stable_id=config_id,
                        name=cls.name,
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                        proxy_bean_methods=first_arg(config_ann.args, "proxyBeanMethods", default=""),
                        profiles=_annotation_values(class_anns, "Profile"),
                        conditions=_condition_annotations(cls.annotations),
                    )
                )
                class_nodes.append(("SpringConfiguration", config_id))

            stereotype = first_annotation(cls.annotations, COMPONENT_ANNOTATIONS)
            bean_id = ""
            if stereotype:
                bean = bean_name(cls.name, stereotype)
                bean_id = f"spring_bean::{project_id}::{stable_hash(class_owner_id(cls))}::{bean}"
                facts.append(
                    fact(
                        kind="SpringBean",
                        stable_id=bean_id,
                        name=bean,
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                        bean_type=cls.qualified_name,
                        stereotype=stereotype.short_name,
                        qualifiers=_annotation_values(class_anns, "Qualifier"),
                        primary="Primary" in class_anns,
                        profiles=_annotation_values(class_anns, "Profile"),
                    )
                )
                class_nodes.append(("SpringBean", bean_id))

            if has_annotation(cls.annotations, CONTROLLER_ANNOTATIONS):
                controller_id = f"spring_controller::{project_id}::{stable_hash(class_owner_id(cls))}"
                facts.append(
                    fact(
                        kind="Controller",
                        stable_id=controller_id,
                        name=cls.name,
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                        controller_class=cls.qualified_name,
                        controller_type=first_annotation(cls.annotations, CONTROLLER_ANNOTATIONS).short_name,
                    )
                )
                class_nodes.append(("Controller", controller_id))

            if has_annotation(cls.annotations, SERVICE_ANNOTATIONS):
                service_id = f"spring_service::{project_id}::{stable_hash(class_owner_id(cls))}"
                facts.append(
                    fact(
                        kind="Service",
                        stable_id=service_id,
                        name=cls.name,
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                        service_class=cls.qualified_name,
                    )
                )
                class_nodes.append(("Service", service_id))

            if has_annotation(cls.annotations, REPOSITORY_ANNOTATIONS):
                repo_id = f"spring_repo::{project_id}::{stable_hash(class_owner_id(cls))}"
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
                        repository_kind="spring_repository_stereotype",
                    )
                )
                class_nodes.append(("DataRepository", repo_id))

            for node_label, node_id in class_nodes:
                relationships.append(
                    rel("SEMANTIC_OF", node_label, node_id, "Class", class_owner_id(cls), project_id, cls.source, "Spring semantic class anchor")
                )

            method_facts, method_rels = _extract_methods(
                cls=cls,
                project_id=project_id,
                project_name=project_name,
                bean_id=bean_id,
            )
            facts.extend(method_facts)
            relationships.extend(method_rels)

    return facts, relationships


def _extract_methods(
    *,
    cls: SourceClass,
    project_id: str,
    project_name: str,
    bean_id: str,
) -> Tuple[List[SpringFact], List[SpringRelationship]]:
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    class_paths = _mapping_paths(cls.annotations)
    controller_id = f"spring_controller::{project_id}::{stable_hash(class_owner_id(cls))}"
    for method in cls.methods:
        anns = annotation_map(method.annotations)
        bean_ann = first_annotation(method.annotations, BEAN_ANNOTATIONS)
        if bean_ann:
            produced_name = bean_name(method.name, bean_ann)
            produced_id = f"spring_bean::{project_id}::{stable_hash(method_owner_id(method))}::{produced_name}"
            facts.append(
                fact(
                    kind="SpringBean",
                    stable_id=produced_id,
                    name=produced_name,
                    source=method.source,
                    project_id=project_id,
                    project_name=project_name,
                    language=method.language,
                    source_symbol_id=method_owner_id(method),
                    bean_type=method.return_type,
                    origin="bean_method",
                    qualifiers=_annotation_values(anns, "Qualifier"),
                    primary="Primary" in anns,
                    scope=_first_annotation_value(anns, "Scope"),
                )
            )
            relationships.append(rel("SEMANTIC_OF", "SpringBean", produced_id, "Function", method_owner_id(method), project_id, method.source, "Bean method anchor"))
            if bean_id:
                relationships.append(rel("PRODUCES_BEAN", "SpringBean", bean_id, "SpringBean", produced_id, project_id, method.source, "@Bean factory method"))

        if has_annotation(method.annotations, INJECTION_ANNOTATIONS) and bean_id:
            site_id = f"spring_injection::{project_id}::{stable_hash(method_owner_id(method))}"
            facts.append(
                fact(
                    kind="SpringBean",
                    stable_id=site_id,
                    name=f"{cls.name}.{method.name}",
                    source=method.source,
                    project_id=project_id,
                    project_name=project_name,
                    language=method.language,
                    confidence=0.72,
                    resolution_status="unresolved",
                    source_symbol_id=method_owner_id(method),
                    injection_kind="method",
                    raw_value=method.params,
                )
            )
            relationships.append(rel("POSSIBLE_INJECTION", "SpringBean", bean_id, "SpringBean", site_id, project_id, method.source, "Annotated injection site", 0.72, "unresolved"))

        for value_ann in [ann for ann in method.annotations if ann.short_name in VALUE_ANNOTATIONS]:
            value_id = f"spring_config_binding::{project_id}::{stable_hash(method_owner_id(method) + value_ann.raw)}"
            facts.append(
                fact(
                    kind="SpringConfiguration",
                    stable_id=value_id,
                    name=f"{cls.name}.{method.name}",
                    source=method.source,
                    project_id=project_id,
                    project_name=project_name,
                    language=method.language,
                    raw_value=value_ann.raw_args,
                    source_symbol_id=method_owner_id(method),
                    binding_kind=value_ann.short_name,
                    binding_value=value_ann.args.get("value") or value_ann.args.get("prefix") or "",
                )
            )

        method_mapping = first_annotation(method.annotations, HTTP_MAPPING_ANNOTATIONS)
        if method_mapping:
            method_paths = _mapping_paths(method.annotations)
            http_methods = _mapping_methods(method_mapping)
            for path in combine_paths(class_paths, method_paths):
                for http_method in http_methods:
                    endpoint_id = f"spring_endpoint::{project_id}::{stable_hash(method_owner_id(method))}::{http_method}::{stable_hash(path)}"
                    facts.append(
                        fact(
                            kind="ApiEndpoint",
                            stable_id=endpoint_id,
                            name=f"{http_method} {path}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method_owner_id(method),
                            path=path,
                            http_method=http_method,
                            handler_names=[method.name],
                            controller_class=cls.qualified_name,
                            framework="spring",
                        )
                    )
                    relationships.append(rel("SEMANTIC_OF", "ApiEndpoint", endpoint_id, "Function", method_owner_id(method), project_id, method.source, "Endpoint handler anchor"))
                    relationships.append(rel("HANDLES", "Controller", controller_id, "ApiEndpoint", endpoint_id, project_id, method.source, "Controller handles endpoint"))
    return facts, relationships


def _mapping_paths(annotations) -> List[str]:
    paths: List[str] = []
    for ann in annotations:
        if ann.short_name not in HTTP_MAPPING_ANNOTATIONS:
            continue
        paths.extend(list_arg(ann.args, "value", "path"))
    return [normalize_path(path) for path in paths] or [""]


def _mapping_methods(annotation) -> List[str]:
    if annotation.short_name in HTTP_MAPPING_METHODS:
        return [HTTP_MAPPING_METHODS[annotation.short_name]]
    values = list_arg(annotation.args, "method")
    out: List[str] = []
    for value in values:
        token = str(value).rsplit(".", 1)[-1].strip("{} ").upper()
        if token:
            out.append(token)
    return out or ["ANY"]


def _annotation_values(anns: Dict[str, object], name: str) -> List[str]:
    ann = anns.get(name)
    if not ann:
        return []
    value = ann.args.get("value") or ann.args.get("name") or ann.args.get("prefix") or ""
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)] if value else []


def _first_annotation_value(anns: Dict[str, object], name: str) -> str:
    values = _annotation_values(anns, name)
    return values[0] if values else ""


def _condition_annotations(annotations) -> List[str]:
    return [ann.raw for ann in annotations if ann.short_name.startswith("Conditional")]
