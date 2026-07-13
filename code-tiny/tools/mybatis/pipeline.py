from __future__ import annotations

import hashlib
import os
from typing import Dict, List, Sequence, Tuple

from tools.mybatis.annotation_mapper import analyze_annotation_mappers
from tools.mybatis.detector import MyBatisProjectDetector, safe_rel_path
from tools.mybatis.mapper_interface_analyzer import analyze_mapper_interfaces
from tools.mybatis.mapper_xml_analyzer import analyze_mapper_xml_files
from tools.mybatis.resolver import resolve_mybatis_relationships
from tools.mybatis.spring_bridge import analyze_optional_extensions
from tools.mybatis.sql_semantic_analyzer import analyze_sql_semantics
from tools.mybatis.models import (
    Diagnostic,
    JavaSourceFact,
    MyBatisAnalysisResult,
    MyBatisCacheFact,
    MyBatisArtifact,
    MyBatisConfigFact,
    MyBatisDependencyIndex,
    MyBatisDynamicSqlNodeFact,
    MyBatisExtensionFact,
    MyBatisFact,
    MyBatisIncludeFact,
    MyBatisJavaPropertyFact,
    MyBatisMapperInterfaceFact,
    MyBatisMapperMethodFact,
    MyBatisMapperParameterFact,
    MyBatisModule,
    MyBatisProviderFact,
    MyBatisResultMapFact,
    MyBatisResultMappingFact,
    MyBatisSpringBridgeFact,
    MyBatisSqlFragmentFact,
    MyBatisSqlColumnFact,
    MyBatisSqlJoinFact,
    MyBatisSqlParameterFact,
    MyBatisSqlStatementSemanticFact,
    MyBatisSqlTableFact,
    MyBatisStatementFact,
    MyBatisXmlDocumentFact,
    SourceSpan,
    SqlSourceFact,
    XmlSourceFact,
)
from tools.mybatis.parser_runtime import check_parser_capabilities


