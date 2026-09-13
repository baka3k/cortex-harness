//! `analyzer-vba` — mirror `code-tiny/tools/vb/vba_analyzer.py`
//! (inject `--dialect vba` rồi chạy `vb_analyzer_base.main`).

fn main() {
    std::process::exit(analyzer_vb::run_binary("vba"));
}
