//! Port `tools/ts/agents/symbol_agent.py` — React role, middleware, API call,
//! navigation call, route config, navigator/param-list extraction.

use std::collections::BTreeMap;

use tree_sitter::Node;

use crate::ast::{ApiCallDef, NavigatorDef, ParamListDef};
use crate::parser::{find_nodes_by_type, node_text};
use crate::regexes::{
    ends_with_any, factory_to_nav_type, nav_fn_call_re, nav_obj_method_re, regexes,
};

/// `_index_module_name`.
pub fn index_module_name(file_path: &str) -> Option<String> {
    const INDEX_BASENAMES: [&str; 4] = ["index.ts", "index.tsx", "index.js", "index.jsx"];
    let normalized = file_path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    if parts.len() >= 2 && INDEX_BASENAMES.contains(&parts[parts.len() - 1]) {
        return Some(parts[parts.len() - 2].to_string());
    }
    None
}

const SCREEN_DIR_SEGMENTS: [&str; 7] =
    ["screens", "screen", "pages", "page", "views", "routes", "route"];
const SERVICE_DIR_SEGMENTS: [&str; 9] = [
    "api",
    "apis",
    "services",
    "service",
    "middleware",
    "http",
    "network",
    "repository",
    "repositories",
];

pub fn is_screen_file(file_path: &str) -> bool {
    file_path
        .replace('\\', "/")
        .split('/')
        .any(|seg| SCREEN_DIR_SEGMENTS.contains(&seg.to_lowercase().as_str()))
}

pub fn is_service_file(file_path: &str) -> bool {
    file_path
        .replace('\\', "/")
        .split('/')
        .any(|seg| SERVICE_DIR_SEGMENTS.contains(&seg.to_lowercase().as_str()))
}

/// `_file_path_to_route` — Expo/Next route normalization.
pub fn file_path_to_route(file_path: &str) -> Option<String> {
    let normalized = file_path.replace('\\', "/");
    let parts: Vec<&str> = normalized.split('/').collect();
    for (i, seg) in parts.iter().enumerate() {
        if (*seg == "app" || *seg == "pages") && i < parts.len() - 1 {
            let mut route_parts: Vec<String> = parts[i + 1..].iter().map(|s| s.to_string()).collect();
            if let Some(last) = route_parts.last_mut() {
                let stem = last.split('.').next().unwrap_or(last).to_string();
                if stem == "index" {
                    route_parts.pop();
                } else {
                    *last = stem;
                }
            }
            let route_parts: Vec<String> = route_parts
                .into_iter()
                .filter(|p| !(p.starts_with('(') && p.ends_with(')')))
                .collect();
            if !route_parts.is_empty() {
                return Some(format!("/{}", route_parts.join("/")));
            }
        }
    }
    None
}

// ── Middleware / React role ─────────────────────────────────────────────────

pub fn detect_middleware_kind(name: &str, code: &str, file_path: &str) -> String {
    let rx = regexes();
    let _ = name;
    if rx.middleware_api.is_match(code) {
        return "api".to_string();
    }
    if rx.middleware_query.is_match(code) {
        return "query".to_string();
    }
    if rx.middleware_redux.is_match(code) {
        return "redux".to_string();
    }
    if rx.service_layer.is_match(code) {
        return "service".to_string();
    }
    if is_service_file(file_path) {
        return "service".to_string();
    }
    String::new()
}

/// `_detect_react_role` — priority: middleware > hook > screen > component > "".
pub fn detect_react_role(
    name: &str,
    file_path: &str,
    has_jsx: bool,
    middleware_kind: &str,
    code: &str,
) -> String {
    let rx = regexes();
    if !middleware_kind.is_empty() {
        return "middleware".to_string();
    }
    if name.starts_with("use") && name.chars().count() > 3 {
        let fourth = name.chars().nth(3).unwrap();
        if fourth.is_uppercase() {
            return "hook".to_string();
        }
    }
    if has_jsx && !name.is_empty() && name.chars().next().unwrap().is_uppercase() {
        let is_hoc_name = rx.hoc_factory_name.is_match(name)
            || ends_with_any(name, WRAPPER_NAME_SUFFIXES_HERE);
        if is_hoc_name || rx.wraps_children.is_match(code) {
            return "component".to_string();
        }
        if ends_with_any(name, NAV_CHROME_SUFFIXES_HERE) {
            return "component".to_string();
        }
        if ends_with_any(name, NAVIGATOR_NAME_SUFFIXES_HERE)
            || rx.navigator_factory_name.is_match(name)
        {
            return "component".to_string();
        }
        let folder_name = index_module_name(file_path).unwrap_or_default();
        let has_screen_name =
            ends_with_any(name, SCREEN_NAME_SUFFIXES_HERE) || ends_with_any(&folder_name, SCREEN_NAME_SUFFIXES_HERE);
        let in_screen_dir = is_screen_file(file_path);
        if rx.screen_hooks.is_match(code) && (has_screen_name || in_screen_dir) {
            return "screen".to_string();
        }
        if rx.screen_nav_call.is_match(code) && (has_screen_name || in_screen_dir) {
            return "screen".to_string();
        }
        if rx.screen_prop_names.is_match(code) && (has_screen_name || in_screen_dir) {
            return "screen".to_string();
        }
        if in_screen_dir {
            return "screen".to_string();
        }
        if has_screen_name {
            return "screen".to_string();
        }
        return "component".to_string();
    }
    String::new()
}

