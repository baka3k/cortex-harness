//! analyzer-fastapi-django — Rust port của
//! `tools/web_framework/web_framework_analyzer.py --framework fastapi_django`.

fn main() {
    std::process::exit(analyzer_web_overlays::web_cli::run(
        analyzer_web_overlays::web_cli::WebFramework::FastapiDjango,
    ));
}
