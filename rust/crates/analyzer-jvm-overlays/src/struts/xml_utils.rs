//! Port `tools/struts/xml_utils.py`.

use std::path::{Path, PathBuf};

use super::models::{Diagnostic, Params, SourceSpan};
use super::xml_dom::{parse, XmlElement};

/// `parse_xml` — resolve file dưới project root + parse; trả (root element,
/// SourceSpan(relative), diagnostics).
pub fn parse_xml(root: &str, file_path: &str, code_prefix: &str) -> (Option<XmlElement>, SourceSpan, Vec<Diagnostic>) {
    let project_root = crate::pyutil::realpath(Path::new(root));
    let candidate = PathBuf::from(file_path);
    let absolute = if candidate.is_absolute() {
        crate::pyutil::realpath(&candidate)
    } else {
        crate::pyutil::realpath(&project_root.join(&candidate))
    };
    let relative = match absolute.strip_prefix(&project_root) {
        Ok(rel) => crate::pyutil::to_posix(rel),
        Err(_) => {
            return (
                None,
                SourceSpan::from_path(file_path),
                vec![Diagnostic::new(
                    &format!("{code_prefix}.path.outside_root"),
                    "XML path is outside the project root",
                    "error",
                    file_path,
                )],
            );
        }
    };
    match std::fs::read(&absolute) {
        Err(error) => (
            None,
            SourceSpan::from_path(&relative),
            vec![Diagnostic::new(
                &format!("{code_prefix}.xml.parse_error"),
                &error.to_string(),
                "error",
                &relative,
            )],
        ),
        Ok(bytes) => match parse(&bytes) {
            Ok(document) => (Some(document), SourceSpan::from_path(&relative), Vec::new()),
            Err(message) => (
                None,
                SourceSpan::from_path(&relative),
                vec![Diagnostic::new(
                    &format!("{code_prefix}.xml.parse_error"),
                    &message,
                    "error",
                    &relative,
                )],
            ),
        },
    }
}

/// `local_name` — strip Clark `{uri}`.
pub fn local_name(tag: &str) -> String {
    tag.rsplit('}').next().unwrap_or(tag).to_string()
}

/// `children(element, name)` — direct children với local name khớp.
pub fn children<'a>(element: &'a XmlElement, name: &str) -> Vec<&'a XmlElement> {
    element
        .elements()
        .filter(|child| local_name(&child.tag) == name)
        .collect()
}

/// `first_child`.
pub fn first_child<'a>(element: &'a XmlElement, name: &str) -> Option<&'a XmlElement> {
    children(element, name).into_iter().next()
}

/// `child_text`.
pub fn child_text(element: &XmlElement, name: &str, default: &str) -> String {
    match first_child(element, name) {
        Some(child) => text(Some(child)),
        None => default.to_string(),
    }
}

/// `text(element)` — itertext().strip().
pub fn text(element: Option<&XmlElement>) -> String {
    match element {
        None => String::new(),
        Some(element) => element.itertext().trim().to_string(),
    }
}

/// `params(element)` — `<param name="...">text</param>` children.
pub fn params(element: &XmlElement) -> Params {
    let mut values = Params::new();
    for item in children(element, "param") {
        let name = item.attr("name").unwrap_or("").trim().to_string();
        if !name.is_empty() {
            values.insert(name, text(Some(item)));
        }
    }
    values
}

/// `normalized_path` (không dùng trong pipeline hiện tại).
#[allow(dead_code)]
pub fn normalized_path(path: &str) -> String {
    path.to_string()
}