def run_mybatis_foundation(
    *,
    root: str,
    project_id: str,
    project_name: str,
    languages: Sequence[str] = ("java", "kotlin"),
) -> MyBatisAnalysisResult:
    root_abs = os.path.abspath(root)
    detector = MyBatisProjectDetector(root_abs)
    modules_raw = detector.discover_modules(languages=languages)
    parser_capabilities, parser_diagnostics = check_parser_capabilities()

    modules: List[MyBatisModule] = []
    artifacts: List[MyBatisArtifact] = []
    xml_facts: List[XmlSourceFact] = []
    java_facts: List[JavaSourceFact] = []
    sql_facts: List[SqlSourceFact] = []
    mapper_interfaces: List[MyBatisMapperInterfaceFact] = []
    mapper_methods: List[MyBatisMapperMethodFact] = []
    mapper_parameters: List[MyBatisMapperParameterFact] = []
    java_properties: List[MyBatisJavaPropertyFact] = []
    xml_documents: List[MyBatisXmlDocumentFact] = []
    statements: List[MyBatisStatementFact] = []
    sql_fragments: List[MyBatisSqlFragmentFact] = []
    result_maps: List[MyBatisResultMapFact] = []
    result_mappings: List[MyBatisResultMappingFact] = []
    includes: List[MyBatisIncludeFact] = []
    dynamic_nodes: List[MyBatisDynamicSqlNodeFact] = []
    config_facts: List[MyBatisConfigFact] = []
    provider_facts: List[MyBatisProviderFact] = []
    spring_bridge_facts: List[MyBatisSpringBridgeFact] = []
    extension_facts: List[MyBatisExtensionFact] = []
    cache_facts: List[MyBatisCacheFact] = []
    sql_statement_semantics: List[MyBatisSqlStatementSemanticFact] = []
    sql_tables: List[MyBatisSqlTableFact] = []
    sql_columns: List[MyBatisSqlColumnFact] = []
    sql_joins: List[MyBatisSqlJoinFact] = []
    sql_parameters: List[MyBatisSqlParameterFact] = []
    semantic_facts: List[MyBatisFact] = []
    diagnostics: List[Diagnostic] = list(parser_diagnostics)

    for item in modules_raw:
        module = MyBatisModule(
            root=".",
            rel_path=str(item["rel_path"]),
            mapper_xml_files=tuple(item["mapper_xml_files"]),  # type: ignore[arg-type]
            config_xml_files=tuple(item["config_xml_files"]),  # type: ignore[arg-type]
            java_files=tuple(item["java_files"]),  # type: ignore[arg-type]
            build_files=tuple(item["build_files"]),  # type: ignore[arg-type]
            spring_config_files=tuple(item["spring_config_files"]),  # type: ignore[arg-type]
            evidence=tuple(item["evidence"]),  # type: ignore[arg-type]
            confidence=float(item["confidence"]),
        )
        modules.append(module)
        semantic_facts.append(_module_fact(project_id, project_name, module))
        for kind, paths in (
            ("mapper_xml", module.mapper_xml_files),
            ("config_xml", module.config_xml_files),
            ("java_mapper", module.java_files),
            ("build", module.build_files),
            ("spring_xml", module.spring_config_files),
        ):
            for rel_path in paths:
                detection = detector.detect_path(rel_path)
                artifact = MyBatisArtifact(
                    kind=kind,
                    file_path=rel_path,
                    module_path=module.rel_path,
                    evidence=detection.evidence,
                    confidence=detection.confidence,
                    source=SourceSpan(rel_path),
                )
                artifacts.append(artifact)
                semantic_facts.append(_artifact_fact(project_id, project_name, artifact))
                if kind in {"mapper_xml", "config_xml", "spring_xml"}:
                    xml_facts.append(XmlSourceFact(file_path=rel_path, parser_status="capability_gate_only"))
                elif kind == "java_mapper":
                    java_facts.append(JavaSourceFact(file_path=rel_path, source_symbol_id=f"file::{rel_path}"))

        if module.java_files:
            mapper_analysis = analyze_mapper_interfaces(
                root=root_abs,
                java_files=module.java_files,
                project_id=project_id,
                project_name=project_name,
            )
            mapper_interfaces.extend(mapper_analysis.interfaces)
            mapper_methods.extend(mapper_analysis.methods)
            mapper_parameters.extend(mapper_analysis.parameters)
            java_properties.extend(mapper_analysis.java_properties)
            diagnostics.extend(mapper_analysis.diagnostics)
            for mapper in mapper_analysis.interfaces:
                semantic_facts.append(_mapper_interface_fact(project_id, project_name, mapper))
            for method in mapper_analysis.methods:
                semantic_facts.append(_mapper_method_fact(project_id, project_name, method))
            for param in mapper_analysis.parameters:
                semantic_facts.append(_mapper_parameter_fact(project_id, project_name, param))
            for prop in mapper_analysis.java_properties:
                semantic_facts.append(_java_property_fact(project_id, project_name, prop))

            annotation_analysis = analyze_annotation_mappers(
                mapper_methods=mapper_analysis.methods,
                project_id=project_id,
            )
            statements.extend(annotation_analysis.statements)
            result_maps.extend(annotation_analysis.result_maps)
            result_mappings.extend(annotation_analysis.result_mappings)
            provider_facts.extend(annotation_analysis.providers)
            diagnostics.extend(annotation_analysis.diagnostics)
            for stmt in annotation_analysis.statements:
                semantic_facts.append(_statement_fact(project_id, project_name, stmt, extraction_method="mybatis_annotation_mapper"))
            for result_map in annotation_analysis.result_maps:
                semantic_facts.append(_result_map_fact(project_id, project_name, result_map, extraction_method="mybatis_annotation_mapper"))
            for mapping in annotation_analysis.result_mappings:
                semantic_facts.append(_result_mapping_fact(project_id, project_name, mapping, extraction_method="mybatis_annotation_mapper"))
            for provider in annotation_analysis.providers:
                semantic_facts.append(_provider_fact(project_id, project_name, provider))
            if annotation_analysis.statements:
                _append_sql_analysis(
                    statements=annotation_analysis.statements,
                    project_id=project_id,
                    project_name=project_name,
                    sql_statement_semantics=sql_statement_semantics,
                    sql_tables=sql_tables,
                    sql_columns=sql_columns,
                    sql_joins=sql_joins,
                    sql_parameters=sql_parameters,
                    semantic_facts=semantic_facts,
                    diagnostics=diagnostics,
                )

        module_xml_files = tuple(module.mapper_xml_files + module.config_xml_files + module.spring_config_files)
        if module_xml_files:
            xml_analysis = analyze_mapper_xml_files(root=root_abs, xml_files=module_xml_files, project_id=project_id)
            xml_documents.extend(xml_analysis.documents)
            statements.extend(xml_analysis.statements)
            sql_fragments.extend(xml_analysis.fragments)
            result_maps.extend(xml_analysis.result_maps)
            result_mappings.extend(xml_analysis.result_mappings)
            includes.extend(xml_analysis.includes)
            dynamic_nodes.extend(xml_analysis.dynamic_nodes)
            config_facts.extend(xml_analysis.config_facts)
            diagnostics.extend(xml_analysis.diagnostics)
            for doc in xml_analysis.documents:
                semantic_facts.append(_xml_document_fact(project_id, project_name, doc))
            for stmt in xml_analysis.statements:
                semantic_facts.append(_statement_fact(project_id, project_name, stmt))
            for fragment in xml_analysis.fragments:
                semantic_facts.append(_fragment_fact(project_id, project_name, fragment))
            for result_map in xml_analysis.result_maps:
                semantic_facts.append(_result_map_fact(project_id, project_name, result_map))
            for mapping in xml_analysis.result_mappings:
                semantic_facts.append(_result_mapping_fact(project_id, project_name, mapping))
            for include in xml_analysis.includes:
                semantic_facts.append(_include_fact(project_id, project_name, include))
            for dynamic in xml_analysis.dynamic_nodes:
                semantic_facts.append(_dynamic_node_fact(project_id, project_name, dynamic))
            for config in xml_analysis.config_facts:
                semantic_facts.append(_config_fact(project_id, project_name, config))

            if xml_analysis.statements:
                _append_sql_analysis(
                    statements=xml_analysis.statements,
                    project_id=project_id,
                    project_name=project_name,
                    sql_statement_semantics=sql_statement_semantics,
                    sql_tables=sql_tables,
                    sql_columns=sql_columns,
                    sql_joins=sql_joins,
                    sql_parameters=sql_parameters,
                    semantic_facts=semantic_facts,
                    diagnostics=diagnostics,
                )

            extension_analysis = analyze_optional_extensions(
                root=root_abs,
                java_files=module.java_files,
                xml_files=module_xml_files,
                config_facts=xml_analysis.config_facts,
                project_id=project_id,
            )
            spring_bridge_facts.extend(extension_analysis.bridge_facts)
            extension_facts.extend(extension_analysis.extension_facts)
            cache_facts.extend(extension_analysis.cache_facts)
            diagnostics.extend(extension_analysis.diagnostics)
            for bridge in extension_analysis.bridge_facts:
                semantic_facts.append(_spring_bridge_fact(project_id, project_name, bridge))
            for extension in extension_analysis.extension_facts:
                semantic_facts.append(_extension_fact(project_id, project_name, extension))
            for cache in extension_analysis.cache_facts:
                semantic_facts.append(_cache_fact(project_id, project_name, cache))

    resolution = resolve_mybatis_relationships(
        project_id=project_id,
        mapper_interfaces=mapper_interfaces,
        mapper_methods=mapper_methods,
        mapper_parameters=mapper_parameters,
        statements=statements,
        sql_tables=sql_tables,
        sql_columns=sql_columns,
        sql_joins=sql_joins,
        sql_parameters=sql_parameters,
        result_maps=result_maps,
        result_mappings=result_mappings,
        java_properties=java_properties,
    )
    diagnostics.extend(resolution.diagnostics)

    dependency_index = _empty_dependency_index(artifacts)
    return MyBatisAnalysisResult(
        project_id=project_id,
        project_name=project_name,
        root=".",
        modules=tuple(modules),
        artifacts=tuple(artifacts),
        parser_capabilities=tuple(parser_capabilities),
        java_facts=tuple(java_facts),
        mapper_interfaces=tuple(mapper_interfaces),
        mapper_methods=tuple(mapper_methods),
        mapper_parameters=tuple(mapper_parameters),
        java_properties=tuple(java_properties),
        xml_documents=tuple(xml_documents),
        statements=tuple(statements),
        sql_fragments=tuple(sql_fragments),
        result_maps=tuple(result_maps),
        result_mappings=tuple(result_mappings),
        includes=tuple(includes),
        dynamic_nodes=tuple(dynamic_nodes),
        config_facts=tuple(config_facts),
        provider_facts=tuple(provider_facts),
        spring_bridge_facts=tuple(spring_bridge_facts),
        extension_facts=tuple(extension_facts),
        cache_facts=tuple(cache_facts),
        sql_statement_semantics=tuple(sql_statement_semantics),
        sql_tables=tuple(sql_tables),
        sql_columns=tuple(sql_columns),
        sql_joins=tuple(sql_joins),
        sql_parameters=tuple(sql_parameters),
        xml_facts=tuple(xml_facts),
        sql_facts=tuple(sql_facts),
        semantic_facts=tuple(semantic_facts),
        relationships=resolution.relationships,
        dependency_index=dependency_index,
        diagnostics=tuple(diagnostics),
    )


