//! `ModelSpec`: mô hình embedding + đúng chuỗi env mà các lane Python đang đọc,
//! để backend Rust và sidecar Python resolve cùng một model (gate parity đòi hỏi
//! điều đó — model khác nhau thì không có số nào để so).
//!
//! Chuỗi env (bằng chứng: `code-tiny/tools/common/embed_runtime.py`,
//! `python_analyzer.py:_resolve_embedding_model_source`, `scripts/rust_mcp/embed_worker.py`):
//! - Code plane: `CODE_EMBEDDING_MODEL_PATH` → `CODE_EMBEDDING_MODEL` → `EMBED_MODEL`
//!   → `JINA_MODEL_PATH` → `jinaai/jina-embeddings-v3`
//! - Doc/mind plane: `EMBEDDING_MODEL_PATH` → `EMBEDDING_MODEL` → `BAAI/bge-m3`

use std::path::{Path, PathBuf};

use crate::error::{EmbedError, Result};

/// Nhừng model nào đang chạy — quyết định chuỗi env và số học pooling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plane {
    Code,
    Doc,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Pooling {
    /// jina-v3: `pooling_mode_mean_tokens=true`.
    Mean,
    /// bge-m3: `pooling_mode_cls_token=true`.
    Cls,
}

/// Mặc định theo config model đã verify trong HF snapshot (findings C1/C2).
pub const JINA_MAX_TOKENS: usize = 8194;
pub const BGE_MAX_TOKENS: usize = 8192;
/// Char bound của lane ingest: `MAX_EMBED_CHARS` (4000) rồi hard cap 16000
/// (`primary_vector_sync.py:44,92-96`). 0 = không bound.
pub const CODE_MAX_CHARS: usize = 4000;
pub const EMBEDDING_DIMENSION: usize = 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModelSpec {
    /// Tên model để log/fixture (`jinaai/jina-embeddings-v3`).
    pub id: String,
    /// File ONNX (graph + external data phải nằm cùng thư mục).
    pub graph: PathBuf,
    pub tokenizer_json: PathBuf,
    pub tokenizer_config: PathBuf,
    pub pooling: Pooling,
    pub normalize: bool,
    /// `model_max_length` của tokenizer — KHÔNG phải 512 (findings C1/C4).
    pub max_length: usize,
    /// Bound theo code point trước khi tokenize; 0 = off.
    pub max_chars: usize,
    /// Cắt whitespace hai đầu TRƯỚC khi tokenize.
    ///
    /// Lane đi qua `SentenceTransformer` (ingest code, explore query, worker
    /// bge-m3) strip text trong `Transformer.tokenize`; lane gọi thẳng
    /// `AutoModel.encode` (`embed_runtime.embed_query`) thì không. Đặt sai là
    /// token-id lệch, không phải lệch fp — nên nó thuộc spec, không thuộc caller.
    pub strip: bool,
    pub dimension: usize,
}

impl ModelSpec {
    /// Đường số học thật của code plane: mean-pool fp32 + L2-normalize, LoRA tắt.
    pub fn jina_v3(snapshot: &Path, graph: PathBuf) -> Self {
        Self {
            id: "jinaai/jina-embeddings-v3".to_string(),
            graph,
            tokenizer_json: snapshot.join("tokenizer.json"),
            tokenizer_config: snapshot.join("tokenizer_config.json"),
            pooling: Pooling::Mean,
            normalize: true,
            max_length: JINA_MAX_TOKENS,
            max_chars: CODE_MAX_CHARS,
            strip: true,
            dimension: EMBEDDING_DIMENSION,
        }
    }

    /// bge-m3: CLS-pool + L2-normalize (`modules.json` có `2_Normalize`).
    pub fn bge_m3(snapshot: &Path, graph: PathBuf) -> Self {
        Self {
            id: "BAAI/bge-m3".to_string(),
            graph,
            tokenizer_json: snapshot.join("tokenizer.json"),
            tokenizer_config: snapshot.join("tokenizer_config.json"),
            pooling: Pooling::Cls,
            normalize: true,
            max_length: BGE_MAX_TOKENS,
            max_chars: 0,
            strip: true,
            dimension: EMBEDDING_DIMENSION,
        }
    }

