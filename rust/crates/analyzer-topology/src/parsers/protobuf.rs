//! Port `parsers/protobuf.py` — static protobuf package/service/RPC extraction
//! với gRPC HTTP annotation (google.api.http style).

use std::sync::LazyLock;

use regex::Regex;

use crate::models::{
    confidence, descriptor_role, descriptor_type, parse_depth, safe_summary, sorted_unique,
    stable_fact_id, stable_module_id, DescriptorFact, EndpointFact, PyValue, SourceEvidence,
};
use crate::parsers::common::{line_number, module_path_for_file};
use crate::parsers::DescriptorParseOutput;

static PACKAGE_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bpackage\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;").expect("regex"));
static SERVICE_START_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\bservice\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{").expect("regex"));
static RPC_HEADER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(stream\s+)?([.\w]+)\s*\)\s*returns\s*\(\s*(stream\s+)?([.\w]+)\s*\)",
    )
    .expect("regex")
});
static HTTP_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(?i)\b(get|put|post|delete|patch)\s*:\s*"([^"]+)""#).expect("regex"));

struct RpcMatch {
    start: usize,
    end: usize,
    name: String,
    client_stream: bool,
    request_type: String,
    server_stream: bool,
    response_type: String,
}

fn rpc_matches(body: &str) -> Vec<RpcMatch> {
    RPC_HEADER_RE
        .captures_iter(body)
        .map(|caps| RpcMatch {
            start: caps.get(0).map(|m| m.start()).unwrap_or(0),
            end: caps.get(0).map(|m| m.end()).unwrap_or(0),
            name: caps.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
            client_stream: caps.get(2).is_some(),
            request_type: caps
                .get(3)
                .map(|m| m.as_str().to_string())
                .unwrap_or_default(),
            server_stream: caps.get(4).is_some(),
            response_type: caps
                .get(5)
                .map(|m| m.as_str().to_string())
                .unwrap_or_default(),
        })
        .collect()
}

/// `_service_blocks` — (service, body, body_offset) theo thứ tự xuất hiện.
fn service_blocks(text: &str) -> Vec<(String, String, usize)> {
    let mut out = Vec::new();
    for caps in SERVICE_START_RE.captures_iter(text) {
        let Some(service) = caps.get(1) else { continue };
        let whole = caps.get(0).expect("group 0");
        let mut depth = 1usize;
        let mut index = whole.end();
        let bytes = text.as_bytes();
        while index < bytes.len() && depth > 0 {
            match bytes[index] {
                b'{' => depth += 1,
                b'}' => depth -= 1,
                _ => {}
            }
            index += 1;
        }
        let body = text[whole.end()..index.saturating_sub(1)].to_string();
        out.push((service.as_str().to_string(), body, whole.end()));
    }
    out
}

/// `_rpc_options(body, start)` — nội dung `{ ... }` options block liền sau.
fn rpc_options(body: &str, start: usize) -> String {
    let bytes = body.as_bytes();
    let mut index = start;
    while index < bytes.len() && bytes[index].is_ascii_whitespace() {
        index += 1;
    }
    if index >= bytes.len() || bytes[index] != b'{' {
        return String::new();
    }
    let mut depth = 1usize;
    let mut end = index + 1;
    while end < bytes.len() && depth > 0 {
        match bytes[end] {
            b'{' => depth += 1,
            b'}' => depth -= 1,
            _ => {}
        }
        end += 1;
    }
    body[index + 1..end.saturating_sub(1)].to_string()
}