def _module_fact(project_id: str, project_name: str, module: MyBatisModule) -> MyBatisFact:
    stable_module = (module.rel_path or ".").replace("/", ".").strip(".") or "root"
    source_file = _first(module.mapper_xml_files, module.config_xml_files, module.java_files, module.build_files) or "."
    return MyBatisFact(
        kind="MyBatisModule",
        stable_id=f"mybatis_module::{project_id}::{stable_module}",
        name=module.rel_path or ".",
        source=SourceSpan(source_file),
        project_id=project_id,
        project_name=project_name,
        confidence=module.confidence,
        properties={
            "module_path": module.rel_path,
            "mapper_xml_files": list(module.mapper_xml_files),
            "config_xml_files": list(module.config_xml_files),
            "java_files": list(module.java_files),
            "build_files": list(module.build_files),
            "spring_config_files": list(module.spring_config_files),
            "evidence": list(module.evidence),
        },
    )


def _artifact_fact(project_id: str, project_name: str, artifact: MyBatisArtifact) -> MyBatisFact:
    digest = hashlib.sha1(f"{artifact.kind}:{safe_rel_path(artifact.file_path)}".encode("utf-8")).hexdigest()[:16]
    return MyBatisFact(
        kind="MyBatisArtifact",
        stable_id=f"mybatis_artifact::{project_id}::{digest}",
        name=os.path.basename(artifact.file_path) or artifact.kind,
        source=artifact.source,
        project_id=project_id,
        project_name=project_name,
        confidence=artifact.confidence,
        resolution_status="resolved" if artifact.evidence else "unresolved",
        properties={
            "artifact_kind": artifact.kind,
            "module_path": artifact.module_path,
            "evidence": list(artifact.evidence),
        },
    )


