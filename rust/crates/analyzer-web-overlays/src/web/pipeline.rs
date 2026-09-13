//! Port `tools/web_framework/pipeline.py` — regex scan endpoints fastapi /
//! django / express / laravel + symbol index + resolution.

use std::collections::BTreeMap;
use std::path::Path;

use fancy_regex::Regex;

use crate::pyutil::{decode_utf8_replace, walk_sorted};
use crate::web::models::{dedupe_and_sort, EndpointFact, WebAnalysisResult};

const SKIP_DIRS: [&str; 11] = [
    ".git", ".cache", ".venv", "venv", "node_modules", "vendor", "dist", "build",
    "__pycache__", "bin", "obj",
];

pub struct WebRegexes {
    fastapi: Regex,
    django: Regex,
    express: Regex,
    laravel: Regex,
    py_def: Regex,
    py_class: Regex,
    js_function: Regex,
    js_arrow: Regex,
    php_class: Regex,
    php_function: Regex,
}

fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).expect("static web overlay regex phải compile được")
}

impl WebRegexes {
    pub fn new() -> Self {
        Self {
            // _FASTAPI_RE — re.IGNORECASE | re.DOTALL
            fastapi: compile(concat!(
                r"(?is)@(?:\w+\.)?(?:app|router|api_router|blueprint)\.(?P<method>get|post|put|delete|patch|head|options|route|websocket)",
                r"\s*\(\s*[rRuUfF]*['\x22](?P<path>[^'\x22]+)['\x22][^)]*\)\s*",
                r"(?:async\s+)?def\s+(?P<handler>[A-Za-z_]\w*)\s*\(",
            )),
            // _DJANGO_RE — re.MULTILINE
            django: compile(concat!(
                r"(?m)\b(?:path|re_path)\s*\(\s*[rRuU]*['\x22](?P<path>[^'\x22]+)['\x22]\s*,\s*",
                r"(?P<handler>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\.as_view\s*\(\s*\))?",
            )),
            // _EXPRESS_RE — re.IGNORECASE
            express: compile(concat!(
                r"(?i)\b(?:app|router|server|api)\.(?P<method>get|post|put|delete|patch|head|options|all|use)",
                r"\s*\(\s*['\x22](?P<path>[^'\x22]+)['\x22]\s*,\s*(?P<handler>[A-Za-z_$][\w$]*)",
            )),
            // _LARAVEL_RE — re.IGNORECASE
            laravel: compile(concat!(
                r"(?i)\bRoute::(?P<method>get|post|put|delete|patch|options|any|match)\s*\(\s*",
                r"['\x22](?P<path>[^'\x22]+)['\x22]\s*,\s*\[\s*(?P<scope>[A-Za-z_\\][\w\\]*)::class",
                r"\s*,\s*['\x22](?P<handler>[A-Za-z_]\w*)['\x22]\s*\]",
            )),
            // symbol index — python
            py_def: compile(r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),
            py_class: compile(r"(?m)^\s*class\s+([A-Za-z_]\w*)\b"),
            // symbol index — js
            js_function: compile(
                r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
            ),
            js_arrow: compile(
                r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
            ),
            // symbol index — php
            php_class: compile(r"\bclass\s+([A-Za-z_]\w*)"),
            php_function: compile(r"\bfunction\s+([A-Za-z_]\w*)\s*\("),
        }
    }
}

impl Default for WebRegexes {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone)]
struct Symbol {
    scope: String,
    file_path: String,
    label: &'static str,
}

/// `pathlib.Path.suffix.lower()`.
fn suffix_lower(path: &Path) -> String {
    match path.extension() {
        Some(extension) => extension.to_string_lossy().to_lowercase(),
        None => String::new(),
    }
}

/// `_source_files` — rglob, bỏ phần tử skip trong relative parts, suffix
/// thuộc {.py, .js, .jsx, .php}. Walk sorted để deterministic (Python rglob
/// theo OS order nhưng mọi consumer cuối đều sort/stable).
fn source_files(root: &Path) -> Vec<(std::path::PathBuf, String)> {
    walk_sorted(root)
        .into_iter()
        .filter(|(path, rel)| {
            if rel.split('/').any(|part| SKIP_DIRS.contains(&part)) {
                return false;
            }
            matches!(suffix_lower(path).as_str(), "py" | "js" | "jsx" | "php")
        })
        .collect()
}

/// `_line` — `text.count("\n", 0, offset) + 1`.
fn line_at(text: &str, offset: usize) -> i64 {
    text.as_bytes()[..offset]
        .iter()
        .filter(|byte| **byte == b'\n')
        .count() as i64
        + 1
}

/// `_normalize_path`.
fn normalize_path(value: &str) -> String {
    let mut path = value.trim().to_string();
    if !path.starts_with('/') {
        path = format!("/{path}");
    }
    let mut normalized = String::with_capacity(path.len());
    let mut previous_slash = false;
    for character in path.chars() {
        if character == '/' {
            if previous_slash {
                continue;
            }
            previous_slash = true;
        } else {
            previous_slash = false;
        }
        normalized.push(character);
    }
    normalized
}

