//! Portable Tree-sitter COBOL discovery and preflight — port
//! `code-tiny/tools/cobol/parser_runtime.py`.
//!
//! Python dùng `ctypes.CDLL` để load native grammar library export
//! `tree_sitter_cobol`, bọc pointer bằng `tree_sitter.Language(ptr)` và parse.
//! Rust mirror đúng chuỗi đó qua `libloading` + `tree_sitter_language::
//! LanguageFn::from_raw` — CÙNG file .so ⇒ CÙNG grammar ⇒ error/missing nodes
//! byte-identical (điều kiện cần cho `diagnostics=%d` của `[SCAN_RESULT]`).
//!
//! Khác biệt scope có chủ đích: Python có fallback `tree_sitter_language_pack`
//! (plane Python) khi không tìm thấy bundled library — Rust chỉ error
//! `COBOL_RUNTIME_UNAVAILABLE` (pack là extension module Python, không load
//! được từ Rust). `tree_sitter_version` trong RuntimeInfo là metadata artifact
//! (facts.json), không tác động graph/scan-plane.

use std::path::{Path, PathBuf};

use libloading::Library;
use tree_sitter::{Language, Parser};
use tree_sitter_language::LanguageFn;

pub const MINIMAL_PROGRAM: &[u8] =
    b"       IDENTIFICATION DIVISION.\n       PROGRAM-ID. PREFLIGHT.\n       PROCEDURE DIVISION.\n       STOP RUN.\n";

#[derive(Debug, Clone)]
pub struct CobolRuntimeError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for CobolRuntimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // Python: super().__init__(f"{code}: {message}")
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl CobolRuntimeError {
    pub fn new(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
        }
    }
}

/// `RuntimeInfo` dataclass — các trường platform/architecture/abi in ra ở
/// `--preflight`; library_path/sha256/tree_sitter_version chỉ vào facts.json.
#[derive(Debug, Clone)]
pub struct RuntimeInfo {
    pub provider: String,
    pub platform: String,
    pub architecture: String,
    pub tree_sitter_version: String,
    pub grammar_abi: usize,
    pub library_path: String,
    pub library_sha256: String,
}

impl RuntimeInfo {
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "provider": self.provider,
            "platform": self.platform,
            "architecture": self.architecture,
            "tree_sitter_version": self.tree_sitter_version,
            "grammar_abi": self.grammar_abi,
            "library_path": self.library_path,
            "library_sha256": self.library_sha256,
        })
    }
}

fn sha256_hex(path: &Path) -> Result<String, CobolRuntimeError> {
    use sha2::{Digest, Sha256};
    let data = std::fs::read(path)
        .map_err(|e| CobolRuntimeError::new("COBOL_RUNTIME_LOAD_FAILED", format!("could not read {}: {e}", path.display())))?;
    let digest = Sha256::digest(&data);
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        hex.push_str(&format!("{byte:02x}"));
    }
    Ok(hex)
}

/// `_bundled_candidates` — sorted files trong `lib/` kế bên module. Rust không
/// có module dir; probe cùng chuỗi: `<exe_dir>/lib` rồi
/// `<repo>/code-tiny/tools/cobol/lib` (repo layout khi binary nằm ở
/// `rust/target/{debug,release}/`).
fn bundled_candidates() -> Vec<PathBuf> {
    let mut dirs: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe()
        && let Some(parent) = exe.parent() {
            dirs.push(parent.join("lib"));
            // target/{debug,release} → target → rust → repo root
            let mut repo = parent.to_path_buf();
            for _ in 0..3 {
                repo.pop();
            }
            dirs.push(repo.join("code-tiny/tools/cobol/lib"));
        }
    let suffixes = [".dylib", ".so", ".dll"];
    let mut out: Vec<PathBuf> = Vec::new();
    for dir in dirs {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let is_lib = path
                .extension()
                .map(|ext| {
                    let ext = format!(".{}", ext.to_string_lossy().to_lowercase());
                    suffixes.contains(&ext.as_str())
                })
                .unwrap_or(false);
            if is_lib {
                out.push(path);
            }
        }
    }
    out.sort();
    out
}

/// `resolve_language_library` — override flag/env trước, bundled sau.
pub fn resolve_language_library(override_path: Option<&str>) -> Result<Option<PathBuf>, CobolRuntimeError> {
    let requested = override_path
        .map(str::to_string)
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var("COBOL_LANGUAGE_LIBRARY").ok().filter(|v| !v.is_empty()));
    if let Some(requested) = requested {
        let path = expanduser(&requested);
        let path = absolutize(&path);
        if !path.is_file() {
            return Err(CobolRuntimeError::new(
                "COBOL_RUNTIME_LIBRARY_NOT_FOUND",
                format!("grammar library not found: {}", path.display()),
            ));
        }
        return Ok(Some(path));
    }
    Ok(bundled_candidates().first().cloned())
}

/// Python `Path.expanduser()` — chỉ xử lý `~` đứng đầu.
fn expanduser(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/")
        && let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(rest);
        }
    if path == "~"
        && let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home);
        }
    PathBuf::from(path)
}

/// `Path.resolve()` cho library path — canonicalize nếu được, giữ nguyên nếu
/// chưa tồn tại (Python resolve(strict=False)).
fn absolutize(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(path)
        }
    })
}

/// Native grammar library đã load — `Library` PHẢI sống cùng đời parser
/// (language pointer trỏ vào static data của library).
pub struct LoadedParser {
    parser: Parser,
    _library: Library,
}

impl LoadedParser {
    pub fn parse_utf8(&mut self, bytes: &[u8]) -> Option<tree_sitter::Tree> {
        self.parser.parse(bytes, None)
    }
}