def _mapper_interface_fact(project_id: str, project_name: str, mapper: MyBatisMapperInterfaceFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisMapper",
        stable_id=mapper.stable_id,
        name=mapper.fqcn,
        source=mapper.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_mapper_interface",
        source_symbol_id=mapper.java_class_symbol_id,
        properties={
            "namespace": mapper.fqcn,
            "interface_fqcn": mapper.fqcn,
            "package_name": mapper.package_name,
            "extended_interfaces": list(mapper.extended_interfaces),
            "type_parameters": list(mapper.type_parameters),
            "modifiers": list(mapper.modifiers),
            "annotations": [item.resolved_name for item in mapper.annotations],
        },
    )


def _mapper_method_fact(project_id: str, project_name: str, method: MyBatisMapperMethodFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisMapperMethod",
        stable_id=method.stable_id,
        name=method.name,
        source=method.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_mapper_interface",
        resolution_status=method.ambiguity_status,
        source_symbol_id=method.java_symbol_id,
        properties={
            "mapper_fqcn": method.mapper_fqcn,
            "signature": method.signature,
            "return_type": method.return_type,
            "parameter_types": list(method.parameter_types),
            "bindable": method.bindable,
            "overload_count": method.overload_count,
            "modifiers": list(method.modifiers),
            "throws": list(method.throws),
            "annotations": [item.resolved_name for item in method.annotations],
            "has_body": method.has_body,
        },
    )


