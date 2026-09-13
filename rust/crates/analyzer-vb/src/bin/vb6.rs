//! `analyzer-vb6` — mirror `code-tiny/tools/vb/vb6_analyzer.py`
//! (inject `--dialect vb6` rồi chạy `vb_analyzer_base.main`).

fn main() {
    std::process::exit(analyzer_vb::run_binary("vb6"));
}