    /// Mode generic cho model thay thế qua `CODE_EMBEDDING_MODEL_PATH` khi model đó
    /// không có `.encode()` — tức nhánh `mean_pool` + `max_length=512` + không
    /// normalize trong `embed_runtime.py:183-199` / `python_analyzer.py:1011,1029`.
    pub fn legacy_unnormalized(snapshot: &Path, graph: PathBuf, dimension: usize) -> Self {
        Self {
            id: snapshot
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_else(|| "legacy".to_string()),
            graph,
            tokenizer_json: snapshot.join("tokenizer.json"),
            tokenizer_config: snapshot.join("tokenizer_config.json"),
            pooling: Pooling::Mean,
            normalize: false,
            max_length: 512,
            max_chars: CODE_MAX_CHARS,
            strip: false,
            dimension,
        }
    }

    /// Biến thể cho lane query code plane (`embed_runtime.embed_query` ->
    /// `AutoModel.encode`): Python không strip và cũng không char-bound ở đó.
    #[must_use]
    pub fn query_lane(mut self) -> Self {
        self.strip = false;
        self.max_chars = 0;
        self
    }

    /// Tiền xử lý text đúng thứ tự Python: `text[:max_chars]` (code point) rồi
    /// `str.strip()` — `_bounded_text` chạy trước, ST `tokenize` strip sau.
    #[must_use]
    pub fn prepare_text(&self, text: &str) -> String {
        let bounded = if self.max_chars == 0 {
            text.to_string()
        } else {
            text.chars().take(self.max_chars).collect()
        };
        if self.strip {
            python_strip(&bounded).to_string()
        } else {
            bounded
        }
    }

    pub fn validate(&self) -> Result<()> {
        for (label, path) in [
            ("graph", &self.graph),
            ("tokenizer.json", &self.tokenizer_json),
            ("tokenizer_config.json", &self.tokenizer_config),
        ] {
            if !path.is_file() {
                return Err(EmbedError::new(format!(
                    "{label} not found for model {}: {} (run `make build`, or set \
                     CORTEX_EMBED_GRAPH / CODE_EMBEDDING_MODEL_PATH)",
                    self.id,
                    path.display()
                )));
            }
        }
        self.verify_provenance()
    }

    /// Phase-06 G7 (red-team S5): a pinned model must load the EXACT exported
    /// graph recorded in-repo — existence checks let a swapped/regenerated
    /// graph through silently, which is the "mất im lặng" class the spike
    /// NO-GO flagged. The multi-GB external weights file is digested by the
    /// `#[ignore]`d repro test (`embed_golden::provenance_pins`) instead of
    /// every process load; the graph digest plus that test plus the HF
    /// revision pins together are the provenance contract.
    ///
    /// `CORTEX_EMBED_VERIFY_SHA256=0|false|no|off` bypasses the load-time
    /// check for re-pinning workflows and warns loudly.
    pub fn verify_provenance(&self) -> Result<()> {
        let raw = std::env::var("CORTEX_EMBED_VERIFY_SHA256")
            .unwrap_or_default()
            .trim()
            .to_lowercase();
        let bypass = matches!(raw.as_str(), "0" | "false" | "no" | "off");
        self.verify_provenance_with(bypass)
    }

    /// Env-free core (unit tests exercise the fail-closed branch without
    /// mutating process env under `unsafe_code = "deny"`).
    pub fn verify_provenance_with(&self, bypass: bool) -> Result<()> {
        if bypass {
            eprintln!(
                "[cortex-embed] WARNING: CORTEX_EMBED_VERIFY_SHA256 off — graph integrity \
                 check skipped (re-pinning workflow only)"
            );
            return Ok(());
        }
        let Some(pin) = model_pin(&self.id) else {
            return Ok(()); // custom/unpinned model (CODE_EMBEDDING_MODEL_PATH)
        };
        let actual = file_sha256(&self.graph)?;
        if !actual.eq_ignore_ascii_case(pin.graph_sha256) {
            return Err(EmbedError::new(format!(
                "graph sha256 mismatch for {} — refusing to embed with an unpinned graph \
                 (expected {}, got {}). Re-export with `make embed-jina-onnx` / \
                 `make embed-bge-onnx`, review the provenance, then update \
                 `model_pin` in cortex-embed/src/model.rs",
                self.id, pin.graph_sha256, actual
            )));
        }
        Ok(())
    }
}