def _mapper_parameter_fact(project_id: str, project_name: str, param: MyBatisMapperParameterFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisParameter",
        stable_id=param.stable_id,
        name=param.name,
        source=param.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_mapper_interface",
        properties={
            "mapper_method_id": param.mapper_method_id,
            "position": param.position,
            "java_type": param.java_type,
            "canonical_type": param.canonical_type,
            "param_alias": param.param_alias,
            "special_role": param.special_role,
            "annotations": [item.resolved_name for item in param.annotations],
        },
    )


def _java_property_fact(project_id: str, project_name: str, prop: MyBatisJavaPropertyFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisJavaProperty",
        stable_id=prop.stable_id,
        name=prop.property_name,
        source=prop.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_java_type_index",
        source_symbol_id=prop.source_symbol_id,
        properties={
            "java_type_fqcn": prop.java_type_fqcn,
            "property_name": prop.property_name,
            "property_type": prop.property_type,
            "source_kind": prop.source_kind,
            "readable": prop.readable,
            "writable": prop.writable,
        },
    )


def _xml_document_fact(project_id: str, project_name: str, doc: MyBatisXmlDocumentFact) -> MyBatisFact:
    digest = hashlib.sha1(doc.file_path.encode("utf-8")).hexdigest()[:16]
    return MyBatisFact(
        kind="MyBatisXmlDocument",
        stable_id=f"mybatis_xml_document::{project_id}::{digest}",
        name=doc.file_path,
        source=doc.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_mapper_xml",
        resolution_status=doc.parser_status,
        properties={
            "document_kind": doc.document_kind,
            "root_tag": doc.root_tag,
            "namespace": doc.namespace,
            "doctype": doc.doctype,
        },
    )


def _statement_fact(
    project_id: str,
    project_name: str,
    stmt: MyBatisStatementFact,
    *,
    extraction_method: str = "mybatis_mapper_xml",
) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisStatement",
        stable_id=stmt.stable_id,
        name=stmt.statement_id,
        source=stmt.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method=extraction_method,
        resolution_status=stmt.parser_status,
        raw_value=stmt.raw_body,
        resolved_value=stmt.expanded_body,
        properties={
            "namespace": stmt.namespace,
            "statement_id": stmt.statement_id,
            "statement_kind": stmt.statement_kind,
            "database_id": stmt.database_id,
            "attributes": dict(stmt.attributes),
        },
    )


def _fragment_fact(project_id: str, project_name: str, fragment: MyBatisSqlFragmentFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisSqlFragment",
        stable_id=fragment.stable_id,
        name=fragment.fragment_id,
        source=fragment.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_mapper_xml",
        raw_value=fragment.raw_body,
        resolved_value=fragment.expanded_body,
        properties={
            "namespace": fragment.namespace,
            "fragment_id": fragment.fragment_id,
            "database_id": fragment.database_id,
            "attributes": dict(fragment.attributes),
        },
    )