// Module-level consts (tách để mượn lifetime &'static).
pub const WRAPPER_NAME_SUFFIXES_HERE: &[&str] = &[
    "Wrapper", "Layout", "Provider", "Shell", "Guard", "Boundary", "Container", "HOC", "Hoc",
    "Decorator",
];
pub const NAV_CHROME_SUFFIXES_HERE: &[&str] = &[
    "HeaderRight",
    "HeaderLeft",
    "HeaderTitle",
    "HeaderButton",
    "HeaderBackButton",
    "HeaderBackImage",
    "HeaderBar",
    "TabBar",
    "TabBarIcon",
    "TabIcon",
    "TabLabel",
    "TabBadge",
    "TabItem",
    "DrawerItem",
    "DrawerIcon",
    "DrawerLabel",
    "DrawerContent",
    "NavBar",
    "NavigationBar",
    "BottomTabBar",
    "Toolbar",
    "FooterBar",
    "StatusBar",
    "ActionBar",
];
pub const NAVIGATOR_NAME_SUFFIXES_HERE: &[&str] =
    &["Navigator", "Navigation", "Stack", "Router", "Switcher"];
pub const SCREEN_NAME_SUFFIXES_HERE: &[&str] =
    &["Screen", "Page", "View", "Tab", "Scene", "Activity"];

// ── URL helpers ─────────────────────────────────────────────────────────────

pub fn normalize_url_pattern(url: &str) -> String {
    if url.is_empty() {
        return String::new();
    }
    let mut url = url.trim().to_string();
    let param_re = regex::Regex::new(r"\$\{[^}]+\}").unwrap();
    url = param_re.replace_all(&url, ":param").to_string();
    if url != "/" && url.ends_with('/') {
        url = url.trim_end_matches('/').to_string();
    }
    url
}

pub fn normalize_http_method(method: &str) -> String {
    method.to_uppercase()
}

pub fn merge_base_url(base: Option<&str>, path: &str) -> String {
    let Some(base) = base else {
        return normalize_url_pattern(path);
    };
    let base = base.trim_end_matches('/');
    let path = if path.starts_with('/') { path.to_string() } else { format!("/{path}") };
    normalize_url_pattern(&format!("{base}{path}"))
}

/// `_clean_url_expr`.
pub fn clean_url_expr(raw: &str) -> String {
    let rx = regexes();
    let mut s = raw.trim().trim_matches(|c| c == '`' || c == '\'' || c == '"').to_string();
    s = rx.env_var.replace_all(&s, "").to_string();
    s.trim()
        .trim_start_matches('+')
        .trim()
        .trim_matches(|c| c == '"' || c == '\'' || c == '`')
        .trim()
        .to_string()
}

/// `_extract_file_base_url`.
pub fn extract_file_base_url(code: &str) -> String {
    let rx = regexes();
    let Some(caps) = rx.axios_create.captures(code) else {
        return String::new();
    };
    let raw = caps
        .name("base")
        .map(|m| m.as_str().trim().trim_matches(|c| c == '`' || c == '\'' || c == '"').to_string())
        .unwrap_or_default();
    let raw = rx.env_var.replace_all(&raw, "").trim().trim_start_matches('+').trim().trim_matches(|c| c == '`' || c == '\'' || c == '"').to_string();
    if raw.is_empty() || raw.contains("process") || raw.to_lowercase().contains("env") {
        return String::new();
    }
    normalize_url_pattern(&raw)
}

fn uuid5_url(name: &str) -> String {
    let uuid = uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, name.as_bytes());
    uuid.to_string()
}

