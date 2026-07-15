from __future__ import annotations

from typing import Dict, List

from tools.struts.models import (
    ActionConfig,
    Diagnostic,
    ExceptionMappingConfig,
    InterceptorConfig,
    InterceptorRef,
    InterceptorStackConfig,
    PackageConfig,
    ResultConfig,
    ResultTypeConfig,
    SourceSpan,
    StrutsXmlData,
)
from tools.struts.xml_utils import children, local_name, params, parse_xml, text


def _interceptor_ref(element) -> InterceptorRef:
    return InterceptorRef(name=(element.get("name") or "").strip(), params=params(element))


def _result(element) -> ResultConfig:
    result_params = params(element)
    location = result_params.get("location", "") or text(element)
    return ResultConfig(
        name=(element.get("name") or "success").strip(),
        type_name=(element.get("type") or "").strip(),
        location=location,
        params=result_params,
    )


def _exception_mapping(element) -> ExceptionMappingConfig:
    return ExceptionMappingConfig(
        exception=(element.get("exception") or "").strip(),
        result=(element.get("result") or "error").strip(),
    )


def _parse_package(element, source: SourceSpan) -> PackageConfig:
    interceptors: List[InterceptorConfig] = []
    stacks: List[InterceptorStackConfig] = []
    result_types: List[ResultTypeConfig] = []
    global_results: List[ResultConfig] = []
    exception_mappings: List[ExceptionMappingConfig] = []
    actions: List[ActionConfig] = []
    default_interceptor_ref = ""

    for child in element:
        name = local_name(child.tag)
        if name == "interceptors":
            for item in child:
                item_name = local_name(item.tag)
                if item_name == "interceptor":
                    interceptors.append(
                        InterceptorConfig(
                            name=(item.get("name") or "").strip(),
                            class_name=(item.get("class") or "").strip(),
                            params=params(item),
                        )
                    )
                elif item_name == "interceptor-stack":
                    stacks.append(
                        InterceptorStackConfig(
                            name=(item.get("name") or "").strip(),
                            refs=tuple(_interceptor_ref(ref) for ref in children(item, "interceptor-ref")),
                        )
                    )
        elif name == "default-interceptor-ref":
            default_interceptor_ref = (child.get("name") or "").strip()
        elif name == "result-types":
            for item in children(child, "result-type"):
                result_types.append(
                    ResultTypeConfig(
                        name=(item.get("name") or "").strip(),
                        class_name=(item.get("class") or "").strip(),
                        default=(item.get("default") or "false").lower() == "true",
                        params=params(item),
                    )
                )
        elif name == "global-results":
            global_results.extend(_result(item) for item in children(child, "result"))
        elif name == "global-exception-mappings":
            exception_mappings.extend(_exception_mapping(item) for item in children(child, "exception-mapping"))
        elif name == "action":
            actions.append(
                ActionConfig(
                    name=(child.get("name") or "").strip(),
                    class_name=(child.get("class") or "").strip(),
                    method=(child.get("method") or "execute").strip(),
                    interceptor_refs=tuple(_interceptor_ref(item) for item in children(child, "interceptor-ref")),
                    results=tuple(_result(item) for item in children(child, "result")),
                    exception_mappings=tuple(
                        _exception_mapping(item) for item in children(child, "exception-mapping")
                    ),
                    params=params(child),
                    source=source,
                )
            )

    extends = tuple(value.strip() for value in (element.get("extends") or "").split(",") if value.strip())
    return PackageConfig(
        name=(element.get("name") or "").strip(),
        namespace=(element.get("namespace") or "").strip(),
        extends=extends,
        default_interceptor_ref=default_interceptor_ref,
        default_result_type=(element.get("default-result-type") or "").strip(),
        interceptors=tuple(interceptors),
        interceptor_stacks=tuple(stacks),
        result_types=tuple(result_types),
        global_results=tuple(global_results),
        exception_mappings=tuple(exception_mappings),
        actions=tuple(actions),
        source=source,
    )


def parse_struts_xml_file(root: str, file_path: str) -> StrutsXmlData:
    document, source, diagnostics = parse_xml(root, file_path, "struts.config")
    if document is None:
        return StrutsXmlData(diagnostics=diagnostics)
    if local_name(document.tag) != "struts":
        return StrutsXmlData(
            diagnostics=diagnostics
            + (Diagnostic("struts.config.invalid_root", "Expected a <struts> document", "error", source.file_path),)
        )

    constants: Dict[str, str] = {}
    includes: List[str] = []
    packages: List[PackageConfig] = []
    for element in document:
        name = local_name(element.tag)
        if name == "constant":
            key = (element.get("name") or "").strip()
            if key:
                constants[key] = (element.get("value") or text(element)).strip()
        elif name == "include":
            include = (element.get("file") or "").strip()
            if include:
                includes.append(include)
        elif name == "package":
            packages.append(_parse_package(element, source))

    return StrutsXmlData(
        packages=tuple(packages),
        constants=constants,
        includes=tuple(includes),
        diagnostics=diagnostics,
    )


__all__ = ["parse_struts_xml_file"]