def _result_map_fact(
    project_id: str,
    project_name: str,
    result_map: MyBatisResultMapFact,
    *,
    extraction_method: str = "mybatis_mapper_xml",
) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisResultMap",
        stable_id=result_map.stable_id,
        name=result_map.result_map_id,
        source=result_map.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method=extraction_method,
        properties={
            "namespace": result_map.namespace,
            "result_map_id": result_map.result_map_id,
            "java_type": result_map.java_type,
            "extends": result_map.extends,
            "auto_mapping": result_map.auto_mapping,
        },
    )


def _result_mapping_fact(
    project_id: str,
    project_name: str,
    mapping: MyBatisResultMappingFact,
    *,
    extraction_method: str = "mybatis_mapper_xml",
) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisResultMapping",
        stable_id=mapping.stable_id,
        name=mapping.property_name or mapping.column or mapping.mapping_kind,
        source=mapping.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method=extraction_method,
        properties={
            "result_map_id": mapping.result_map_id,
            "mapping_kind": mapping.mapping_kind,
            "property_name": mapping.property_name,
            "column": mapping.column,
            "java_type": mapping.java_type,
            "jdbc_type": mapping.jdbc_type,
            "nested_select": mapping.nested_select,
            "nested_result_map": mapping.nested_result_map,
            "attributes": dict(mapping.attributes),
        },
    )


def _include_fact(project_id: str, project_name: str, include: MyBatisIncludeFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisInclude",
        stable_id=include.stable_id,
        name=include.refid,
        source=include.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_mapper_xml",
        resolution_status=include.resolution_status,
        properties={
            "owner_id": include.owner_id,
            "refid": include.refid,
            "resolved_refid": include.resolved_refid,
            "properties": dict(include.properties),
        },
    )


def _dynamic_node_fact(project_id: str, project_name: str, dynamic: MyBatisDynamicSqlNodeFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisDynamicNode",
        stable_id=dynamic.stable_id,
        name=dynamic.tag,
        source=dynamic.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_dynamic_sql",
        raw_value=dynamic.text,
        properties={
            "owner_id": dynamic.owner_id,
            "tag": dynamic.tag,
            "node_kind": dynamic.node_kind,
            "order": dynamic.order,
            "attributes": dict(dynamic.attributes),
            "test": dynamic.test,
            "branch_role": dynamic.branch_role,
            "referenced_variables": list(dynamic.referenced_variables),
        },
    )


def _config_fact(project_id: str, project_name: str, config: MyBatisConfigFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisConfig",
        stable_id=config.stable_id,
        name=config.file_path,
        source=config.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_config_xml",
        properties={
            "properties": dict(config.properties),
            "settings": dict(config.settings),
            "type_aliases": dict(config.type_aliases),
            "type_handlers": list(config.type_handlers),
            "plugins": list(config.plugins),
            "environments": list(config.environments),
            "database_id_provider": dict(config.database_id_provider),
            "mapper_registrations": list(config.mapper_registrations),
        },
    )


def _provider_fact(project_id: str, project_name: str, provider: MyBatisProviderFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisSqlProvider",
        stable_id=provider.stable_id,
        name=provider.provider_type or provider.statement_id,
        source=provider.source,
        project_id=project_id,
        project_name=project_name,
        confidence=0.55,
        extraction_method="mybatis_annotation_mapper",
        resolution_status=provider.resolution_status,
        raw_value=provider.raw_arguments,
        properties={
            "mapper_method_id": provider.mapper_method_id,
            "namespace": provider.namespace,
            "statement_id": provider.statement_id,
            "provider_kind": provider.provider_kind,
            "provider_type": provider.provider_type,
            "provider_method": provider.provider_method,
            "attributes": dict(provider.attributes),
        },
    )