/// `_extract_api_calls`.
pub fn extract_api_calls(
    code: &str,
    function_id: &str,
    rel_path: &str,
    start_line: i64,
    file_base_url: &str,
) -> Vec<ApiCallDef> {
    let rx = regexes();
    let mut results: Vec<ApiCallDef> = Vec::new();

    let make_call = |raw_url: &str, method: &str, base_url: &str| -> Option<ApiCallDef> {
        let cleaned = clean_url_expr(raw_url);
        if cleaned.is_empty() {
            return None;
        }
        let effective_base = if !base_url.is_empty() { base_url } else { file_base_url };
        let resolved = if !effective_base.is_empty() {
            merge_base_url(Some(effective_base), &cleaned)
        } else {
            normalize_url_pattern(&cleaned)
        };
        if resolved.is_empty() {
            return None;
        }
        let norm_method = normalize_http_method(method);
        let sid = uuid5_url(&format!("ApiCall::{function_id}::{norm_method}::{resolved}"));
        Some(ApiCallDef {
            symbol_id: sid,
            caller_function_id: function_id.to_string(),
            url_pattern: resolved,
            raw_url: raw_url.trim().to_string(),
            http_method: norm_method,
            base_url_ref: if base_url.is_empty() { file_base_url.to_string() } else { base_url.to_string() },
            file_path: rel_path.to_string(),
            start_line,
            confidence: 0.85,
        })
    };

    for m in rx.fetch_call.captures_iter(code) {
        let raw = m.name("url").map(|m| m.as_str()).unwrap_or_default();
        let start = m.get(0).unwrap().start();
        let vicinity = &code[start..std::cmp::min(code.len(), start + 300)];
        let method = rx
            .fetch_method
            .captures(vicinity)
            .and_then(|c| c.name("method"))
            .map(|m| m.as_str())
            .unwrap_or("GET");
        if let Some(call) = make_call(raw, method, "") {
            results.push(call);
        }
    }
    for m in rx.axios_shorthand.captures_iter(code) {
        let url = m.name("url").map(|m| m.as_str()).unwrap_or_default();
        let method = m.name("method").map(|m| m.as_str()).unwrap_or_default();
        if let Some(call) = make_call(url, method, "") {
            results.push(call);
        }
    }
    for m in rx.axios_config.captures_iter(code) {
        let url = m.name("url").map(|m| m.as_str()).unwrap_or_default();
        let method = m.name("method").map(|m| m.as_str()).unwrap_or("GET");
        if let Some(call) = make_call(url, method, "") {
            results.push(call);
        }
    }
    for m in rx.http_client.captures_iter(code) {
        let url = m.name("url").map(|m| m.as_str()).unwrap_or_default();
        let method = m.name("method").map(|m| m.as_str()).unwrap_or_default();
        if let Some(call) = make_call(url, method, "") {
            results.push(call);
        }
    }
    for m in rx.named_client.captures_iter(code) {
        let url = m.name("url").map(|m| m.as_str()).unwrap_or_default();
        let method = m.name("method").map(|m| m.as_str()).unwrap_or_default();
        if let Some(call) = make_call(url, method, "") {
            results.push(call);
        }
    }

    // dedup theo method:pattern — giữ first-seen
    let mut seen: Vec<String> = Vec::new();
    let mut deduped: Vec<ApiCallDef> = Vec::new();
    for call in results {
        let key = format!("{}:{}", call.http_method, call.url_pattern);
        if seen.contains(&key) {
            continue;
        }
        seen.push(key);
        deduped.push(call);
    }
    deduped
}

