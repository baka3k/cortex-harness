//! Port `tools/struts/struts_xml_parser.py`.

use super::models::{
    ActionConfig, Diagnostic, ExceptionMappingConfig, InterceptorConfig, InterceptorRef, InterceptorStackConfig,
    PackageConfig, Params, ResultConfig, ResultTypeConfig, SourceSpan, StrutsXmlData,
};
use super::xml_utils::{children, child_text, local_name, params, parse_xml, text};

fn interceptor_ref(element: &XmlElementAlias) -> InterceptorRef {
    InterceptorRef {
        name: element.attr("name").unwrap_or("").trim().to_string(),
        params: params(element),
    }
}

// Alias để giữ tên hàm ngắn như Python (element = XmlElement).
type XmlElementAlias = super::xml_dom::XmlElement;

fn result(element: &XmlElementAlias) -> ResultConfig {
    let result_params = params(element);
    let location = result_params
        .get("location")
        .cloned()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| text(Some(element)));
    ResultConfig {
        name: element.attr("name").unwrap_or("success").trim().to_string(),
        type_name: element.attr("type").unwrap_or("").trim().to_string(),
        location,
        params: result_params,
    }
}

fn exception_mapping(element: &XmlElementAlias) -> ExceptionMappingConfig {
    ExceptionMappingConfig {
        exception: element.attr("exception").unwrap_or("").trim().to_string(),
        result: element.attr("result").unwrap_or("error").trim().to_string(),
    }
}

fn parse_package(element: &XmlElementAlias, source: &SourceSpan) -> PackageConfig {
    let mut interceptors: Vec<InterceptorConfig> = Vec::new();
    let mut stacks: Vec<InterceptorStackConfig> = Vec::new();
    let mut result_types: Vec<ResultTypeConfig> = Vec::new();
    let mut global_results: Vec<ResultConfig> = Vec::new();
    let mut exception_mappings: Vec<ExceptionMappingConfig> = Vec::new();
    let mut actions: Vec<ActionConfig> = Vec::new();
    let mut default_interceptor_ref = String::new();

    for child in element.elements() {
        let name = local_name(&child.tag);
        if name == "interceptors" {
            for item in child.elements() {
                let item_name = local_name(&item.tag);
                if item_name == "interceptor" {
                    interceptors.push(InterceptorConfig {
                        name: item.attr("name").unwrap_or("").trim().to_string(),
                        class_name: item.attr("class").unwrap_or("").trim().to_string(),
                        params: params(item),
                    });
                } else if item_name == "interceptor-stack" {
                    stacks.push(InterceptorStackConfig {
                        name: item.attr("name").unwrap_or("").trim().to_string(),
                        refs: children(item, "interceptor-ref")
                            .into_iter()
                            .map(interceptor_ref)
                            .collect(),
                    });
                }
            }
        } else if name == "default-interceptor-ref" {
            default_interceptor_ref = child.attr("name").unwrap_or("").trim().to_string();
        } else if name == "result-types" {
            for item in children(child, "result-type") {
                result_types.push(ResultTypeConfig {
                    name: item.attr("name").unwrap_or("").trim().to_string(),
                    class_name: item.attr("class").unwrap_or("").trim().to_string(),
                    default: item.attr("default").unwrap_or("false").to_lowercase() == "true",
                    params: params(item),
                });
            }
        } else if name == "global-results" {
            global_results.extend(children(child, "result").into_iter().map(result));
        } else if name == "global-exception-mappings" {
            exception_mappings.extend(children(child, "exception-mapping").into_iter().map(exception_mapping));
        } else if name == "action" {
            actions.push(ActionConfig {
                name: child.attr("name").unwrap_or("").trim().to_string(),
                class_name: child.attr("class").unwrap_or("").trim().to_string(),
                method: child.attr("method").unwrap_or("execute").trim().to_string(),
                interceptor_refs: children(child, "interceptor-ref").into_iter().map(interceptor_ref).collect(),
                results: children(child, "result").into_iter().map(result).collect(),
                exception_mappings: children(child, "exception-mapping")
                    .into_iter()
                    .map(exception_mapping)
                    .collect(),
                params: params(child),
                source: source.clone(),
            });
        }
    }

    let extends: Vec<String> = element
        .attr("extends")
        .unwrap_or("")
        .split(',')
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .collect();
    PackageConfig {
        name: element.attr("name").unwrap_or("").trim().to_string(),
        namespace: element.attr("namespace").unwrap_or("").trim().to_string(),
        extends,
        default_interceptor_ref,
        default_result_type: element.attr("default-result-type").unwrap_or("").trim().to_string(),
        interceptors,
        interceptor_stacks: stacks,
        result_types,
        global_results,
        exception_mappings,
        actions,
        source: source.clone(),
    }
}

pub fn parse_struts_xml_file(root: &str, file_path: &str) -> StrutsXmlData {
    let (document, source, diagnostics) = parse_xml(root, file_path, "struts.config");
    let Some(document) = document else {
        return StrutsXmlData {
            diagnostics,
            ..Default::default()
        };
    };
    if local_name(&document.tag) != "struts" {
        let mut diagnostics = diagnostics;
        diagnostics.push(Diagnostic::new(
            "struts.config.invalid_root",
            "Expected a <struts> document",
            "error",
            &source.file_path,
        ));
        return StrutsXmlData {
            diagnostics,
            ..Default::default()
        };
    }

    let mut constants: Params = Params::new();
    let mut includes: Vec<String> = Vec::new();
    let mut packages: Vec<PackageConfig> = Vec::new();
    for element in document.elements() {
        let name = local_name(&element.tag);
        if name == "constant" {
            let key = element.attr("name").unwrap_or("").trim().to_string();
            if !key.is_empty() {
                let value = element
                    .attr("value")
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| text(Some(element)));
                constants.insert(key, value.trim().to_string());
            }
        } else if name == "include" {
            let include = element.attr("file").unwrap_or("").trim().to_string();
            if !include.is_empty() {
                includes.push(include);
            }
        } else if name == "package" {
            packages.push(parse_package(element, &source));
        }
    }

    StrutsXmlData {
        packages,
        constants,
        includes,
        diagnostics,
    }
}

/// `child_text` re-export giữ import gọn (không dùng trực tiếp).
#[allow(dead_code)]
fn unused_child_text(element: &XmlElementAlias, name: &str) -> String {
    child_text(element, name, "")
}