/// In-repo provenance pins (phase-06 G7): the self-exported / fetched graphs
/// the parity fixtures were measured on. `weights_sha256` covers the external
/// data file next to the graph; `hf_revision` is the snapshot the export was
/// cut from (scripts pin the same revision).
pub struct ModelPin {
    pub graph_sha256: &'static str,
    pub weights_file: &'static str,
    pub weights_sha256: &'static str,
    pub hf_revision: &'static str,
}

#[must_use]
pub fn model_pin(id: &str) -> Option<ModelPin> {
    match id {
        "jinaai/jina-embeddings-v3" => Some(ModelPin {
            graph_sha256: "be2de66d2b4e087d0e0582dd43af0046004ffd2847b79b26dfe32956c63c4fa4",
            weights_file: "model.onnx.data",
            weights_sha256: "a0497c9e634b6faab5a43c35cfaea46bad1c1382bc7b74b12d4ff440515881d6",
            hf_revision: "ab036b023d30b4d1138c4c3bfa9f0c445ab455d6",
        }),
        "BAAI/bge-m3" => Some(ModelPin {
            graph_sha256: "f84251230831afb359ab26d9fd37d5936d4d9bb5d1d5410e66442f630f24435b",
            weights_file: "model.onnx_data",
            weights_sha256: "1eebfb28493f67bba03ce0ef64bfdc7fc5a3bd9d7493f818bb1d78cd798416b4",
            hf_revision: "5617a9f61b028005a4858fdac845db406aefb181",
        }),
        _ => None,
    }
}

/// Streaming SHA-256 of a file (hex). Public so the phase-06 repro gate can
/// digest the multi-GB weights file outside the load-time hot path.
pub fn file_sha256(path: &Path) -> Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut file = std::fs::File::open(path)
        .map_err(|error| EmbedError::new(format!("open {}: {error}", path.display())))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 4 * 1024 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|error| EmbedError::new(format!("read {}: {error}", path.display())))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>())
}

/// Bảng whitespace của CPython `str.strip()` (`Py_UNICODE_ISSPACE`), rộng hơn
/// Unicode `White_Space` mà `char::is_whitespace()` dùng: Python cắt thêm
/// U+001C..U+001F (file/group/record/unit separator). Khác biệt này thật — nếu
/// text ingest kết thúc bằng mấy ký tự đó thì token-id Python và Rust lệch nhau.
#[must_use]
pub fn is_python_space(ch: char) -> bool {
    ch.is_whitespace() || matches!(ch, '\u{1c}'..='\u{1f}')
}

/// `str.strip()` của Python.
#[must_use]
pub fn python_strip(text: &str) -> &str {
    text.trim_matches(is_python_space as fn(char) -> bool)
}

/// Cắt chuỗi env thành tên model/dir đã resolve. `lookup` được inject để test
/// không đụng `std::env::set_var` (race với test song song).
pub fn resolve_model_source(plane: Plane, lookup: &impl Fn(&str) -> Option<String>) -> String {
    let non_empty = |key: &str| {
        lookup(key)
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
    };
    let chain: &[&str] = match plane {
        Plane::Code => &[
            "CODE_EMBEDDING_MODEL_PATH",
            "CODE_EMBEDDING_MODEL",
            "EMBED_MODEL",
            "JINA_MODEL_PATH",
        ],
        Plane::Doc => &["EMBEDDING_MODEL_PATH", "EMBEDDING_MODEL"],
    };
    for key in chain {
        if let Some(value) = non_empty(key) {
            return value;
        }
    }
    match plane {
        Plane::Code => "jinaai/jina-embeddings-v3".to_string(),
        Plane::Doc => "BAAI/bge-m3".to_string(),
    }
}

/// `jinaai/jina-embeddings-v3` -> `jinaai--jina-embeddings-v3` (quy ước HF cache:
/// mỗi dấu `/` và ký tự không hợp lệ bị replace bằng `-`).
pub fn hf_dir_slug(name: &str) -> String {
    name.replace('/', "--")
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' || c == '.' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect()
}

/// Đường HF hub cache (tôn trọng `HF_HOME`/`HF_HUB_CACHE` như SDK Python).
pub fn hub_dir() -> PathBuf {
    if let Ok(explicit) = std::env::var("HF_HUB_CACHE")
        && !explicit.trim().is_empty()
    {
        return PathBuf::from(explicit);
    }
    if let Ok(home) = std::env::var("HF_HOME")
        && !home.trim().is_empty()
    {
        return PathBuf::from(home).join("hub");
    }
    let base = std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."));
    base.join(".cache").join("huggingface").join("hub")
}

