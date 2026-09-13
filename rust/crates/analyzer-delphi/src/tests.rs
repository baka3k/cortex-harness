//! Golden tests — đối chiếu từng chữ với output của
//! `code-tiny/tools/delphi/delphi_analyzer.py` chạy trên corpus
//! `tests/fixtures/delphi-analyzer/` (sinh bởi golden_parse.json; xem
//! reports/phase07-delphi-parity.md). Bao gồm: scan order, parse meta
//! (grammar pin: ranges rỗng + has_error/ERROR counts), bảng functions/
//! types/fields/uses/relations, và `resolve_calls` (callee_id từng call).

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::path::PathBuf;
    use std::sync::OnceLock;

    use crate::danalyzer::stable_point_id;
    use crate::dparse::{
        count_signature_arity, merge_line_ranges, normalize_call_name, normalize_type_name,
        scan_delphi_files, strip_comments_and_strings,
    };
    use crate::resolve;

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
    }

    fn fixtures_root() -> PathBuf {
        repo_root().join("tests/fixtures/delphi-analyzer")
    }

    fn golden() -> &'static serde_json::Value {
        static GOLDEN: OnceLock<serde_json::Value> = OnceLock::new();
        GOLDEN.get_or_init(|| {
            serde_json::from_str(include_str!("golden_parse.json")).expect("golden json")
        })
    }

    fn parse_all() -> BTreeMap<String, crate::dparse::ParsedFile> {
        let root = fixtures_root();
        let mut out = BTreeMap::new();
        for abs in scan_delphi_files(&root) {
            let rel = cortex_analyzer_framework::scan::rel_posix(&root, std::path::Path::new(&abs));
            let parsed = crate::dparse::parse_delphi_file(std::path::Path::new(&abs), &root)
                .expect("parse");
            out.insert(rel, parsed);
        }
        out
    }

    #[test]
    fn scan_matches_golden_order() {
        let expected: Vec<&str> = golden()["scan"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        let root = fixtures_root();
        let got = scan_delphi_files(&root)
            .iter()
            .map(|p| {
                cortex_analyzer_framework::scan::rel_posix(&root, std::path::Path::new(p))
            })
            .collect::<Vec<_>>();
        assert_eq!(got, expected);
    }

    #[test]
    fn parse_tables_match_golden() {
        let parsed = parse_all();
        for file in golden()["files"].as_array().unwrap() {
            let rel = file["rel"].as_str().unwrap();
            let p = parsed.get(rel).unwrap_or_else(|| panic!("missing {rel}"));

            // uses
            let uses: Vec<String> = p.uses_units.clone();
            let expected_uses: Vec<String> = file["uses"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_string())
                .collect();
            assert_eq!(uses, expected_uses, "uses of {rel}");

            // parse_meta — grammar pin: pascal Isopod ranges rỗng như Python.
            let meta = &file["meta"];
            assert_eq!(p.parse_meta.parser_language, meta["parser_language"].as_str().unwrap());
            assert_eq!(p.parse_meta.parser_available, meta["parser_available"].as_bool().unwrap());
            assert_eq!(p.parse_meta.has_error, meta["has_error"].as_bool().unwrap(), "has_error {rel}");
            assert_eq!(
                p.parse_meta.error_nodes,
                meta["error_nodes"].as_u64().unwrap() as usize,
                "error_nodes {rel}"
            );
            assert!(!p.parse_meta.range_guided_by_tree, "range_guided {rel}");
            assert!(p.parse_meta.interface_ranges.is_empty(), "interface ranges {rel}");
            assert!(p.parse_meta.implementation_ranges.is_empty(), "implementation ranges {rel}");

            // functions — full table theo thứ tự parse.
            let got_fns: Vec<(String, String, usize, usize, usize, Option<String>)> = p
                .functions
                .iter()
                .map(|f| {
                    (
                        f.symbol_id.clone(),
                        f.kind.clone(),
                        f.start_line,
                        f.end_line,
                        f.arity,
                        f.scope_name.clone(),
                    )
                })
                .collect();
            let exp_fns: Vec<(String, String, usize, usize, usize, Option<String>)> = file["functions"]
                .as_array()
                .unwrap()
                .iter()
                .map(|f| {
                    (
                        f["symbol_id"].as_str().unwrap().to_string(),
                        f["kind"].as_str().unwrap().to_string(),
                        f["start_line"].as_u64().unwrap() as usize,
                        f["end_line"].as_u64().unwrap() as usize,
                        f["arity"].as_u64().unwrap() as usize,
                        f["scope_name"]
                            .as_str()
                            .map(str::to_string),
                    )
                })
                .collect();
            assert_eq!(got_fns, exp_fns, "functions of {rel}");

            // calls (chưa resolve).
            let got_calls: Vec<(usize, String, String, usize)> = p
                .calls
                .iter()
                .map(|c| (c.call_line, c.callee_raw.clone(), c.callee_name.clone(), c.call_arity))
                .collect();
            let exp_calls: Vec<(usize, String, String, usize)> = file["calls"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| {
                    (
                        c["call_line"].as_u64().unwrap() as usize,
                        c["callee_raw"].as_str().unwrap().to_string(),
                        c["callee_name"].as_str().unwrap().to_string(),
                        c["call_arity"].as_u64().unwrap() as usize,
                    )
                })
                .collect();
            assert_eq!(got_calls, exp_calls, "calls of {rel}");

            // types.
            let got_types: Vec<(String, String, usize, usize)> = p
                .types
                .iter()
                .map(|t| {
                    (
                        t.symbol_id.clone(),
                        t.kind.clone(),
                        t.start_line,
                        t.end_line,
                    )
                })
                .collect();
            let exp_types: Vec<(String, String, usize, usize)> = file["types"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| {
                    (
                        t["symbol_id"].as_str().unwrap().to_string(),
                        t["kind"].as_str().unwrap().to_string(),
                        t["start_line"].as_u64().unwrap() as usize,
                        t["end_line"].as_u64().unwrap() as usize,
                    )
                })
                .collect();
            assert_eq!(got_types, exp_types, "types of {rel}");

            // fields.
            let got_fields: Vec<(String, String)> = p
                .fields
                .iter()
                .map(|f| (f.symbol_id.clone(), f.type_signature.clone()))
                .collect();
            let exp_fields: Vec<(String, String)> = file["fields"]
                .as_array()
                .unwrap()
                .iter()
                .map(|f| {
                    (
                        f["symbol_id"].as_str().unwrap().to_string(),
                        f["type_signature"].as_str().unwrap().to_string(),
                    )
                })
                .collect();
            assert_eq!(got_fields, exp_fields, "fields of {rel}");

            // relations (source, target, rel_type) — multiset.
            let mut got_rels: Vec<(String, String, String)> = p
                .relations
                .iter()
                .map(|r| (r.source_id.clone(), r.target_id.clone(), r.rel_type.clone()))
                .collect();
            let mut exp_rels: Vec<(String, String, String)> = file["relations"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| {
                    (
                        r["source_id"].as_str().unwrap().to_string(),
                        r["target_id"].as_str().unwrap().to_string(),
                        r["rel_type"].as_str().unwrap().to_string(),
                    )
                })
                .collect();
            got_rels.sort();
            exp_rels.sort();
            assert_eq!(got_rels, exp_rels, "relations of {rel}");
        }
    }

    #[test]
    fn resolution_matches_golden() {
        let parsed = parse_all();
        let root = fixtures_root();

        let mut function_defs: Vec<crate::dparse::FunctionDef> = Vec::new();
        let mut calls: Vec<crate::dparse::CallEdge> = Vec::new();
        let mut uses_by_file: std::collections::HashMap<String, Vec<String>> =
            std::collections::HashMap::new();
        for (rel, p) in &parsed {
            function_defs.extend(p.functions.iter().cloned());
            calls.extend(p.calls.iter().cloned());
            uses_by_file.insert(rel.clone(), p.uses_units.clone());
        }

        // Uses index + resolve (kết quả đã golden từ Python).
        let all_scanned: Vec<String> = scan_delphi_files(&root);
        let (unit_by_file, _) = resolve::collect_unit_and_uses_index(&all_scanned, &root);
        let resolved_uses = resolve::resolve_uses_by_file(&uses_by_file, &unit_by_file, &all_scanned, &root);
        let files: Vec<String> = uses_by_file.keys().cloned().collect();
        let closure = resolve::build_uses_closure_by_file(&files, &resolved_uses);

        // golden uses_resolved
        for (fp, deps) in golden()["uses_resolved"].as_object().unwrap() {
            let got = resolved_uses.get(fp).cloned().unwrap_or_default();
            let mut got_sorted = got.clone();
            got_sorted.sort();
            let exp: Vec<String> = deps
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_string())
                .collect();
            assert_eq!(got_sorted, exp, "resolved uses of {fp}");
        }

        resolve::resolve_calls(&function_defs, &mut calls, &closure);

        let expected: Vec<(String, usize, String, Option<String>)> = golden()["resolution"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| {
                (
                    r["caller_id"].as_str().unwrap().to_string(),
                    r["call_line"].as_u64().unwrap() as usize,
                    r["callee_raw"].as_str().unwrap().to_string(),
                    r["callee_id"].as_str().map(str::to_string),
                )
            })
            .collect();
        let got: Vec<(String, usize, String, Option<String>)> = calls
            .iter()
            .map(|c| {
                (
                    c.caller_id.clone(),
                    c.call_line,
                    c.callee_raw.clone(),
                    c.callee_id.clone(),
                )
            })
            .collect();
        assert_eq!(got, expected);
    }

    #[test]
    fn stable_point_id_matches_python_uuid5() {
        let fixture = &golden()["stable_point_id_fixture"];
        let got = stable_point_id(fixture["input"].as_str().unwrap());
        assert_eq!(got, fixture["output"].as_str().unwrap());
    }

    // ── Helper units ────────────────────────────────────────────────────────

    #[test]
    fn strip_comments_keeps_offsets() {
        let text = "var\n  S: string;\n{ comment\nspans }begin\n  S := 'a;b'; // tail\nend.";
        let masked = strip_comments_and_strings(text);
        assert_eq!(masked.len(), text.len());
        // Strings/comments bị mask thành spaces, offsets không trôi.
        assert!(masked.contains("S :=      ;"));
        assert!(masked.contains("      begin"));
        // `begin`/`end` giữ nguyên ngoài comment/string.
        assert!(masked.ends_with("end."));
    }

    #[test]
    fn merge_ranges_merges_adjacent() {
        assert_eq!(merge_line_ranges(vec![(1, 3), (4, 6), (10, 12)]), vec![(1, 6), (10, 12)]);
        assert_eq!(merge_line_ranges(vec![(5, 2)]), vec![(2, 5)]);
        assert!(merge_line_ranges(vec![]).is_empty());
    }

    #[test]
    fn signature_arity_counts_groups() {
        assert_eq!(count_signature_arity(""), 0);
        assert_eq!(count_signature_arity("(A, B: Integer)"), 2);
        assert_eq!(count_signature_arity("(const S: string; var X: Integer)"), 2);
        assert_eq!(count_signature_arity("(A: Integer = 3; B: Double = 0)"), 2);
        assert_eq!(count_signature_arity("()"), 0);
    }

    #[test]
    fn type_name_normalization() {
        assert_eq!(normalize_type_name("^TShape"), Some("TShape".into()));
        assert_eq!(normalize_type_name("array of string"), Some("string".into()));
        assert_eq!(normalize_type_name("Shapes.TPoint"), Some("TPoint".into()));
        assert_eq!(normalize_type_name("const Integer"), Some("Integer".into()));
        assert_eq!(normalize_type_name("   "), None);
    }

    #[test]
    fn call_name_normalization() {
        assert_eq!(normalize_call_name("Self.Step"), "Step");
        assert_eq!(normalize_call_name("inherited Create"), "Create");
        assert_eq!(normalize_call_name("Math.Sqr"), "Sqr");
        assert_eq!(normalize_call_name(" Foo "), "Foo");
    }
}
