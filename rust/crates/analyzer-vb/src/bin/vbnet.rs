//! `analyzer-vbnet` — mirror `code-tiny/tools/vb/vbnet_analyzer.py`
//! (inject `--dialect vbnet` rồi chạy `vb_analyzer_base.main`).

fn main() {
    std::process::exit(analyzer_vb::run_binary("vbnet"));
}