/// Snapshot đã cache chứa `tokenizer.json`. `refs/main` được ưu tiên; nếu snapshot
/// đó không có tokenizer (bge-m3 tách wrapper/weights làm hai rev) thì quét hết.
pub fn hf_snapshot(name: &str) -> Option<PathBuf> {
    let root = hub_dir().join(format!("models--{}", hf_dir_slug(name)));
    let snapshots = root.join("snapshots");
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(rev) = std::fs::read_to_string(root.join("refs/main")) {
        candidates.push(snapshots.join(rev.trim()));
    }
    if let Ok(entries) = std::fs::read_dir(&snapshots) {
        let mut found: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| path.is_dir())
            .collect();
        found.sort();
        candidates.extend(found);
    }
    candidates
        .into_iter()
        .find(|path| path.join("tokenizer.json").is_file())
}

/// Thư mục repo (đi ngược từ CWD tìm cặp `code-tiny` + `rust`), fallback CWD.
pub fn repo_root() -> PathBuf {
    let start = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let mut cursor: &Path = &start;
    loop {
        if cursor.join("code-tiny").is_dir() && cursor.join("rust").is_dir() {
            return cursor.to_path_buf();
        }
        match cursor.parent() {
            Some(up) => cursor = up,
            None => return start,
        }
    }
}

/// Where our own ONNX exports live (gitignored via `.cache/*`).
pub fn export_dir(root: &Path, name: &str) -> PathBuf {
    root.join(".cache").join("embed").join(hf_dir_slug(name))
}

/// Ghép `ModelSpec` từ env. Đồ thị ưu tiên bản export của mình (jina-v3 bắt buộc
/// phải export — findings C3), rồi tới `onnx/model.onnx` chính thức trong snapshot.
pub fn spec_from_env(plane: Plane) -> Result<ModelSpec> {
    spec_from_source(plane, &resolve_model_source(plane, &environment))
}

