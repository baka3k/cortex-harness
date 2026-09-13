//! Golden parse test — so giá trị sinh bởi `parse_c_family_file` Python
//! (tree-sitter-cpp 0.23.4) trên fixture nội tuyến.

use serde_json::{Map, Value};

fn parse_payload(source: &str, file_name: &str) -> analyzer_cplus::Payload {
    let dir = tempfile_dir();
    let path = dir.join(file_name);
    std::fs::write(&path, source).unwrap();
    analyzer_cplus::parse_file_for_test(&path, &dir, true)
}

fn tempfile_dir() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("analyzer-cplus-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn strings(rows: &[Map<String, Value>], key: &str) -> Vec<String> {
    rows.iter()
        .filter_map(|row| row.get(key).and_then(Value::as_str))
        .map(str::to_string)
        .collect()
}

#[test]
fn golden_probe_file() {
    let source = r#"#include "other.h"
using namespace outer;
namespace outer { int helper(int a); }
class Base { public: virtual int run(int x); };
class Derived : public Base {
public:
    int run(int x) override;
    int extra;
};
int Derived::run(int x) { return helper(x); }
typedef Base* BasePtr;
using AliasVec = Base;
template <typename T> T tplmax(T a, T b);
enum Color { RED, GREEN };
int main() {
    Derived d;
    return d.run(1);
}
"#;
    let payload = parse_payload(source, "t.cpp");
    // using_namespaces luôn rỗng (list-copy quirk của _walk_tree Python).
    assert!(payload.using_namespaces.is_empty());
    assert_eq!(payload.includes, vec!["other.h".to_string()]);
    let func_qnames = strings(&payload.functions, "qualified_name");
    assert_eq!(
        func_qnames,
        vec![
            "outer::helper",
            "Base::run",
            "Derived::run",
            "Derived::Derived",
            "main",
        ]
    );
    // symbol_id của outer::helper khớp Python golden.
    let helper = payload
        .functions
        .iter()
        .find(|f| f["qualified_name"] == "outer::helper")
        .unwrap();
    assert_eq!(
        helper["symbol_id"],
        "cplus-function-v2::outer::helper::b1c53f751d688d5534ff9605"
    );
    let type_qnames = strings(&payload.types, "qualified_name");
    assert_eq!(type_qnames, vec!["int", "Base", "Derived", "typedef", "using", "Color"]);
    let alias_names = strings(&payload.aliases, "name");
    assert_eq!(alias_names, vec!["Base", "AliasVec"]);
    assert_eq!(payload.templates.len(), 1);
    let rel_types: Vec<String> = payload
        .relations
        .iter()
        .filter_map(|r| r.get("rel_type").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    assert_eq!(
        rel_types,
        vec![
            "CONTAINS", "USES_TYPE", "DECLARES", "USES_TYPE", "EXTENDS", "DECLARES",
            "USES_TYPE", "DECLARES", "USES_TYPE", "DECLARES", "USES_TYPE", "ALIASES",
            "ALIASES",
        ]
    );
    let calls: Vec<(String, String)> = payload
        .calls
        .iter()
        .map(|c| {
            (
                c["callee_name"].as_str().unwrap().to_string(),
                c["call_type"].as_str().unwrap().to_string(),
            )
        })
        .collect();
    assert_eq!(calls, vec![("helper".to_string(), "call_expression".to_string()), ("run".to_string(), "member_call".to_string())]);
    let meta = &payload.parse_meta;
    assert_eq!(meta["has_error"], Value::Bool(false));
    assert_eq!(meta["error_nodes"], Value::from(0));
    assert_eq!(meta["quality_tier"], "clean");
    assert_eq!(meta["parser_language"], "cpp");
}
