//! cortex-dev — Rust port of `cortex_harness/dev.py` (unified dev CLI).
//!
//! Phase 10 Scope A of plans/260913-2130-rust-full-migration. Command names,
//! option surfaces, output formats, and defaults are pinned to the Python
//! reference; heavy operations shell out to the same Python toolchain.

mod cmds;
mod config;
mod db_transfer;
mod ensure_ort;
mod env;
mod help;
mod journalx;
mod mcp_state;
mod parser;
mod procinfo;
mod spec;
mod tree;
mod util;

use parser::{ParseOutcome, Level};
use std::io::Write;

fn main() {
    // Parity-harness hook (not part of the CLI surface): dump the native
    // env payload the way the retired bridge ops did, so
    // scripts/rust_parity/dev_cli_parity.py can diff Python vs Rust per key.
    if let Ok(role) = std::env::var("CORTEX_DEV_PARITY_ENV") {
        match role.as_str() {
            "code" | "doc" | "status" => {
                let project_dir = std::path::PathBuf::from(
                    std::env::var("CORTEX_DEV_PARITY_PROJECT").unwrap_or_else(|_| ".".to_string()),
                );
                let payload = match role.as_str() {
                    "code" => env::code_env(&project_dir),
                    "doc" => env::doc_env(&project_dir),
                    _ => env::status_env_payload(&project_dir),
                };
                println!("{}", sorted_json(&payload));
                let _ = std::io::stdout().flush();
                return;
            }
            // Phase-02 parity probe: embedded-FalkorDB discovery + stop
            // against an arbitrary db path (`CORTEX_DEV_PARITY_DB`).
            "procinfo" => {
                let db_path = std::path::PathBuf::from(
                    std::env::var("CORTEX_DEV_PARITY_DB").unwrap_or_default(),
                );
                let before = procinfo::embedded_falkordb_pids(&db_path, None);
                let stopped = procinfo::stop_embedded_falkordb(&db_path, 5.0);
                let after = procinfo::embedded_falkordb_pids(&db_path, None);
                println!(
                    "{}",
                    sorted_json(&serde_json::json!({
                        "before": before,
                        "stopped": stopped,
                        "after": after,
                    }))
                );
                let _ = std::io::stdout().flush();
                return;
            }
            // Debug probe: dump the process table + code-worker matches.
            "table" => {
                let table = procinfo::process_table();
                let matched = procinfo::sync_processes("code", &util::repo_root(), &table, &[], true);
                println!(
                    "{}",
                    sorted_json(&serde_json::json!({
                        "table_rows": table.len(),
                        "matched": matched.iter().map(|r| serde_json::json!({
                            "pid": r.pid,
                            "argv": r.argv,
                        })).collect::<Vec<_>>(),
                    }))
                );
                let _ = std::io::stdout().flush();
                return;
            }
            // Phase-07 synthetic-table probe: run embedded-FalkorDB discovery
            // against a fabricated process record (sandbox-safe — no live
            // child needed). `CORTEX_DEV_PARITY_ARGV` is '|'-separated.
            "procinfo_synth" => {
                let db_path = std::path::PathBuf::from(
                    std::env::var("CORTEX_DEV_PARITY_DB").unwrap_or_default(),
                );
                let argv: Vec<String> = std::env::var("CORTEX_DEV_PARITY_ARGV")
                    .unwrap_or_default()
                    .split('|')
                    .map(|s| s.to_string())
                    .collect();
                let record = procinfo::ProcessRecord { pid: 9999, ppid: 1, argv };
                let mut table = std::collections::BTreeMap::new();
                table.insert(9999i64, record);
                let pids = procinfo::embedded_falkordb_pids(&db_path, Some(&table));
                println!(
                    "{}",
                    sorted_json(&serde_json::json!({ "pids": pids }))
                );
                let _ = std::io::stdout().flush();
                return;
            }
            // Phase-02 parity probe: `_mcp_pids` discovery (`CORTEX_DEV_PARITY_PATTERN`).
            "mcp_pids" => {
                let pattern =
                    std::env::var("CORTEX_DEV_PARITY_PATTERN").unwrap_or_default();
                println!(
                    "{}",
                    sorted_json(&serde_json::json!({
                        "pids": procinfo::mcp_pids(&pattern, None),
                    }))
                );
                let _ = std::io::stdout().flush();
                return;
            }
            _ => {}
        }
    }

    let argv: Vec<String> = std::env::args().skip(1).collect();
    let prog = "dev";

    let outcome = match parser::parse(&tree::ROOT, &argv, prog) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("{}", e.message);
            eprintln!("Try '{} --help' for help.", prog);
            std::process::exit(e.code);
        }
    };

    match outcome {
        ParseOutcome::Help { path, code } => {
            print!("{}", help::render_help(&path, prog, &dynamic_defaults));
            let _ = std::io::stdout().flush();
            std::process::exit(code);
        }
        ParseOutcome::Run(levels) => dispatch(&levels),
    }
}