/// `_collect_navigate_calls` — dedup dict giữ first-seen vị trí; các pattern
/// chạy ĐÚNG thứ tự như Python.
pub fn collect_navigate_calls(code: &str) -> Vec<(String, String)> {
    let rx = regexes();
    let mut seen: Vec<(String, String)> = Vec::new();
    let mark = |target: &str, method: &str, seen: &mut Vec<(String, String)>| {
        if !target.is_empty() && !seen.iter().any(|(t, m)| t == target && m == method) {
            seen.push((target.to_string(), method.to_string()));
        }
    };

    let mut nav_obj_vars: Vec<String> =
        vec!["navigation".to_string(), "navigator".to_string()];
    let mut nav_fn_vars: Vec<String> = Vec::new();
    let mut hist_vars: Vec<String> = Vec::new();

    for caps in rx.assign_use_navigation.captures_iter(code) {
        let var = caps.name("var").unwrap().as_str().to_string();
        if !nav_obj_vars.contains(&var) {
            nav_obj_vars.push(var);
        }
    }
    for caps in rx.assign_use_router.captures_iter(code) {
        let var = caps.name("var").unwrap().as_str().to_string();
        if !nav_obj_vars.contains(&var) {
            nav_obj_vars.push(var);
        }
    }
    for caps in rx.assign_use_history.captures_iter(code) {
        hist_vars.push(caps.name("var").unwrap().as_str().to_string());
    }
    if rx.assign_use_navigation_destruct.is_match(code) {
        nav_fn_vars.push("navigate".to_string());
    }
    for caps in rx.assign_use_navigate.captures_iter(code) {
        nav_fn_vars.push(caps.name("var").unwrap().as_str().to_string());
    }

    let has_use_router = rx.has_use_router.is_match(code);
    let has_use_history = rx.has_use_history.is_match(code);

    for caps in rx.nav_prop_call.captures_iter(code) {
        let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
        let method = caps.name("method").map(|m| m.as_str()).unwrap_or_default();
        mark(target, method, &mut seen);
    }
    for caps in rx.nav_prop_obj.captures_iter(code) {
        let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
        mark(target, "navigate", &mut seen);
    }
    for var in nav_obj_vars.iter().filter(|v| *v != "navigation" && *v != "navigator") {
        for caps in nav_obj_method_re(var).captures_iter(code) {
            let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
            let method = caps.name("method").map(|m| m.as_str()).unwrap_or_default();
            mark(target, method, &mut seen);
        }
    }
    if has_use_router {
        for caps in rx.router_call.captures_iter(code) {
            let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
            let method = caps.name("method").map(|m| m.as_str()).unwrap_or_default();
            mark(target, method, &mut seen);
        }
        for caps in rx.router_obj.captures_iter(code) {
            let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
            mark(target, "navigate", &mut seen);
        }
    }
    if has_use_history {
        let vars: Vec<String> = if hist_vars.is_empty() {
            vec!["history".to_string()]
        } else {
            hist_vars.clone()
        };
        for var in vars {
            for caps in nav_obj_method_re(&var).captures_iter(code) {
                let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
                let method = caps.name("method").map(|m| m.as_str()).unwrap_or_default();
                mark(target, method, &mut seen);
            }
        }
    }
    for var in &nav_fn_vars {
        for caps in nav_fn_call_re(var).captures_iter(code) {
            let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
            mark(target, "navigate", &mut seen);
        }
    }
    for caps in rx.nav_ref_call.captures_iter(code) {
        let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
        let method = caps.name("method").map(|m| m.as_str()).unwrap_or_default();
        mark(target, method, &mut seen);
    }
    for caps in rx.nav_service_call.captures_iter(code) {
        let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
        let method = caps.name("method").map(|m| m.as_str()).unwrap_or_default();
        mark(target, method, &mut seen);
    }
    for caps in rx.nav_service_obj.captures_iter(code) {
        let target = caps.name("target").map(|m| m.as_str()).unwrap_or_default();
        mark(target, "navigate", &mut seen);
    }
    for caps in rx.jsx_link.captures_iter(code) {
        let target = caps
            .name("route")
            .or_else(|| caps.name("route2"))
            .map(|m| m.as_str())
            .unwrap_or_default();
        mark(target.trim(), "link", &mut seen);
    }
    for caps in rx.jsx_navigate_el.captures_iter(code) {
        let target = caps
            .name("route")
            .or_else(|| caps.name("route2"))
            .map(|m| m.as_str())
            .unwrap_or_default();
        mark(target.trim(), "navigate", &mut seen);
    }
    seen
}

/// `_classify_nav_context`.
pub fn classify_nav_context(code: &str) -> String {
    let rx = regexes();
    if rx.user_trigger.is_match(code) {
        return "user".to_string();
    }
    if rx.async_trigger.is_match(code) {
        return "async".to_string();
    }
    if rx.system_trigger.is_match(code) {
        return "system".to_string();
    }
    "user".to_string()
}

/// `_detect_nav_guard`.
pub fn detect_nav_guard(code: &str) -> Option<String> {
    let rx = regexes();
    if rx.auth_guard.is_match(code) {
        return Some("auth".to_string());
    }
    if rx.perm_guard.is_match(code) {
        return Some("permission".to_string());
    }
    None
}

