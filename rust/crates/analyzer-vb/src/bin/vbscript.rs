//! `analyzer-vbscript` — mirror `code-tiny/tools/vb/vbscript_analyzer.py`
//! (inject `--dialect vbscript` rồi chạy `vb_analyzer_base.main`).

fn main() {
    std::process::exit(analyzer_vb::run_binary("vbscript"));
}
