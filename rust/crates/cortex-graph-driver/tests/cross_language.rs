//! Cross-language spike tests: Rust (`lbug` crate) mở store do Python
//! (`ladybug` PyPI) tạo — đọc + ghi; rồi export store đã ghi để Python
//! kiểm chứng (`scripts/rust_parity/check_ladybug_roundtrip.py`).

use cortex_graph_driver::spike::{
    copy_store, fixture_store, read_file_ids, with_store, write_bootstrap, write_file_node,
};
use std::path::PathBuf;

fn temp_store(name: &str) -> PathBuf {
    std::env::temp_dir()
        .join("cortex_graph_driver_spike")
        .join(name)
}

#[test]
fn rust_reads_python_store() {
    let store = temp_store("read");
    copy_store(&fixture_store(), &store).expect("copy fixture store");
    with_store(&store, |conn| {
        let ids = read_file_ids(conn)?;
        assert!(
            ids.contains(&"py-file-1".to_string()),
            "phải thấy node do Python ghi, got {ids:?}"
        );
        Ok(())
    })
    .expect("scoped read");
}

#[test]
fn rust_writes_into_python_store() {
    let store = temp_store("write");
    copy_store(&fixture_store(), &store).expect("copy fixture store");
    with_store(&store, |conn| {
        // Table `File` đã tồn tại từ fixture Python — không bootstrap lại
        // (ladybug 0.20.4 không có `IF NOT EXISTS`, xem spike.rs dialect note).
        let written = write_file_node(conn, "rust-file-1", "written-by-rust")
            .expect("ghi node từ Rust");
        assert_eq!(written, 1);

        let ids = read_file_ids(conn).expect("read ids sau khi ghi");
        assert_eq!(ids, vec!["py-file-1", "py-file-2", "rust-file-1"]);
        Ok(())
    })
    .expect("scoped write");

    // Export SAU khi đóng store — để Python kiểm chứng chiều ngược lại.
    if let Ok(export_path) = std::env::var("CORTEX_EXPORT_LADYBUG_STORE") {
        copy_store(&store, std::path::Path::new(&export_path))
            .expect("export ladybug store");
    }
}

/// Benchmark smoke — 500 point-reads qua lbug. Chạy `-- --nocapture` để xem số.
#[test]
fn benchmark_smoke_500_reads() {
    let store = temp_store("bench");
    copy_store(&fixture_store(), &store).expect("copy fixture store");
    with_store(&store, |conn| {
        write_bootstrap(conn).ok(); // store đã có table — ignore lỗi exists
        let start = std::time::Instant::now();
        const N: usize = 500;
        for _ in 0..N {
            let ids = read_file_ids(conn)?;
            assert!(!ids.is_empty());
        }
        let elapsed = start.elapsed();
        println!(
            "rust lbug: {N} reads (MATCH/ORDER) trong {elapsed:?} (~{:.3} ms/read)",
            elapsed.as_secs_f64() * 1000.0 / N as f64
        );
        Ok(())
    })
    .expect("bench");
}