/// `_native_parser` — dlopen + `tree_sitter_cobol` symbol + bọc pointer.
pub fn load_native_parser(path: &Path) -> Result<(LoadedParser, RuntimeInfo), CobolRuntimeError> {
    // SAFETY: dlopen native grammar library, cùng cơ chế với ctypes.CDLL phía
    // Python reference; symbol `tree_sitter_cobol` có signature
    // `unsafe extern "C" fn() -> *const ()` (TSLanguage*).
    let library = unsafe { Library::new(path) }.map_err(|e| {
        CobolRuntimeError::new(
            "COBOL_RUNTIME_LOAD_FAILED",
            format!("could not load {}: {}", path.display(), e),
        )
    })?;
    // SAFETY: symbol layout khớp LanguageFn (extern "C" fn() -> pointer).
    let symbol: libloading::Symbol<unsafe extern "C" fn() -> *const ()> =
        unsafe { library.get(b"tree_sitter_cobol") }.map_err(|_| {
            CobolRuntimeError::new(
                "COBOL_RUNTIME_SYMBOL_MISSING",
                format!("{} does not export tree_sitter_cobol", path.display()),
            )
        })?;
    let func = *symbol;
    // SAFETY: gọi hàm trả TSLanguage* static — cần kiểm null như Python
    // (`if not pointer: raise COBOL_RUNTIME_NULL_LANGUAGE`).
    // SAFETY: cùng lý do LanguageFn::from_raw — hàm C thuần trả static ptr.
    let probe: *const () = unsafe { func() };
    if probe.is_null() {
        return Err(CobolRuntimeError::new(
            "COBOL_RUNTIME_NULL_LANGUAGE",
            format!("{} returned a null grammar pointer", path.display()),
        ));
    }
    // SAFETY: pointer trả về từ tree_sitter_cobol là &TSLanguage static.
    let language_fn = unsafe { LanguageFn::from_raw(func) };
    let language = Language::from(language_fn);
    let abi = language.abi_version();
    let mut parser = Parser::new();
    parser
        .set_language(&language)
        .map_err(|e| {
            CobolRuntimeError::new(
                "COBOL_RUNTIME_ABI_INCOMPATIBLE",
                format!("native grammar is incompatible with the tree-sitter binding: {e}"),
            )
        })?;
    let info = RuntimeInfo {
        provider: "native-library".to_string(),
        platform: std::env::consts::OS.to_string(),
        architecture: std::env::consts::ARCH.to_string(),
        tree_sitter_version: "unknown".to_string(),
        grammar_abi: abi,
        library_path: path.to_string_lossy().to_string(),
        library_sha256: sha256_hex(path)?,
    };
    Ok((LoadedParser { parser, _library: library }, info))
}

/// `load_parser` — native library là đường chính; không có fallback
/// language-pack (plane Python) ⇒ lỗi COBOL_RUNTIME_UNAVAILABLE.
pub fn load_parser(language_library: Option<&str>) -> Result<(LoadedParser, RuntimeInfo), CobolRuntimeError> {
    let requested = language_library
        .map(str::to_string)
        .filter(|value| !value.is_empty())
        .or_else(|| {
            std::env::var("COBOL_LANGUAGE_LIBRARY")
                .ok()
                .filter(|value| !value.is_empty())
        });
    let path = resolve_language_library(language_library)?;
    if let Some(path) = path {
        match load_native_parser(&path) {
            Ok((parser, info)) => return Ok((parser, info)),
            Err(error) => {
                if requested.is_some() {
                    return Err(error);
                }
                // Python rơi xuống language-pack fallback; Rust không có plane
                // đó — báo unavailable kèm mã lỗi bundled.
            }
        }
    }
    Err(CobolRuntimeError::new(
        "COBOL_RUNTIME_UNAVAILABLE",
        "install tree-sitter-language-pack or pass --cobol-language-library",
    ))
}

/// `preflight` — parse MINIMAL_PROGRAM, kiểm tra root node.
pub fn preflight(language_library: Option<&str>) -> Result<RuntimeInfo, CobolRuntimeError> {
    let (mut loaded, info) = load_parser(language_library)?;
    let tree = loaded.parse_utf8(MINIMAL_PROGRAM);
    let root_type = tree.as_ref().map(|tree| tree.root_node().kind().to_string());
    let named_child_count = tree.as_ref().map(|tree| tree.root_node().named_child_count());
    let has_error = tree.as_ref().map(|tree| tree.root_node().has_error());
    let ok = matches!(root_type.as_deref(), Some("start") | Some("source_file"))
        && named_child_count.unwrap_or(0) > 0
        && !has_error.unwrap_or(true);
    if !ok {
        return Err(CobolRuntimeError::new(
            "COBOL_RUNTIME_PARSE_FAILED",
            "minimal COBOL parse produced no program tree",
        ));
    }
    Ok(info)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundled_library() -> Option<PathBuf> {
        bundled_candidates().into_iter().next()
    }

    #[test]
    fn preflight_ok_with_bundled_library() {
        let Some(_lib) = bundled_library() else {
            eprintln!("skipped: no bundled cobol grammar library");
            return;
        };
        let info = preflight(None).expect("preflight");
        assert_eq!(info.provider, "native-library");
        assert_eq!(info.grammar_abi, 14);
    }

    #[test]
    fn explicit_missing_library_errors() {
        let err = resolve_language_library(Some("/nonexistent/cobol.so"))
            .expect_err("must fail");
        assert_eq!(err.code, "COBOL_RUNTIME_LIBRARY_NOT_FOUND");
    }
}
