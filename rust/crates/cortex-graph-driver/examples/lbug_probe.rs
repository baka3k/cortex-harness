//! Real-world smoke: đọc/ghi một LadybugDB store bất kỳ.
//! Usage:
//!   cargo run -p cortex-graph-driver --example lbug_probe -- read  <store>
//!   cargo run -p cortex-graph-driver --example lbug_probe -- write <store> <id> <name>

use cortex_graph_driver::spike::{read_file_ids, with_store, write_file_node};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: lbug_probe <read|write> <store> [id] [name]");
        std::process::exit(2);
    }
    let store = PathBuf::from(&args[2]);
    let mode = args[1].clone();
    let node_id = args.get(3).cloned();
    let node_name = args.get(4).cloned();
    let outcome = with_store(&store, move |conn| match mode.as_str() {
        "read" => read_file_ids(conn),
        "write" => {
            let (Some(id), Some(name)) = (node_id, node_name) else {
                return Err("usage: lbug_probe write <store> <id> <name>".to_string());
            };
            write_file_node(conn, &id, &name)?;
            read_file_ids(conn)
        }
        other => Err(format!("unknown mode: {other}")),
    });
    match outcome {
        Ok(ids) => println!("{}", serde_json::to_string(&ids).expect("serialize ids")),
        Err(error) => {
            eprintln!("error: {error}");
            std::process::exit(1);
        }
    }
}
