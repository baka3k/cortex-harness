//! analyzer-aspnet-framework — Rust port của
//! `tools/aspnet_framework/aspnet_framework_analyzer.py`.

fn main() {
    std::process::exit(analyzer_web_overlays::aspnet_cli::run(
        "aspnet_framework",
        |root, project_id, project_name, semantic, deleted, selected, worker, verbose| {
            analyzer_web_overlays::aspnet::framework_overlay::run_aspnet_framework_analysis(
                root,
                project_id,
                project_name,
                semantic,
                deleted,
                selected,
                worker,
                verbose,
            )
        },
    ));
}