/// `_collect_route_configs` — dict {name: comp} last-write-wins, thứ tự first-seen.
pub fn collect_route_configs(code: &str) -> Vec<(String, String)> {
    let rx = regexes();
    // Python dict: giá trị ghi đè (last wins) nhưng KEY giữ first-seen vị trí.
    let mut order: Vec<String> = Vec::new();
    let mut map: BTreeMap<String, String> = BTreeMap::new();
    for m in rx.screen_elem_start.find_iter(code) {
        let start = m.start();
        let window = &code[start..std::cmp::min(code.len(), start + 1000)];
        let name_m = rx.screen_name_attr.captures(window);
        let comp_m = rx.screen_comp_attr.captures(window);
        if let (Some(name_m), Some(comp_m)) = (name_m, comp_m) {
            let name = name_m.name("name").map(|m| m.as_str()).unwrap_or_default();
            let comp = comp_m.name("comp").map(|m| m.as_str()).unwrap_or_default();
            if !name.is_empty() && !comp.is_empty() {
                if !map.contains_key(name) {
                    order.push(name.to_string());
                }
                map.insert(name.to_string(), comp.to_string());
            }
        }
    }
    order
        .into_iter()
        .filter_map(|name| map.remove(&name).map(|comp| (name, comp)))
        .collect()
}

/// `_extract_navigator_declarations`.
pub fn extract_navigator_declarations(code: &str, rel_path: &str) -> Vec<NavigatorDef> {
    let rx = regexes();
    let routes_by_file = collect_route_configs(code);
    let mut results = Vec::new();
    for m in rx.navigator_factory.captures_iter(code) {
        let var_name = m.name("var_name").unwrap().as_str().to_string();
        let factory = m.name("factory").unwrap().as_str().to_string();
        let generic = m
            .name("generic")
            .map(|g| g.as_str().trim().to_string())
            .unwrap_or_default();
        let param_list_ref = if generic.is_empty() {
            String::new()
        } else {
            generic.split(',').next().unwrap_or("").trim().to_string()
        };
        let nav_type = factory_to_nav_type(&factory).to_string();
        let start_line = code[..m.get(0).unwrap().start()].matches('\n').count() as i64 + 1;
        results.push(NavigatorDef {
            symbol_id: format!("Navigator::{var_name}::{rel_path}"),
            var_name,
            factory,
            nav_type,
            param_list_ref,
            file_path: rel_path.to_string(),
            start_line,
            routes: routes_by_file.clone(),
        });
    }
    results
}

/// `_extract_param_lists`.
pub fn extract_param_lists(root: Node, source: &[u8], rel_path: &str) -> Vec<ParamListDef> {
    let mut results = Vec::new();
    for node in find_nodes_by_type(root, "type_alias_declaration") {
        let Some(name_node) = node.child_by_field_name("name") else {
            continue;
        };
        let type_name = node_text(name_node, source);
        if !type_name.ends_with("ParamList") {
            continue;
        }
        let mut routes: BTreeMap<String, String> = BTreeMap::new();
        if let Some(value_node) = node.child_by_field_name("value") {
            for prop in find_nodes_by_type(value_node, "property_signature") {
                let Some(key_node) = prop.child_by_field_name("name") else {
                    continue;
                };
                let key = node_text(key_node, source)
                    .trim_matches(|c| c == '"' || c == '\'')
                    .to_string();
                let type_str = prop
                    .child_by_field_name("type")
                    .map(|t| {
                        let text = node_text(t, source)
                            .trim()
                            .trim_start_matches(':')
                            .trim()
                            .to_string();
                        text.split_whitespace().collect::<Vec<_>>().join(" ")
                    })
                    .unwrap_or_else(|| "undefined".to_string());
                routes.insert(key, type_str);
            }
        }
        let _start_line =
            source[..node.start_byte()].iter().filter(|&&b| b == b'\n').count() as i64 + 1;
        results.push(ParamListDef {
            symbol_id: format!("ParamList::{type_name}::{rel_path}"),
            name: type_name,
            file_path: rel_path.to_string(),
            routes,
        });
    }
    results
}

/// `_has_jsx_in_subtree`.
pub fn has_jsx_in_subtree(node: Node) -> bool {
    if matches!(
        node.kind(),
        "jsx_element" | "jsx_self_closing_element" | "jsx_fragment" | "jsx_opening_element"
    ) {
        return true;
    }
    for child in node.children(&mut node.walk()) {
        if has_jsx_in_subtree(child) {
            return true;
        }
    }
    false
}

/// `_collect_rendered_components` — PascalCase JSX components, insertion order.
pub fn collect_rendered_components(node: Node, source: &[u8]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    for kind in ["jsx_opening_element", "jsx_self_closing_element"] {
        for jsx_node in find_nodes_by_type(node, kind) {
            if let Some(name) = crate::parser::jsx_name(jsx_node, source)
                && name.chars().next().map(|c| c.is_uppercase()).unwrap_or(false)
                    && !names.contains(&name)
                {
                    names.push(name);
                }
        }
    }
    names
}
