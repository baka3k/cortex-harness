//! Real-world smoke: inspect một journal DB bất kỳ, in JSON summaries.
//! Usage: cargo run -p cortex-graph-core --example inspect_journal -- <db-path>

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 2 {
        eprintln!("usage: inspect_journal <db-path>");
        std::process::exit(2);
    }
    let now_epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs_f64();
    match cortex_graph_core::journal::inspect_journal(std::path::Path::new(&args[1]), now_epoch) {
        Ok(summaries) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&summaries).expect("serialize")
            );
        }
        Err(error) => {
            eprintln!("error: {error}");
            std::process::exit(1);
        }
    }
}