/// `_symbol_index`.
fn symbol_index(
    files: &[(std::path::PathBuf, String)],
    regexes: &WebRegexes,
) -> BTreeMap<String, Vec<Symbol>> {
    let mut index: BTreeMap<String, Vec<Symbol>> = BTreeMap::new();
    for (path, rel) in files {
        let bytes = match std::fs::read(path) {
            Ok(bytes) => bytes,
            Err(_) => continue, // read_text failed — Python ném lỗi, caller crash; không xảy ra trên corpus parity
        };
        let text = decode_utf8_replace(&bytes);
        let suffix = suffix_lower(path);
        let mut push = |name: &str, scope: &str, label: &'static str| {
            index.entry(name.to_string()).or_default().push(Symbol {
                scope: scope.to_string(),
                file_path: rel.clone(),
                label,
            });
        };
        if suffix == "py" {
            for capture in regexes.py_def.captures_iter(&text).flatten() {
                push(&capture[1], "", "Function");
            }
            for capture in regexes.py_class.captures_iter(&text).flatten() {
                push(&capture[1], "", "Class");
            }
        } else if suffix == "js" || suffix == "jsx" {
            for capture in regexes.js_function.captures_iter(&text).flatten() {
                push(&capture[1], "", "Function");
            }
            for capture in regexes.js_arrow.captures_iter(&text).flatten() {
                push(&capture[1], "", "Function");
            }
        } else if suffix == "php" {
            let scope = regexes
                .php_class
                .captures(&text)
                .ok()
                .flatten()
                .map(|capture| capture[1].to_string())
                .unwrap_or_default();
            for capture in regexes.php_function.captures_iter(&text).flatten() {
                push(&capture[1], &scope, "Function");
            }
        }
    }
    index
}

/// `_resolve_handler` — trả (handler_file, resolved_scope, label, status).
fn resolve_handler(
    symbols: &BTreeMap<String, Vec<Symbol>>,
    name: &str,
    scope: &str,
) -> (String, String, String, String) {
    let mut candidates: Vec<Symbol> = symbols.get(name).cloned().unwrap_or_default();
    if !scope.is_empty() {
        let tail = scope.rsplit('\\').next().unwrap_or("").to_string();
        let scoped: Vec<Symbol> = candidates
            .iter()
            .filter(|item| item.scope == tail)
            .cloned()
            .collect();
        if !scoped.is_empty() {
            candidates = scoped;
        }
    }
    if candidates.len() == 1 {
        let item = &candidates[0];
        return (
            item.file_path.clone(),
            item.scope.clone(),
            item.label.to_string(),
            "resolved".into(),
        );
    }
    let scope_tail = if scope.is_empty() {
        String::new()
    } else {
        scope.rsplit('\\').next().unwrap_or("").to_string()
    };
    let status = if candidates.is_empty() { "unresolved" } else { "ambiguous" };
    (String::new(), scope_tail, "Function".into(), status.into())
}

/// `_endpoint`.
#[allow(clippy::too_many_arguments)]
fn endpoint(
    project_id: &str,
    framework: &str,
    method: &str,
    route: &str,
    rel: &str,
    line: i64,
    handler: &str,
    symbols: &BTreeMap<String, Vec<Symbol>>,
    scope: &str,
) -> EndpointFact {
    let (handler_file, resolved_scope, label, status) =
        resolve_handler(symbols, handler, scope);
    let normalized_route = normalize_path(route);
    let normalized_method = if matches!(
        method.to_lowercase().as_str(),
        "route" | "use" | "all" | "any" | "match"
    ) {
        "ALL".to_string()
    } else {
        method.to_uppercase()
    };
    EndpointFact {
        endpoint_id: crate::web::models::stable_id(&[
            project_id.to_string(),
            framework.to_string(),
            normalized_method.clone(),
            normalized_route.clone(),
            rel.to_string(),
            line.to_string(),
        ]),
        project_id: project_id.to_string(),
        framework: framework.to_string(),
        http_method: normalized_method,
        path: normalized_route,
        file_path: rel.to_string(),
        start_line: line,
        handler_name: handler.to_string(),
        handler_scope: resolved_scope,
        handler_file,
        handler_label: label,
        resolution_status: status.clone(),
        confidence: if status == "resolved" { 1.0 } else { 0.6 },
    }
}