/// Dynamic help defaults: repo-root paths and CPU-derived workers.
fn dynamic_defaults(canonical: &str) -> Option<String> {
    match canonical {
        "--legacy-root" => Some(util::repo_root().to_string_lossy().to_string()),
        "--parse-quality-workers" => Some(cmds::sync::default_workers().to_string()),
        _ => None,
    }
}

/// Sort object keys recursively so parity diffs are byte-stable
/// (`json.dumps(..., sort_keys=True)` equivalent).
fn sorted_json(v: &serde_json::Value) -> String {
    fn sort(value: &serde_json::Value) -> serde_json::Value {
        match value {
            serde_json::Value::Object(map) => {
                let mut keys: Vec<&String> = map.keys().collect();
                keys.sort();
                let mut out = serde_json::Map::new();
                for k in keys {
                    out.insert(k.clone(), sort(&map[k]));
                }
                serde_json::Value::Object(out)
            }
            serde_json::Value::Array(items) => {
                serde_json::Value::Array(items.iter().map(sort).collect())
            }
            other => other.clone(),
        }
    }
    serde_json::to_string(&sort(v)).unwrap_or_else(|_| "{}".to_string())
}

/// Group-option fallback for sync subcommands (see `Matches::merged`).
fn merged_with_group(levels: &[Level<'static>]) -> parser::Matches {
    let group = &levels[levels.len() - 2].matches;
    let child = &levels[levels.len() - 1].matches;
    parser::Matches::merged(group, child)
}

fn dispatch(levels: &[Level<'static>]) {
    let names: Vec<&str> = levels.iter().map(|l| l.cmd.name).collect();
    let cur = levels.last().unwrap();
    let m = &cur.matches;

    match names.as_slice() {
        ["dev"] => unreachable!("bare root handled by parser"),
        ["dev", "help"] => cmds::lifecycle::lifecycle_help(),
        ["dev", "build"] => cmds::lifecycle::build(),
        ["dev", "install"] => cmds::lifecycle::install(),
        ["dev", "uninstall"] => cmds::lifecycle::uninstall(),
        ["dev", "infra-up"] => cmds::lifecycle::infra_up(m),
        ["dev", "infra-down"] => cmds::lifecycle::infra_down(),
        ["dev", "storage-init"] => cmds::lifecycle::storage_init(),
        ["dev", "storage-layout"] => cmds::lifecycle::storage_layout(),
        ["dev", "storage-migrate-layout"] => cmds::lifecycle::storage_migrate_layout(m),
        ["dev", "storage-backup"] => cmds::lifecycle::storage_backup(m),
        ["dev", "storage-stop"] => cmds::lifecycle::storage_stop(),
        ["dev", "start"] => cmds::lifecycle::start(m),
        ["dev", "stop"] => {
            // Rust-backend MCP processes (cortex-mcp) không match pattern
            // python của lifecycle stop — dọn trước, rồi lifecycle như cũ.
            cmds::mcp::stop_rust_mcp(m.value("--name"));
            cmds::lifecycle::stop(m)
        }
        ["dev", "doctor"] => cmds::lifecycle::doctor(),
        ["dev", "mcp-gates"] => cmds::lifecycle::mcp_gates(),
        ["dev", "init"] => cmds::init::run(m),
        ["dev", "status"] => cmds::status::run(m),
        ["dev", "journal"] => unreachable!("bare journal group handled by parser"),
        ["dev", "journal", "status"] => cmds::journal::status(m),
        ["dev", "journal", "purge"] => cmds::journal::purge(m),
        ["dev", "sync"] => unreachable!("bare sync group handled by parser"),
        ["dev", "sync", "code"] => cmds::sync::sync_code(m),
        ["dev", "sync", "code", "all"] => {
            let m = merged_with_group(levels);
            cmds::sync::sync_code_all(&m)
        }
        ["dev", "sync", "code", "stop"] => {
            let m = merged_with_group(levels);
            cmds::sync::sync_code_stop(&m)
        }
        ["dev", "sync", "code", "add"] => {
            let m = merged_with_group(levels);
            cmds::sync::sync_code_add(&m)
        }
        ["dev", "sync", "doc"] => cmds::docsync::sync_doc(m, false),
        ["dev", "sync", "doc", "all"] => {
            let m = merged_with_group(levels);
            cmds::docsync::sync_doc(&m, true)
        }
        ["dev", "sync", "doc", "stop"] => {
            let m = merged_with_group(levels);
            cmds::docsync::sync_doc_stop(&m)
        }
        ["dev", "sync", "doc", "add"] => {
            let m = merged_with_group(levels);
            cmds::sync::sync_doc_add(&m)
        }
        ["dev", "ignore"] => unreachable!("bare ignore group handled by parser"),
        ["dev", "ignore", "add"] => cmds::ignore::add(m),
        ["dev", "ignore", "remove"] => cmds::ignore::remove(m),
        ["dev", "ignore", "list"] => cmds::ignore::list(m),
        ["dev", "mcp"] => unreachable!("bare mcp group handled by parser"),
        ["dev", "mcp", "start"] => cmds::mcp::start(m),
        ["dev", "mcp", "add"] => cmds::mcp::add(m),
        ["dev", "migrate"] => cmds::migrate::run(m),
        ["dev", "ensure-ort"] => cmds::lifecycle::ensure_ort(m),
        ["dev", "harness"] => unreachable!("bare harness group handled by parser"),
        ["dev", "harness", "init"] => cmds::harness::init(m),
        ["dev", "harness", "status"] => cmds::harness::status(m),
        ["dev", "harness", "task"] => unreachable!("bare harness task handled by parser"),
        ["dev", "harness", "task", "list"] => cmds::harness::task_list(m),
        ["dev", "harness", "task", "add"] => cmds::harness::task_add(m),
        ["dev", "harness", "task", "show"] => cmds::harness::task_show(m),
        ["dev", "harness", "run"] => cmds::harness::run(m),
        ["dev", "harness", "context"] => cmds::harness::context(m),
        ["dev", "harness", "verify"] => cmds::harness::verify(m),
        ["dev", "installer"] => unreachable!("bare installer group handled by parser"),
        ["dev", "installer", "build"] => cmds::installer::build(m),
        ["dev", "installer", "install"] => cmds::installer::install(m),
        ["dev", "installer", "uninstall"] => cmds::installer::uninstall(m),
        ["dev", "export-db"] => cmds::db::export(m, None),
        ["dev", "import-db"] => cmds::db::import(m),
        ["dev", "export"] => {
            let positional = m.positionals().first().cloned();
            cmds::db::export(m, positional.as_deref())
        }
        ["dev", "import"] => cmds::db::import(m),
        other => {
            eprintln!("Error: unhandled command path: {:?}", other);
            std::process::exit(2);
        }
    }
}

// ---------------------------------------------------------------------------
// Tests: argparse-equivalent parsing + config resolution helpers.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::{parse, ParseOutcome};
    use crate::tree::ROOT;
    use serde_json::json;

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    fn run(levels: &[Level<'static>]) -> Vec<&'static str> {
        levels.iter().map(|l| l.cmd.name).collect()
    }

    #[test]
    fn parses_top_level_command() {
        let out = parse(&ROOT, &argv(&["status"]), "dev").unwrap();
        match out {
            ParseOutcome::Run(levels) => assert_eq!(run(&levels), ["dev", "status"]),
            _ => panic!("expected run"),
        }
    }

    #[test]
    fn parses_nested_sync_code_all() {
        // Group options must precede the subcommand token (Click semantics).
        let out = parse(&ROOT, &argv(&["sync", "code", "--dry-run", "all"]), "dev").unwrap();
        match out {
            ParseOutcome::Run(levels) => {
                assert_eq!(run(&levels), ["dev", "sync", "code", "all"]);
                let code_level = &levels[2];
                assert!(code_level.matches.flag("--dry-run"));
                assert_eq!(code_level.matches.value_or("--project-dir", "."), ".");
                assert_eq!(
                    code_level.matches.value_or("--sync-mode", "both"),
                    "both"
                );
            }
            _ => panic!("expected run"),
        }
    }

    #[test]
    fn group_options_must_precede_subcommand() {
        // `sync code --dry-run all` is valid; `sync code all --project-dir`
        // would hand the option to `all`, which has none.
        let out = parse(&ROOT, &argv(&["sync", "code", "--project-dir", "/tmp/x", "all"]), "dev");
        assert!(matches!(out, Ok(ParseOutcome::Run(_))));
        let out2 = parse(&ROOT, &argv(&["sync", "code", "all", "--project-dir", "/tmp/x"]), "dev");
        assert!(out2.is_err());
    }

    #[test]
    fn flag_pair_resolution() {
        let on = parse(&ROOT, &argv(&["sync", "code", "--verbose"]), "dev").unwrap();
        match on {
            ParseOutcome::Run(levels) => {
                assert!(levels[2].matches.flag("--verbose"));
            }
            _ => panic!(),
        }
        let off = parse(&ROOT, &argv(&["sync", "code", "--no-verbose"]), "dev").unwrap();
        match off {
            ParseOutcome::Run(levels) => {
                assert!(!levels[2].matches.flag("--verbose"));
            }
            _ => panic!(),
        }
    }

    #[test]
    fn option_alias_and_inline_value() {
        let out = parse(
            &ROOT,
            &argv(&["start", "--db=mydb", "--port=9999", "--provider", "neo4j"]),
            "dev",
        )
        .unwrap();
        match out {
            ParseOutcome::Run(levels) => {
                let m = &levels[1].matches;
                assert_eq!(m.value("--database"), Some("mydb"));
                assert_eq!(m.value("--port"), Some("9999"));
                assert_eq!(m.value("--provider"), Some("neo4j"));
            }
            _ => panic!(),
        }
    }

    #[test]
    fn rejects_invalid_choice() {
        let err = parse(&ROOT, &argv(&["storage-backup", "--owner", "both"]), "dev").unwrap_err();
        assert_eq!(err.code, 2);
        assert!(err.message.contains("is not one of"));
    }

    #[test]
    fn rejects_out_of_range_port() {
        let err = parse(&ROOT, &argv(&["start", "--port", "70000"]), "dev").unwrap_err();
        assert_eq!(err.code, 2);
    }

    #[test]
    fn unknown_option_and_command() {
        let err = parse(&ROOT, &argv(&["status", "--wat"]), "dev").unwrap_err();
        assert_eq!(err.code, 2);
        let err = parse(&ROOT, &argv(&["frobnicate"]), "dev").unwrap_err();
        assert_eq!(err.code, 2);
    }

    #[test]
    fn help_flag_everywhere() {
        for path in tree::all_paths() {
            let mut args: Vec<&str> = path.clone();
            args.push("--help");
            let out = parse(&ROOT, &argv(&args), "dev").unwrap();
            assert!(matches!(out, ParseOutcome::Help { code: 0, .. }), "path={:?}", path);
        }
    }

    #[test]
    fn bare_groups_request_help() {
        for group in [
            vec!["sync"],
            vec!["journal"],
            vec!["ignore"],
            vec!["mcp"],
            vec!["harness"],
            vec!["installer"],
        ] {
            let out = parse(&ROOT, &argv(&group), "dev").unwrap();
            assert!(
                matches!(out, ParseOutcome::Help { code: 2, .. }),
                "group={:?}",
                group
            );
        }
    }

    #[test]
    fn required_positional_and_option() {
        let err = parse(&ROOT, &argv(&["ignore", "add"]), "dev").unwrap_err();
        assert_eq!(err.code, 2);
        let err = parse(&ROOT, &argv(&["journal", "purge"]), "dev").unwrap_err();
        assert_eq!(err.code, 2);
        let ok = parse(
            &ROOT,
            &argv(&["journal", "purge", "--journal-path", "/x", "--run-id", "r", "--project-id", "p", "--root", "/y"]),
            "dev",
        )
        .unwrap();
        assert!(matches!(ok, ParseOutcome::Run(_)));
    }

    #[test]
    fn variadic_positionals() {
        let ok = parse(
            &ROOT,
            &argv(&["ignore", "add", "a", "b", "generated-*"]),
            "dev",
        )
        .unwrap();
        match ok {
            ParseOutcome::Run(levels) => {
                assert_eq!(levels[2].matches.positionals().len(), 3);
            }
            _ => panic!(),
        }
    }

    // ── config resolution ───────────────────────────────────────────────

    #[test]
    fn config_paths_and_active_semantics() {
        let tmp = std::env::temp_dir().join(format!("cortex-dev-test-{}", std::process::id()));
        let cfg_dir = tmp.join(".cortext-harness").join("config");
        std::fs::create_dir_all(&cfg_dir).unwrap();

        assert_eq!(
            crate::config::config_dir(&tmp),
            tmp.join(".cortext-harness").join("config")
        );
        assert_eq!(crate::config::config_path(&tmp, "dev"), cfg_dir.join("dev.json"));

        // Two configs; prod active → load_active_config picks prod.
        std::fs::write(
            cfg_dir.join("dev.json"),
            json!({"active": false, "project": {"code": "d"}}).to_string(),
        )
        .unwrap();
        std::fs::write(
            cfg_dir.join("prod.json"),
            json!({"active": true, "project": {"code": "p"}}).to_string(),
        )
        .unwrap();
        let (cfg, path) = crate::config::load_active_config(&tmp);
        assert_eq!(path.file_name().unwrap(), "prod.json");
        assert_eq!(cfg["project"]["code"], "p");

        // Nothing active → first sorted with warning.
        std::fs::write(cfg_dir.join("prod.json"), json!({"active": false}).to_string()).unwrap();
        let (_, path) = crate::config::load_active_config(&tmp);
        assert_eq!(path.file_name().unwrap(), "dev.json");

        std::fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn save_roundtrip_matches_python_json_layout() {
        let tmp = std::env::temp_dir().join(format!("cortex-dev-rt-{}", std::process::id()));
        let path = tmp.join("dev.json");
        let mut cfg = serde_json::Map::new();
        cfg.insert("active".to_string(), json!(true));
        cfg.insert("project".to_string(), json!({"code": "мой_проект", "name": "n"}));
        crate::config::save_config(&serde_json::Value::Object(cfg), &path);
        let text = std::fs::read_to_string(&path).unwrap();
        // indent=2, ensure_ascii=False, no trailing newline.
        assert!(text.starts_with("{\n  \"active\": true,\n  \"project\": {"));
        assert!(text.contains("мой_проект"));
        assert!(!text.ends_with('\n'));
        std::fs::remove_dir_all(&tmp).unwrap();
    }

    #[test]
    fn ignore_folders_dedupe_and_order() {
        let cfg = json!({"ignore": {"folders": ["a", "b", "a", "  ", 3, "b"]}});
        assert_eq!(
            crate::config::ignore_folders(&cfg),
            vec!["a".to_string(), "b".to_string()]
        );
        assert!(crate::config::ignore_folders(&json!({})).is_empty());
    }

    #[test]
    fn graph_provider_aliases() {
        let env = json!({});
        // POSIX default.
        assert_eq!(
            crate::config::graph_provider(&env, "CODE_GRAPH_PROVIDER").unwrap(),
            "falkordb"
        );
        assert_eq!(
            crate::config::graph_provider(&json!({"CODE_GRAPH_PROVIDER": "lbug"}), "CODE_GRAPH_PROVIDER").unwrap(),
            "ladybug"
        );
        assert_eq!(
            crate::config::graph_provider(&json!({"GRAPH_PROVIDER": "Falkor"}), "CODE_GRAPH_PROVIDER").unwrap(),
            "falkordb"
        );
        assert!(crate::config::graph_provider(&json!({"CODE_GRAPH_PROVIDER": "bogus"}), "CODE_GRAPH_PROVIDER").is_err());
    }

    #[test]
    fn source_projects_handles_legacy_flat_format() {
        let legacy = json!({"git": "https://x", "folder": ["a", "b"]});
        assert_eq!(crate::config::source_projects(&legacy).len(), 1);
        assert_eq!(crate::config::source_folders(&legacy), vec!["a", "b"]);
        let modern = json!({"projects": [
            {"git": "", "folder": ["a"]},
            {"git": "u", "folder": ["a", "c"]}
        ]});
        assert_eq!(crate::config::source_folders(&modern), vec!["a", "c"]);
    }

    #[test]
    fn md5_known_vectors() {
        assert_eq!(util::md5_hex(b""), "d41d8cd98f00b204e9800998ecf8427e");
        assert_eq!(util::md5_hex(b"abc"), "900150983cd24fb0d6963f7d28e17f72");
        assert_eq!(
            util::md5_hex(b"The quick brown fox jumps over the lazy dog"),
            "9e107d9d372bb6826bd81d3542a419d6"
        );
    }

    #[test]
    fn fnmatch_globs() {
        assert!(util::fnmatch_one("generated-foo", "generated-*"));
        assert!(util::fnmatch_one("node_modules", "node_modules"));
        assert!(util::fnmatch_one("a.env", "*.env"));
        assert!(!util::fnmatch_one(".env", ".env.*"));
        assert!(util::fnmatch_one(".env.local", ".env.*"));
        assert!(util::fnmatch_one("ab", "a?"));
        assert!(!util::fnmatch_one("abc", "a?"));
        assert!(util::fnmatch_one("buil", "bui[ld]"));
        assert!(util::fnmatch_one("buid", "bui[ld]"));
        assert!(!util::fnmatch_one("build", "bui[ld]"));
    }

    #[test]
    fn is_local_endpoints() {
        assert!(crate::config::is_local("localhost:6379"));
        assert!(crate::config::is_local("http://127.0.0.1:6333"));
        assert!(!crate::config::is_local("redis://remote.example.com:6379"));
        assert!(!crate::config::is_local(""));
    }

    #[test]
    fn state_key_is_md5_prefix() {
        // dev.py: md5(folder)[:12]
        let key = &util::md5_hex(b"src")[..12];
        assert_eq!(key.len(), 12);
        let p = util::state_path(std::path::Path::new("/proj"), "src");
        assert_eq!(
            p.file_name().unwrap().to_string_lossy(),
            format!("{}.json", key)
        );
    }

    #[test]
    fn workers_formula() {
        assert!(cmds::sync::default_workers() >= 1);
        assert!(cmds::sync::default_workers() <= 4);
    }

    #[test]
    fn shlex_basic() {
        assert_eq!(
            util::shlex_split(r#"python -m tools.sync "/path with space/x.py""#).unwrap(),
            vec!["python", "-m", "tools.sync", "/path with space/x.py"]
        );
    }
}
