//! Port `tools/struts/web_xml_parser.py` (bản struts — filter declarations).

use std::collections::BTreeMap;

use super::models::{WebFilterConfig, WebXmlData};
use super::xml_utils::{child_text, children, parse_xml, text};

pub fn parse_web_xml_file(root: &str, file_path: &str) -> WebXmlData {
    let (document, source, diagnostics) = parse_xml(root, file_path, "struts.web_xml");
    let Some(document) = document else {
        return WebXmlData {
            diagnostics,
            ..Default::default()
        };
    };

    // declarations: name -> (class_name, init_params); giữ insertion order như
    // Python dict (sau đó sorted khi build filters).
    type FilterDecl = (String, (String, BTreeMap<String, String>));
    let mut declarations: Vec<FilterDecl> = Vec::new();
    for element in children(&document, "filter") {
        let name = child_text(element, "filter-name", "");
        let class_name = child_text(element, "filter-class", "");
        let mut init_params: BTreeMap<String, String> = BTreeMap::new();
        for item in children(element, "init-param") {
            let param_name = child_text(item, "param-name", "");
            if !param_name.is_empty() {
                init_params.insert(param_name, child_text(item, "param-value", ""));
            }
        }
        if !name.is_empty() {
            declarations.retain(|(key, _)| key != &name);
            declarations.push((name, (class_name, init_params)));
        }
    }

    let mut mappings: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut mapping_order: Vec<String> = Vec::new();
    for element in children(&document, "filter-mapping") {
        let name = child_text(element, "filter-name", "");
        if name.is_empty() {
            continue;
        }
        let values: Vec<String> = children(element, "url-pattern")
            .into_iter()
            .map(|item| text(Some(item)))
            .filter(|value| !value.is_empty())
            .collect();
        if !mappings.contains_key(&name) {
            mapping_order.push(name.clone());
        }
        mappings.entry(name).or_default().extend(values);
    }

    let mut names: Vec<String> = declarations.iter().map(|(name, _)| name.clone()).collect();
    names.sort();
    let lookup: BTreeMap<String, &(String, BTreeMap<String, String>)> =
        declarations.iter().map(|(name, value)| (name.clone(), value)).collect();
    let filters = names
        .into_iter()
        .map(|name| {
            let (class_name, init_params) = lookup[&name].clone();
            let mut url_patterns: Vec<String> = Vec::new();
            for key in mapping_order.iter().filter(|key| **key == name) {
                for value in &mappings[key] {
                    if !url_patterns.contains(value) {
                        url_patterns.push(value.clone());
                    }
                }
            }
            WebFilterConfig {
                name,
                class_name,
                url_patterns,
                init_params,
                source: source.clone(),
            }
        })
        .collect();
    WebXmlData {
        filters,
        diagnostics,
    }
}