fn environment(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

pub fn spec_from_source(plane: Plane, source: &str) -> Result<ModelSpec> {
    let root = repo_root();
    let as_path = PathBuf::from(source.trim());
    let snapshot = if as_path.is_dir() {
        as_path
    } else {
        hf_snapshot(source).ok_or_else(|| {
            EmbedError::new(format!(
                "model '{source}' is not a directory and has no cached HF snapshot with \
                 tokenizer.json under {}; run `make build` or sync the model first",
                hub_dir().display()
            ))
        })?
    };

    let graph = if let Some(explicit) = environment("CORTEX_EMBED_GRAPH") {
        let explicit = explicit.trim();
        if !explicit.is_empty() {
            PathBuf::from(explicit)
        } else {
            pick_graph(&root, &snapshot, source)?
        }
    } else {
        pick_graph(&root, &snapshot, source)?
    };

    let spec = match plane {
        Plane::Code => ModelSpec::jina_v3(&snapshot, graph),
        Plane::Doc => ModelSpec::bge_m3(&snapshot, graph),
    };
    spec.validate()?;
    Ok(spec)
}

fn pick_graph(root: &Path, snapshot: &Path, source: &str) -> Result<PathBuf> {
    let exported = export_dir(root, source).join("model.onnx");
    if exported.is_file() {
        return Ok(exported);
    }
    // Export scripts pick their own directory names (jina → `jina-v3-onnx-fp32`),
    // which need not equal the HF slug `export_dir` derives. `metadata.json`
    // records the source model, so scan for it — otherwise the resolver silently
    // falls to the official snapshot graph (jina's needs a `task_id` input this
    // embedder never passes) and only the G7 pin digest catches the substitution
    // at load.
    if let Some(found) = metadata_exported_graph(root, source) {
        return Ok(found);
    }
    let official = snapshot.join("onnx").join("model.onnx");
    if official.is_file() {
        return Ok(official);
    }
    Err(EmbedError::new(format!(
        "no ONNX graph for '{source}': looked in {} and {}",
        exported.display(),
        official.display()
    )))
}

/// Find an exported graph under `.cache/embed/<dir>/` whose `metadata.json`
/// declares `model == source`. Deterministic (sorted dir names).
pub fn metadata_exported_graph(root: &Path, source: &str) -> Option<PathBuf> {
    let embed_dir = root.join(".cache").join("embed");
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(&embed_dir)
        .ok()?
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .collect();
    dirs.sort();
    for dir in dirs {
        let Ok(text) = std::fs::read_to_string(dir.join("metadata.json")) else {
            continue;
        };
        let Ok(meta) = serde_json::from_str::<serde_json::Value>(&text) else {
            continue;
        };
        if meta.get("model").and_then(serde_json::Value::as_str) == Some(source) {
            let graph = dir.join("model.onnx");
            if graph.is_file() {
                return Some(graph);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn lookup_from(pairs: &[(&str, &str)]) -> impl Fn(&str) -> Option<String> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect();
        move |key: &str| map.get(key).cloned()
    }

    #[test]
    fn code_chain_prefers_path_then_model_then_fallback() {
        let both = lookup_from(&[
            ("CODE_EMBEDDING_MODEL_PATH", "/models/custom"),
            ("CODE_EMBEDDING_MODEL", "other/model"),
        ]);
        assert_eq!(resolve_model_source(Plane::Code, &both), "/models/custom");

        let only_second = lookup_from(&[
            ("CODE_EMBEDDING_MODEL_PATH", "   "),
            ("EMBED_MODEL", "acme/emb"),
        ]);
        assert_eq!(resolve_model_source(Plane::Code, &only_second), "acme/emb");

        let none = lookup_from(&[]);
        assert_eq!(
            resolve_model_source(Plane::Code, &none),
            "jinaai/jina-embeddings-v3"
        );
    }

    #[test]
    fn doc_chain_is_independent_of_code_env() {
        let only_code = lookup_from(&[("CODE_EMBEDDING_MODEL", "jinaai/jina-embeddings-v3")]);
        assert_eq!(resolve_model_source(Plane::Doc, &only_code), "BAAI/bge-m3");

        let doc = lookup_from(&[("EMBEDDING_MODEL", "BAAI/bge-m3"), ("EMBED_MODEL", "x")]);
        assert_eq!(resolve_model_source(Plane::Doc, &doc), "BAAI/bge-m3");
    }

    #[test]
    fn jina_spec_is_mean_normalized_8194() {
        let spec = ModelSpec::jina_v3(Path::new("/snap"), PathBuf::from("/g.onnx"));
        assert_eq!(spec.pooling, Pooling::Mean);
        assert!(spec.normalize);
        assert_eq!(spec.max_length, 8194);
        assert_eq!(spec.max_chars, 4000);
        assert_eq!(spec.dimension, 1024);
    }

    #[test]
    fn bge_spec_is_cls_normalized_8192() {
        let spec = ModelSpec::bge_m3(Path::new("/snap"), PathBuf::from("/g.onnx"));
        assert_eq!(spec.pooling, Pooling::Cls);
        assert!(spec.normalize);
        assert_eq!(spec.max_length, 8192);
        assert_eq!(spec.max_chars, 0);
    }

    #[test]
    fn legacy_spec_keeps_the_512_unnormalized_branch() {
        let spec = ModelSpec::legacy_unnormalized(Path::new("/snap"), PathBuf::from("/g.onnx"), 768);
        assert!(!spec.normalize);
        assert_eq!(spec.max_length, 512);
        assert_eq!(spec.dimension, 768);
    }

    #[test]
    fn prepare_text_bounds_by_code_point_not_byte() {
        // "HOẰN" = 4 code point nhưng 10 UTF-8 byte; max_chars=4 phải giữ đúng 4 ký tự.
        let mut spec = ModelSpec::bge_m3(Path::new("/snap"), PathBuf::from("/g.onnx"));
        spec.max_chars = 4;
        assert_eq!(spec.prepare_text("HOẰN KIẾM"), "HOẰN");
        // Emoji là 1 code point ở cả Rust lẫn Python (không phải 2 như UTF-16).
        spec.max_chars = 2;
        assert_eq!(spec.prepare_text("🚀🔥ab"), "🚀🔥");
        spec.max_chars = 0;
        assert_eq!(spec.prepare_text("untouched"), "untouched");
    }

    #[test]
    fn prepare_text_strips_after_char_bound() {
        let mut spec = ModelSpec::bge_m3(Path::new("/snap"), PathBuf::from("/g.onnx"));
        spec.max_chars = 6;
        // bound("  abcdefgh  ", 6) == "  abcd" -> strip == "abcd"
        assert_eq!(spec.prepare_text("  abcdefgh  "), "abcd");
    }

    #[test]
    fn strip_flag_follows_the_lane() {
        let jina = ModelSpec::jina_v3(Path::new("/snap"), PathBuf::from("/g.onnx"));
        assert!(jina.strip, "ingest lane goes through SentenceTransformer");
        assert_eq!(jina.prepare_text("  x\n"), "x");
        let query = jina.clone().query_lane();
        assert_eq!(query.prepare_text("  x\n"), "  x\n");
        assert_eq!(query.max_chars, 0, "query lane does not char-bound in Python");
        assert!(
            !ModelSpec::legacy_unnormalized(Path::new("/s"), PathBuf::from("/g.onnx"), 1024).strip
        );
    }

    #[test]
    fn strip_matches_python_on_file_separator_chars() {
        // `char::is_whitespace()` KHÔNG cắt U+001C..U+001F; Python `str.strip()` thì có.
        assert!(!'\u{1c}'.is_whitespace(), "baseline: Rust Unicode table excludes FS");
        assert!(is_python_space('\u{1c}'));
        let spec = ModelSpec::bge_m3(Path::new("/snap"), PathBuf::from("/g.onnx"));
        assert_eq!(spec.prepare_text("\u{1c}payload\u{1d}\u{1e}\u{1f}"), "payload");
        // Phần trùng bảng: whitespace Unicode thông thường.
        assert_eq!(spec.prepare_text("\t\n\r\u{a0}x\u{2028}"), "x");
        // Không được ăn ký tự giữa chuỗi.
        assert_eq!(spec.prepare_text("a\u{1c}b"), "a\u{1c}b");
    }

    #[test]
    fn hf_slug_matches_hub_layout() {
        assert_eq!(
            hf_dir_slug("jinaai/jina-embeddings-v3"),
            "jinaai--jina-embeddings-v3"
        );
        assert_eq!(hf_dir_slug("BAAI/bge-m3"), "BAAI--bge-m3");
    }

    #[test]
    fn validate_reports_missing_graph() {
        let spec = ModelSpec::jina_v3(Path::new("/nonexistent-snap"), PathBuf::from("/nonexistent.onnx"));
        let error = spec.validate().expect_err("missing files must fail");
        assert!(
            error.0.contains("graph") && error.0.contains("make build"),
            "unhelpful error: {error}"
        );
    }

    // ── phase-06 G7: provenance pins ────────────────────────────────────────
    #[test]
    fn pinned_graph_with_wrong_digest_fails_closed() {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        std::io::Write::write_all(&mut file, b"not the exported graph").unwrap();
        let spec = ModelSpec::jina_v3(Path::new("/snap"), file.path().to_path_buf());
        let error = spec
            .verify_provenance_with(false)
            .expect_err("substituted graph must be rejected");
        assert!(error.0.contains("graph sha256 mismatch"), "{error}");
        assert!(error.0.contains("model_pin"), "{error}");
    }

    #[test]
    fn unpinned_custom_model_passes() {
        let mut file = tempfile::NamedTempFile::new().unwrap();
        std::io::Write::write_all(&mut file, b"anything").unwrap();
        let spec = ModelSpec::legacy_unnormalized(
            Path::new("/models/custom"),
            file.path().to_path_buf(),
            512,
        );
        spec.verify_provenance_with(false).expect("no pin ⇒ skip, do not hard-error");
    }

    #[test]
    fn bypass_flag_skips_verification_loudly() {
        let spec = ModelSpec::jina_v3(Path::new("/snap"), PathBuf::from("/definitely-missing.onnx"));
        spec.verify_provenance_with(true)
            .expect("bypass is the operator escape hatch");
    }

    #[test]
    fn pins_are_self_consistent() {
        for id in ["jinaai/jina-embeddings-v3", "BAAI/bge-m3"] {
            let pin = model_pin(id).expect("pinned");
            assert_eq!(pin.graph_sha256.len(), 64, "{id} graph digest");
            assert_eq!(pin.weights_sha256.len(), 64, "{id} weights digest");
            assert_eq!(pin.hf_revision.len(), 40, "{id} revision hash");
        }
        assert!(model_pin("some/other").is_none());
    }
}