def _spring_bridge_fact(project_id: str, project_name: str, bridge: MyBatisSpringBridgeFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisSpringBridge",
        stable_id=bridge.stable_id,
        name=bridge.name or bridge.bridge_kind,
        source=bridge.source,
        project_id=project_id,
        project_name=project_name,
        confidence=0.7,
        extraction_method="mybatis_optional_bridge",
        resolution_status=bridge.resolution_status,
        resolved_value=bridge.target,
        properties={
            "bridge_kind": bridge.bridge_kind,
            "target": bridge.target,
            "attributes": dict(bridge.attributes),
        },
    )


def _extension_fact(project_id: str, project_name: str, extension: MyBatisExtensionFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisExtension",
        stable_id=extension.stable_id,
        name=extension.name or extension.extension_kind,
        source=extension.source,
        project_id=project_id,
        project_name=project_name,
        confidence=0.75,
        extraction_method="mybatis_optional_extension",
        resolution_status=extension.resolution_status,
        resolved_value=extension.java_type,
        properties={
            "extension_kind": extension.extension_kind,
            "java_type": extension.java_type,
            "attributes": dict(extension.attributes),
        },
    )


def _cache_fact(project_id: str, project_name: str, cache: MyBatisCacheFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisCache",
        stable_id=cache.stable_id,
        name=cache.namespace,
        source=cache.source,
        project_id=project_id,
        project_name=project_name,
        confidence=0.8,
        extraction_method="mybatis_optional_extension",
        resolution_status=cache.resolution_status,
        resolved_value=cache.target_namespace,
        properties={
            "namespace": cache.namespace,
            "cache_kind": cache.cache_kind,
            "target_namespace": cache.target_namespace,
            "attributes": dict(cache.attributes),
        },
    )


def _sql_statement_fact(project_id: str, project_name: str, stmt: MyBatisSqlStatementSemanticFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisSqlStatement",
        stable_id=stmt.stable_id,
        name=stmt.crud or stmt.xml_statement_kind or "sql",
        source=stmt.source,
        project_id=project_id,
        project_name=project_name,
        confidence=stmt.confidence,
        extraction_method="mybatis_sql_semantic",
        resolution_status=stmt.parser_status,
        raw_value=stmt.raw_sql,
        resolved_value=stmt.normalized_sql,
        properties={
            "owner_statement_id": stmt.owner_statement_id,
            "crud": stmt.crud,
            "xml_statement_kind": stmt.xml_statement_kind,
            "database_id": stmt.database_id,
            "parser_error_count": stmt.parser_error_count,
            "has_textual_substitution": stmt.has_textual_substitution,
        },
    )


def _sql_table_fact(project_id: str, project_name: str, table: MyBatisSqlTableFact) -> MyBatisFact:
    return MyBatisFact(
        kind="DatabaseTable",
        stable_id=table.stable_id,
        name=table.raw_name,
        source=table.source,
        project_id=project_id,
        project_name=project_name,
        confidence=0.7 if table.is_dynamic else 1.0,
        extraction_method="mybatis_sql_semantic",
        resolution_status=table.resolution_status,
        properties={
            "sql_statement_id": table.sql_statement_id,
            "raw_name": table.raw_name,
            "normalized_name": table.normalized_name,
            "role": table.role,
            "alias": table.alias,
            "catalog": table.catalog,
            "schema": table.schema,
            "is_cte": table.is_cte,
            "is_dynamic": table.is_dynamic,
            "dynamic_node_ids": list(table.dynamic_node_ids),
            "branch_roles": list(table.branch_roles),
        },
    )