/// `analyze_project` — frameworks là list đã lowercase ("fastapi", "django",
/// "express_js", "laravel").
pub fn analyze_project(
    root: &Path,
    project_id: &str,
    frameworks: &[&str],
    selected_paths: &[String],
) -> WebAnalysisResult {
    let regexes = WebRegexes::new();
    let files = source_files(root);
    let symbols = symbol_index(&files, &regexes);
    let selected: std::collections::HashSet<&String> = selected_paths.iter().collect();
    let framework_set: std::collections::HashSet<&str> = frameworks.iter().copied().collect();
    let mut endpoints: Vec<EndpointFact> = Vec::new();
    for (path, rel) in &files {
        if !selected.is_empty() && !selected.contains(rel) {
            continue;
        }
        let bytes = match std::fs::read(path) {
            Ok(bytes) => bytes,
            Err(_) => continue,
        };
        let text = decode_utf8_replace(&bytes);
        let suffix = suffix_lower(path);
        if suffix == "py" && (framework_set.contains("fastapi") || framework_set.contains("django"))
        {
            if framework_set.contains("fastapi") {
                for capture in regexes.fastapi.captures_iter(&text).flatten() {
                    let whole = capture.get(0).map(|m| m.start()).unwrap_or(0);
                    endpoints.push(endpoint(
                        project_id,
                        "fastapi",
                        capture.name("method").map(|m| m.as_str()).unwrap_or(""),
                        capture.name("path").map(|m| m.as_str()).unwrap_or(""),
                        rel,
                        line_at(&text, whole),
                        capture.name("handler").map(|m| m.as_str()).unwrap_or(""),
                        &symbols,
                        "",
                    ));
                }
            }
            if framework_set.contains("django") {
                for capture in regexes.django.captures_iter(&text).flatten() {
                    let whole = capture.get(0).map(|m| m.start()).unwrap_or(0);
                    let mut raw_handler = capture.name("handler").map(|m| m.as_str()).unwrap_or("");
                    if let Some(stripped) = raw_handler.strip_suffix(".as_view") {
                        raw_handler = stripped;
                    }
                    let handler = raw_handler.rsplit('.').next().unwrap_or("");
                    endpoints.push(endpoint(
                        project_id,
                        "django",
                        "ALL",
                        capture.name("path").map(|m| m.as_str()).unwrap_or(""),
                        rel,
                        line_at(&text, whole),
                        handler,
                        &symbols,
                        "",
                    ));
                }
            }
        } else if (suffix == "js" || suffix == "jsx") && framework_set.contains("express_js") {
            for capture in regexes.express.captures_iter(&text).flatten() {
                let whole = capture.get(0).map(|m| m.start()).unwrap_or(0);
                endpoints.push(endpoint(
                    project_id,
                    "express_js",
                    capture.name("method").map(|m| m.as_str()).unwrap_or(""),
                    capture.name("path").map(|m| m.as_str()).unwrap_or(""),
                    rel,
                    line_at(&text, whole),
                    capture.name("handler").map(|m| m.as_str()).unwrap_or(""),
                    &symbols,
                    "",
                ));
            }
        } else if suffix == "php" && framework_set.contains("laravel") {
            for capture in regexes.laravel.captures_iter(&text).flatten() {
                let whole = capture.get(0).map(|m| m.start()).unwrap_or(0);
                endpoints.push(endpoint(
                    project_id,
                    "laravel",
                    capture.name("method").map(|m| m.as_str()).unwrap_or(""),
                    capture.name("path").map(|m| m.as_str()).unwrap_or(""),
                    rel,
                    line_at(&text, whole),
                    capture.name("handler").map(|m| m.as_str()).unwrap_or(""),
                    &symbols,
                    capture.name("scope").map(|m| m.as_str()).unwrap_or(""),
                ));
            }
        }
    }
    // sort + dedupe theo endpoint_id (last wins) rồi (framework, file, line).
    WebAnalysisResult {
        project_id: project_id.to_string(),
        endpoints: dedupe_and_sort(endpoints),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_path_collapses_slashes() {
        assert_eq!(normalize_path("items/{id}/"), "/items/{id}/");
        assert_eq!(normalize_path("//a///b"), "/a/b");
        assert_eq!(normalize_path("/x"), "/x");
    }

    #[test]
    fn fastapi_regex_matches_decorator_then_def() {
        let regexes = WebRegexes::new();
        let text = "@app.get(\"/items\")\nasync def read_items():\n    pass\n";
        let capture = regexes.fastapi.captures(text).unwrap().unwrap();
        assert_eq!(capture.name("method").unwrap().as_str(), "get");
        assert_eq!(capture.name("path").unwrap().as_str(), "/items");
        assert_eq!(capture.name("handler").unwrap().as_str(), "read_items");
    }

    #[test]
    fn laravel_regex_matches_class_handler() {
        let regexes = WebRegexes::new();
        let text = "Route::get('/users', [UserController::class, 'index']);\n";
        let capture = regexes.laravel.captures(text).unwrap().unwrap();
        assert_eq!(capture.name("method").unwrap().as_str(), "get");
        assert_eq!(capture.name("scope").unwrap().as_str(), "UserController");
        assert_eq!(capture.name("handler").unwrap().as_str(), "index");
    }
}