pub fn parse_protobuf(project_id: &str, path: &str, text: &str) -> DescriptorParseOutput {
    let module_path = module_path_for_file(path);
    let package = PACKAGE_RE
        .captures(text)
        .and_then(|caps| caps.get(1))
        .map(|m| m.as_str().to_string())
        .unwrap_or_default();
    let mut services: Vec<String> = Vec::new();
    let mut endpoints: Vec<EndpointFact> = Vec::new();
    for (service, body, body_offset) in service_blocks(text) {
        services.push(service.clone());
        for rpc in rpc_matches(&body) {
            let options_text = rpc_options(&body, rpc.end);
            let http_match = HTTP_RE.captures(&options_text);
            let method = http_match
                .as_ref()
                .and_then(|caps| caps.get(1))
                .map(|m| m.as_str().to_uppercase())
                .unwrap_or_default();
            let route = http_match
                .as_ref()
                .and_then(|caps| caps.get(2))
                .map(|m| m.as_str().to_string())
                .unwrap_or_default();
            let qualified = [package.as_str(), service.as_str(), rpc.name.as_str()]
                .iter()
                .filter(|value| !value.is_empty())
                .copied()
                .collect::<Vec<&str>>()
                .join(".");
            let service_qualified = [package.as_str(), service.as_str()]
                .iter()
                .filter(|value| !value.is_empty())
                .copied()
                .collect::<Vec<&str>>()
                .join(".");
            let endpoint_id = stable_fact_id(project_id, "grpc-endpoint", &[path, &qualified])
                .unwrap_or_default();
            let module_id =
                stable_module_id(project_id, &module_path).unwrap_or_default();
            let start_line = line_number(text, body_offset + rpc.start);
            endpoints.push(EndpointFact {
                id: endpoint_id,
                project_id: project_id.to_string(),
                module_id,
                protocol: "grpc",
                name: rpc.name.clone(),
                path: route,
                method,
                framework: "protobuf".to_string(),
                handler_id: String::new(),
                service: service_qualified,
                request_type: rpc.request_type.clone(),
                response_type: rpc.response_type.clone(),
                client_streaming: rpc.client_stream,
                server_streaming: rpc.server_stream,
                file_path: path.to_string(),
                start_line: Some(start_line),
                security: PyValue::dict(),
                confidence: confidence::HIGH,
                evidence: vec![SourceEvidence::at_line(path, start_line)],
                original_kind: "GrpcEndpoint".to_string(),
            });
        }
    }
    let mut sorted_endpoints = endpoints.clone();
    sorted_endpoints.sort_by(|a, b| a.id.cmp(&b.id));
    let mut properties = PyValue::dict();
    properties.set("package", PyValue::Str(package.clone()));
    properties.set(
        "services",
        PyValue::List(
            sorted_unique(services)
                .into_iter()
                .map(PyValue::Str)
                .collect(),
        ),
    );
    // Python: endpoint_ids lấy từ list TRƯỚC khi sort (insertion order).
    properties.set(
        "endpoint_ids",
        PyValue::List(
            endpoints
                .iter()
                .map(|item| PyValue::Str(item.id.clone()))
                .collect(),
        ),
    );
    let summary = safe_summary(&format!(
        "Protobuf package {} with {} RPCs",
        if package.is_empty() { "(default)" } else { package.as_str() },
        sorted_endpoints.len()
    ));
    let descriptor = DescriptorFact::create(
        project_id,
        &module_path,
        path,
        descriptor_type::PROTOBUF,
        descriptor_role::INTERFACE,
        "protobuf",
        parse_depth::SEMANTIC,
        summary,
        properties,
        confidence::HIGH,
        vec![SourceEvidence::new(path)],
        vec![],
    )
    .expect("protobuf descriptor");
    DescriptorParseOutput {
        descriptor,
        dependencies: vec![],
        endpoints: sorted_endpoints,
        diagnostics: vec![],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_service_and_rpc() {
        let output = parse_protobuf(
            "Bank",
            "api/service.proto",
            "syntax = \"proto3\";\npackage bank.v1;\nservice BankService {\n  rpc GetAccount (GetRequest) returns (GetReply) {\n    option (google.api.http) = { get: \"/v1/account\" };\n  }\n  rpc StreamUpdates (Req) returns (stream Upd);\n}\n",
        );
        assert_eq!(output.endpoints.len(), 2);
        assert_eq!(output.endpoints[0].method, "GET");
        assert_eq!(output.endpoints[0].path, "/v1/account");
        assert!(output.endpoints[1].server_streaming);
    }
}
