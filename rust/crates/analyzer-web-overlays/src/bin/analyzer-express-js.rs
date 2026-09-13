//! analyzer-express-js — Rust port của
//! `tools/web_framework/web_framework_analyzer.py --framework express_js`.

fn main() {
    std::process::exit(analyzer_web_overlays::web_cli::run(
        analyzer_web_overlays::web_cli::WebFramework::ExpressJs,
    ));
}
