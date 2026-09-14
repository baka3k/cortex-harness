//! `cortex-mcp` binary — streamable-HTTP MCP server thay
//! `code-tiny/mcp/unified_mcp.py` (production launch: `code-tiny/mcp.sh`,
//! `:8788`, path `/mcp`; doc server `:8789` tương tự).
//!
//! Launch mechanics mirror the Python server:
//! * `--transport streamable-http` (default; stdio không phục vụ phase 11),
//! * `--host` (default `127.0.0.1`, env `FASTMCP_HOST`),
//! * `--port` (default `8788`, env `FASTMCP_PORT`),
//! * `--path` (default `/mcp`, env `FASTMCP_STREAMABLE_HTTP_PATH`),
//! * stateless HTTP (`stateless_http=True` ở unified → rmcp session-free),
//! * `/health` + `/ready` custom routes cùng shape Python.

use std::sync::Arc;

use axum::response::Json;
use axum::routing::get;
use axum::Router;
use rmcp::transport::streamable_http_server::session::never::NeverSessionManager;
use rmcp::transport::streamable_http_server::{StreamableHttpServerConfig, StreamableHttpService};

use cortex_mcp::server::CortexMcpServer;

fn arg_value(flag: &str, env: &str, default: &str) -> String {
    let args: Vec<String> = std::env::args().collect();
    if let Some(position) = args.iter().position(|item| item == flag)
        && let Some(value) = args.get(position + 1)
    {
        return value.clone();
    }
    std::env::var(env).unwrap_or_else(|_| default.to_string())
}

fn normalize_http_path(path: &str) -> String {
    let path = path.trim();
    if path.is_empty() {
        return "/mcp".to_string();
    }
    if path.starts_with('/') {
        return path.to_string();
    }
    format!("/{path}")
}

#[tokio::main]
async fn main() {
    let transport = arg_value("--transport", "FASTMCP_TRANSPORT", "streamable-http");
    if transport != "streamable-http" {
        eprintln!(
            "Unsupported transport '{transport}': the phase-11 Rust server serves streamable-http only."
        );
        std::process::exit(2);
    }
    let host = arg_value("--host", "FASTMCP_HOST", "127.0.0.1");
    let port: u16 = arg_value("--port", "FASTMCP_PORT", "8788")
        .parse()
        .expect("--port must be a valid port number");
    let path = normalize_http_path(&arg_value(
        "--path",
        "FASTMCP_STREAMABLE_HTTP_PATH",
        "/mcp",
    ));
    // Server flavor: `--server mind` (or MCP_SERVER_NAME=mind_mcp) serves the
    // doc/mind tool surface (`doc-tiny/mcp_graph_rag.py`); default = unified.
    let server_name = arg_value("--server", "MCP_SERVER_NAME", "");
    let flavor = cortex_mcp::server::ServerFlavor::resolve(Some(&server_name));

    // Python unified serves `stateless_http=True, json_response=True`:
    // no session state, plain application/json responses.
    #[allow(clippy::field_reassign_with_default)]
    let mut config = StreamableHttpServerConfig::default();
    config.legacy_session_mode = false;
    config.json_response = true;
    let service = StreamableHttpService::new(
        move || Ok(CortexMcpServer::with_flavor(flavor)),
        Arc::new(NeverSessionManager::default()),
        config,
    );

    let app = Router::new()
        .route(
            "/health",
            get(|| async {
                Json(serde_json::json!({
                    "status": "healthy",
                    "service": "fastmcp-server",
                    "liveness": true,
                }))
            }),
        )
        .route(
            "/ready",
            get(|| async {
                Json(serde_json::json!({
                    "status": "ready",
                    "service": "fastmcp-server",
                    "readiness": true,
                    "storage_state": "ready",
                }))
            }),
        )
        .fallback_service(service);

    let listener = tokio::net::TcpListener::bind((host.as_str(), port))
        .await
        .unwrap_or_else(|error| {
            panic!("Failed to bind {host}:{port}: {error}");
        });
    println!(
        "Starting MCP server: {}",
        match flavor {
            cortex_mcp::server::ServerFlavor::Mind => "mind_mcp",
            cortex_mcp::server::ServerFlavor::Graph => "graph_mcp",
        }
    );
    println!("Transport: streamable-http");
    println!("Endpoint: http://{host}:{port}{path}");

    let shutdown = async {
        let sigint = tokio::signal::ctrl_c();
        #[cfg(unix)]
        {
            let mut sigterm = tokio::signal::unix::signal(
                tokio::signal::unix::SignalKind::terminate(),
            )
            .expect("SIGTERM handler");
            tokio::select! {
                _ = sigint => println!("Received SIGINT. Shutting down."),
                _ = sigterm.recv() => println!("Received SIGTERM. Shutting down."),
            }
        }
        #[cfg(not(unix))]
        {
            let _ = sigint.await;
            println!("Received SIGINT. Shutting down.");
        }
        // Embedded FalkorDB children: SHUTDOWN NOSAVE (không đè data.rdb).
        cortex_mcp::graph::runtime::shutdown_runtime();
    };
    if let Err(error) = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown)
        .await
    {
        eprintln!("MCP server startup failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_normalization_matches_python() {
        assert_eq!(normalize_http_path(""), "/mcp");
        assert_eq!(normalize_http_path("/mcp"), "/mcp");
        assert_eq!(normalize_http_path("mcp"), "/mcp");
        assert_eq!(normalize_http_path("  /mcp  "), "/mcp");
    }
}
