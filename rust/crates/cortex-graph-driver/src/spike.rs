//! Spike phase 06 — LadybugDB qua crate `lbug` (pin 0.20.4, cùng version
//! PyPI `ladybug` mà Python side dùng). Mục tiêu: chứng minh cùng engine +
//! cùng store format giữa 2 ngôn ngữ, phục vụ decision record về biên tích hợp.

use std::path::{Path, PathBuf};

use lbug::{Connection, Database, SystemConfig};

/// Mở store trong scope — `Connection<'a>` mượn `Database` nên cả hai phải
/// sống trong cùng closure (không thể trả struct tự-tham-chiếu).
pub fn with_store<T>(
    store_path: &Path,
    f: impl FnOnce(&Connection) -> Result<T, String>,
) -> Result<T, String> {
    let database = Database::new(store_path, SystemConfig::default())
        .map_err(|e| format!("cannot open ladybug store {store_path:?}: {e}"))?;
    let connection = Connection::new(&database).map_err(|e| format!("cannot connect: {e}"))?;
    f(&connection)
}

/// Bootstrap schema tối giản cho spike.
///
/// **Dialect finding 0.20.4 (đã verify CẢ HAI engine nhất quán):**
/// `IF NOT EXISTS` chỉ hợp lệ ở vị trí sau tên bảng (`CREATE NODE TABLE IF
/// NOT EXISTS <name> (...)` — cú pháp kuzu); đặt ở CUỐI statement bị parser
/// từ chối. Python `LadybugDriver` (line 512) đang dùng đúng vị trí sau tên
/// bảng → side Python không cần thay đổi.
pub fn write_bootstrap(conn: &Connection) -> Result<(), String> {
    conn.query("CREATE NODE TABLE IF NOT EXISTS File (id STRING, name STRING, PRIMARY KEY(id))")
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Insert một node File — trả number of tuples written.
pub fn write_file_node(conn: &Connection, id: &str, name: &str) -> Result<u64, String> {
    let statement = format!(
        "CREATE (f:File {{id: '{id}', name: '{name}'}}) RETURN count(f)"
    );
    let result = conn.query(&statement).map_err(|e| e.to_string())?;
    Ok(result.get_num_tuples())
}

/// Đọc toàn bộ File.id, sort theo alphabet.
pub fn read_file_ids(conn: &Connection) -> Result<Vec<String>, String> {
    let result = conn
        .query("MATCH (f:File) RETURN f.id ORDER BY f.id")
        .map_err(|e| e.to_string())?;
    let mut ids = Vec::new();
    for row in result {
        let value = row
            .first()
            .ok_or_else(|| "empty flat tuple".to_string())?;
        match value {
            lbug::Value::String(s) => ids.push(s.clone()),
            other => ids.push(format!("{other}")),
        }
    }
    Ok(ids)
}

/// Sao chép store file (giữ fixture nguyên vẹn cho mỗi test).
/// Ladybug 0.20.4 store là FILE (+ optional `.wal`).
pub fn copy_store(source: &Path, destination: &Path) -> std::io::Result<()> {
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent)?;
    }
    for suffix in ["", ".wal"] {
        let mut from = source.as_os_str().to_owned();
        from.push(suffix);
        let source_path = Path::new(&from);
        if !source_path.exists() {
            continue;
        }
        let mut to = destination.as_os_str().to_owned();
        to.push(suffix);
        std::fs::copy(source_path, Path::new(&to))?;
    }
    Ok(())
}

/// Đường dẫn helper cho tests.
pub fn fixture_store() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/ladybug_python_store")
}
