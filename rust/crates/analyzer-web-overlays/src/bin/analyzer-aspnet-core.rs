//! analyzer-aspnet-core — Rust port của
//! `tools/aspnet_core/aspnet_core_analyzer.py`.

fn main() {
    std::process::exit(analyzer_web_overlays::aspnet_cli::run(
        "aspnet_core",
        |root, project_id, project_name, semantic, deleted, selected, worker, verbose| {
            analyzer_web_overlays::aspnet::core_overlay::run_aspnet_core_analysis(
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
