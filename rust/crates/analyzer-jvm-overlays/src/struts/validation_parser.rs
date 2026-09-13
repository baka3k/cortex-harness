//! Port `tools/struts/validation_parser.py`.

use super::models::{ValidationData, ValidationRule};
use super::xml_utils::{child_text, children, params, parse_xml, text};

/// `_target_from_name` — tên file `Xyz-validation.xml` → (target, method).
fn target_from_name(file_path: &str) -> (String, String) {
    let stem = crate::pyutil::basename(file_path);
    let prefix = if let Some(stripped) = stem.strip_suffix("-validation.xml") {
        stripped.to_string()
    } else {
        stem.trim_end_matches(".xml").to_string()
    };
    // Python: PurePath(file_path).stem cho fallback — bỏ extension cuối.
    match prefix.rfind('-') {
        Some(index) if prefix.contains('-') => (prefix[..index].to_string(), prefix[index + 1..].to_string()),
        _ => (prefix.clone(), String::new()),
    }
}

pub fn parse_validation_xml_file(root: &str, file_path: &str) -> ValidationData {
    let (document, source, diagnostics) = parse_xml(root, file_path, "struts.validation");
    let Some(document) = document else {
        return ValidationData {
            diagnostics,
            ..Default::default()
        };
    };

    let (target, method) = target_from_name(&source.file_path);
    let mut rules: Vec<ValidationRule> = Vec::new();
    for element in document.elements() {
        let tag = element.tag.rsplit('}').next().unwrap_or(&element.tag).to_string();
        let (field_name, validators): (String, Vec<&super::xml_dom::XmlElement>) = if tag == "field" {
            let name = element.attr("name").unwrap_or("").trim().to_string();
            (name, children(element, "field-validator"))
        } else if tag == "validator" {
            (String::new(), vec![element])
        } else {
            continue;
        };
        for validator in validators {
            let message_element = children(validator, "message").into_iter().next();
            rules.push(ValidationRule {
                target: target.clone(),
                method: method.clone(),
                validator_type: validator.attr("type").unwrap_or("").trim().to_string(),
                field_name: field_name.clone(),
                message: child_text(validator, "message", ""),
                message_key: message_element
                    .and_then(|element| element.attr("key"))
                    .unwrap_or("")
                    .trim()
                    .to_string(),
                params: params(validator),
                source: source.clone(),
            });
        }
    }
    ValidationData {
        rules,
        diagnostics,
    }
}

/// `text` re-export (unused).
#[allow(dead_code)]
fn unused(element: Option<&super::xml_dom::XmlElement>) -> String {
    text(element)
}
