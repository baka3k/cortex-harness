//! Call edge extraction — port of `_iter_calls` + `_extract_call_info`.

use tree_sitter::Node;

use crate::symbols::{
    call_arity as compute_call_arity, extract_call_info, CallEdge,
};
use crate::walker::WalkContext;

/// Iterate all `call_expression` nodes under `node`, in source order.
pub fn iter_calls<'a>(node: Node<'a>) -> Vec<Node<'a>> {
    let mut out = Vec::new();
    find_calls(node, &mut out);
    out.sort_by_key(|n| n.start_byte());
    out
}

fn find_calls<'a>(node: Node<'a>, out: &mut Vec<Node<'a>>) {
    if node.kind() == "call_expression" {
        out.push(node);
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        find_calls(child, out);
    }
}

/// Extract every call inside a function-like node, attaching CallEdge records
/// to the WalkContext with the given caller_id / scope.
pub fn extract_calls_in_node<'a>(
    ctx: &mut WalkContext,
    func_node: Node<'a>,
    caller_id: &str,
    caller_scope: Option<&str>,
) {
    let calls = iter_calls(func_node);
    for call_node in calls {
        let (callee, call_type) = extract_call_info(call_node, ctx.source);
        let Some(callee) = callee else { continue };
        let line = call_node.start_position().row as u32 + 1;
        let col = call_node.start_position().column as u32 + 1;
        let start_byte = call_node.start_byte() as u32;
        let (branch_kind, loop_depth, frames_json) = collect_control_context(call_node);
        let arity = compute_call_arity(call_node);
        ctx.calls.push(CallEdge {
            caller_id: caller_id.to_string(),
            caller_file: ctx.rel_path.to_string(),
            caller_scope: caller_scope.map(|s| s.to_string()),
            call_line: line,
            call_column: col,
            call_start_byte: start_byte,
            call_branch_kind: branch_kind,
            call_loop_depth: loop_depth,
            call_control_frames_json: frames_json,
            call_type,
            call_arity: arity,
            callee_name: callee,
            callee_id: None,
        });
    }
}

/// Best-effort branch/loop inference. The Python analyzer tracks
/// `_collect_call_control_context`; we approximate with a simple frame
/// stack capturing the nearest enclosing if/for/while/switch node kinds.
fn collect_control_context(call_node: Node) -> (String, u32, String) {
    let mut branch_kind = String::new();
    let mut loop_depth: u32 = 0;
    let mut frames: Vec<String> = Vec::new();
    let mut current = call_node.parent();
    while let Some(node) = current {
        match node.kind() {
            "if_statement" | "else_clause" => {
                if branch_kind.is_empty() {
                    branch_kind = node.kind().to_string();
                }
                frames.push(node.kind().to_string());
            }
            "for_statement" | "while_statement" | "do_statement" | "for_range_loop" => {
                loop_depth += 1;
                frames.push(node.kind().to_string());
            }
            "switch_statement" | "case_statement" => {
                if branch_kind.is_empty() {
                    branch_kind = node.kind().to_string();
                }
                frames.push(node.kind().to_string());
            }
            "try_statement" | "catch_clause" => {
                frames.push(node.kind().to_string());
            }
            _ => {}
        }
        current = node.parent();
    }
    let frames_json = serde_json::json!(frames).to_string();
    (branch_kind, loop_depth, frames_json)
}