def _sql_column_fact(project_id: str, project_name: str, column: MyBatisSqlColumnFact) -> MyBatisFact:
    return MyBatisFact(
        kind="DatabaseColumn",
        stable_id=column.stable_id,
        name=column.raw_name,
        source=column.source,
        project_id=project_id,
        project_name=project_name,
        confidence=0.75,
        extraction_method="mybatis_sql_semantic",
        resolution_status=column.resolution_status,
        raw_value=column.expression,
        properties={
            "sql_statement_id": column.sql_statement_id,
            "raw_name": column.raw_name,
            "normalized_name": column.normalized_name,
            "role": column.role,
            "qualifier": column.qualifier,
            "table_ref": column.table_ref,
            "dynamic_node_ids": list(column.dynamic_node_ids),
            "branch_roles": list(column.branch_roles),
        },
    )


def _sql_join_fact(project_id: str, project_name: str, join: MyBatisSqlJoinFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisSqlJoin",
        stable_id=join.stable_id,
        name=join.right_table,
        source=join.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_sql_semantic",
        resolution_status=join.resolution_status,
        raw_value=join.condition,
        properties={
            "sql_statement_id": join.sql_statement_id,
            "join_type": join.join_type,
            "right_table": join.right_table,
            "right_alias": join.right_alias,
            "dynamic_node_ids": list(join.dynamic_node_ids),
            "branch_roles": list(join.branch_roles),
        },
    )


def _sql_parameter_fact(project_id: str, project_name: str, param: MyBatisSqlParameterFact) -> MyBatisFact:
    return MyBatisFact(
        kind="MyBatisSqlParameter",
        stable_id=param.stable_id,
        name=param.name,
        source=param.source,
        project_id=project_id,
        project_name=project_name,
        confidence=1.0,
        extraction_method="mybatis_sql_semantic",
        resolution_status=param.parameter_kind,
        properties={
            "sql_statement_id": param.sql_statement_id,
            "token": param.token,
            "parameter_kind": param.parameter_kind,
            "options": dict(param.options),
            "position": param.position,
            "dynamic_node_ids": list(param.dynamic_node_ids),
            "branch_roles": list(param.branch_roles),
        },
    )


def _append_sql_analysis(
    *,
    statements: Sequence[MyBatisStatementFact],
    project_id: str,
    project_name: str,
    sql_statement_semantics: List[MyBatisSqlStatementSemanticFact],
    sql_tables: List[MyBatisSqlTableFact],
    sql_columns: List[MyBatisSqlColumnFact],
    sql_joins: List[MyBatisSqlJoinFact],
    sql_parameters: List[MyBatisSqlParameterFact],
    semantic_facts: List[MyBatisFact],
    diagnostics: List[Diagnostic],
) -> None:
    sql_analysis = analyze_sql_semantics(statements=statements, project_id=project_id)
    sql_statement_semantics.extend(sql_analysis.statements)
    sql_tables.extend(sql_analysis.tables)
    sql_columns.extend(sql_analysis.columns)
    sql_joins.extend(sql_analysis.joins)
    sql_parameters.extend(sql_analysis.parameters)
    diagnostics.extend(sql_analysis.diagnostics)
    for sql_stmt in sql_analysis.statements:
        semantic_facts.append(_sql_statement_fact(project_id, project_name, sql_stmt))
    for table in sql_analysis.tables:
        semantic_facts.append(_sql_table_fact(project_id, project_name, table))
    for column in sql_analysis.columns:
        semantic_facts.append(_sql_column_fact(project_id, project_name, column))
    for join in sql_analysis.joins:
        semantic_facts.append(_sql_join_fact(project_id, project_name, join))
    for param in sql_analysis.parameters:
        semantic_facts.append(_sql_parameter_fact(project_id, project_name, param))


def _empty_dependency_index(artifacts: Sequence[MyBatisArtifact]) -> MyBatisDependencyIndex:
    files: Dict[str, Tuple[str, ...]] = {artifact.file_path: () for artifact in artifacts}
    return MyBatisDependencyIndex(files=files)


def _first(*groups: Sequence[str]) -> str:
    for group in groups:
        if group:
            return group[0]
    return ""
