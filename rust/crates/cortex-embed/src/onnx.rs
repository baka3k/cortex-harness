//! `OnnxEmbedder`: tokenizer crate `tokenizers` + inference `ort` (load-dynamic).
//!
//! Số học khớp reference Python đã đối chiếu ở `findings.md`:
//! tokenize(add special, truncate `model_max_length`) → run graph
//! `input_ids,attention_mask -> last_hidden_state` → mean/CLS pool → L2-normalize.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use ort::session::Session;
use tokenizers::{Tokenizer, TruncationParams};

use crate::backend::Embedder;
use crate::error::{EmbedError, Result};
use crate::model::{ModelSpec, Pooling};
use crate::pooling;

/// ONNX Runtime được `make build` provision vào `.cache/ort/`, hoặc tái dùng dylib
/// mà package `onnxruntime` của Python đã có trong `.venv`.
static ORT_RUNTIME: OnceLock<std::result::Result<(), String>> = OnceLock::new();

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionConfig {
    pub intra_threads: usize,
    pub inter_threads: usize,
    /// Bật để ORT reduce theo thứ tự cố định — parity gate đòi kết quả đo lại được.
    pub deterministic: bool,
    /// Cho phép intra-op pool của ORT **spin-wait** sau mỗi `run()`. Default on
    /// (hành vi ORT gốc). Phase-02b đã đo: spin on/off KHÔNG đổi latency server
    /// (+25-31ms/tool-call lúc đó đến từ GET `/collections` qua ssh-tunnel,
    /// không phải spin) và KHÔNG đổi số học (golden 3/3 cả hai cấu hình) — knob
    /// giữ lại để benchmark/tuning, không phải fix.
    pub spin: bool,
}

impl Default for SessionConfig {
    fn default() -> Self {
        Self {
            intra_threads: crate::backend::read_env("CORTEX_EMBED_ORT_THREADS")
                .and_then(|raw| raw.parse::<usize>().ok())
                .filter(|width| *width > 0)
                .unwrap_or_else(|| {
                    std::thread::available_parallelism()
                        .map(|width| width.get().min(8))
                        .unwrap_or(4)
                }),
            inter_threads: 1,
            // Bật mặc định để golden vector tái lập được; tắt qua env khi đo
            // benchmark vì ORT bỏ qua một số optimization khi deterministic.
            deterministic: crate::backend::parse_flag(
                crate::backend::read_env("CORTEX_EMBED_ORT_DETERMINISTIC").as_deref(),
                true,
            ),
            spin: crate::backend::parse_flag(
                crate::backend::read_env("CORTEX_EMBED_ORT_SPIN").as_deref(),
                true,
            ),
        }
    }
}

/// Tìm `libonnxruntime` động: `ORT_DYLIB_PATH` → `.cache/ort/**` → `.venv`.
pub fn ort_dylib(root: &Path) -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("ORT_DYLIB_PATH") {
        let explicit = explicit.trim();
        if !explicit.is_empty() {
            let path = PathBuf::from(explicit);
            return path.is_file().then_some(path);
        }
    }
    let names: &[&str] = if cfg!(target_os = "macos") {
        &["libonnxruntime.dylib"]
    } else if cfg!(target_os = "windows") {
        &["onnxruntime.dll"]
    } else {
        &["libonnxruntime.so"]
    };
    for candidate in ort_search_dirs(root) {
        if let Some(found) = first_matching(&candidate, names, "onnxruntime") {
            return Some(found);
        }
    }
    None
}

fn ort_search_dirs(root: &Path) -> Vec<PathBuf> {
    let mut dirs = vec![root.join(".cache").join("ort")];
    for python_dir in ["python3.12", "python3.11", "python3.13"] {
        dirs.push(
            root.join(".venv")
                .join("lib")
                .join(python_dir)
                .join("site-packages")
                .join("onnxruntime")
                .join("capi"),
        );
    }
    dirs
}

/// DFS nông, ưu tiên tên chính xác rồi tới prefix (venv có `libonnxruntime.1.29.0.dylib`).
fn first_matching(dir: &Path, names: &[&str], prefix: &str) -> Option<PathBuf> {
    let mut queue = vec![dir.to_path_buf()];
    let mut fallback: Option<PathBuf> = None;
    let mut visited = 0usize;
    while let Some(current) = queue.pop() {
        visited += 1;
        if visited > 64 {
            break;
        }
        let Ok(entries) = std::fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                queue.push(path);
                continue;
            }
            let file = path.file_name().map(|name| name.to_string_lossy().into_owned());
            let Some(file) = file else { continue };
            if names.iter().any(|name| file == *name) {
                return Some(path);
            }
            let stem = file.strip_prefix("lib").unwrap_or(&file);
            if fallback.is_none() && stem.starts_with(prefix) && path.is_file() {
                fallback = Some(path);
            }
        }
    }
    fallback
}

/// `ort::init_from` là process-global; gọi nhiều lần phải không làm hỏng session.
fn ensure_runtime(dylib: &Path) -> Result<()> {
    ORT_RUNTIME
        .get_or_init(|| {
            ort::init_from(dylib)
                .map(|builder| {
                    if !builder.commit() {
                        Err(String::from("ONNX Runtime environment init failed"))
                    } else {
                        Ok(())
                    }
                    .map_err(|error| error.to_string())
                })
                .map_err(|error| error.to_string())?
        })
        .clone()
        .map_err(EmbedError::new)
}

pub struct OnnxEmbedder {
    session: Mutex<Session>,
    tokenizer: Tokenizer,
    spec: ModelSpec,
    pad_id: u32,
    batch_size: usize,
}

impl OnnxEmbedder {
    pub fn new(spec: ModelSpec) -> Result<Self> {
        Self::with_config(spec, SessionConfig::default())
    }

    pub fn with_config(spec: ModelSpec, config: SessionConfig) -> Result<Self> {
        spec.validate()?;
        let root = crate::model::repo_root();
        let dylib = ort_dylib(&root).ok_or_else(|| {
            EmbedError::new(format!(
                "libonnxruntime not found under {} or .venv — run `make build` \
                 (or set ORT_DYLIB_PATH)",
                root.join(".cache").join("ort").display()
            ))
        })?;
        ensure_runtime(&dylib)?;

        let mut tokenizer = Tokenizer::from_file(&spec.tokenizer_json)?;
        // `tokenizer.json` của cả hai model đều null `truncation`/`padding`, và
        // TruncationParams::default() = 512 — phải set tường minh (findings C4).
        tokenizer.with_truncation(Some(TruncationParams {
            max_length: spec.max_length,
            ..Default::default()
        }))?;
        let pad_id = pad_token_id(&tokenizer, &spec.tokenizer_config)?;

        let mut builder = Session::builder()?;
        builder = builder
            .with_intra_threads(config.intra_threads)
            .map_err(|error| EmbedError::new(error.to_string()))?;
        builder = builder
            .with_inter_threads(config.inter_threads)
            .map_err(|error| EmbedError::new(error.to_string()))?;
        if config.deterministic {
            builder = builder
                .with_deterministic_compute(true)
                .map_err(|error| EmbedError::new(error.to_string()))?;
        }
        if !config.spin {
            builder = builder
                .with_intra_op_spinning(false)
                .map_err(|error| EmbedError::new(error.to_string()))?;
        }
        let session = builder.commit_from_file(&spec.graph)?;

        Ok(Self {
            session: Mutex::new(session),
            tokenizer,
            spec,
            pad_id,
            batch_size: 8,
        })
    }

    #[must_use]
    pub fn with_batch(mut self, batch_size: usize) -> Self {
        self.batch_size = batch_size.max(1);
        self
    }

    #[must_use]
    pub fn spec(&self) -> &ModelSpec {
        &self.spec
    }

    #[must_use]
    pub fn ort_version() -> &'static str {
        ort::info()
    }

    /// Token ids sau khi tiền xử lý theo lane — để harness parity so trực tiếp với
    /// `AutoTokenizer` Python (gate token-id trước, cosine sau).
    pub fn tokenize_ids(&self, texts: &[String]) -> Result<Vec<Vec<u32>>> {
        self.tokenize(texts).map(|(sequences, _)| sequences)
    }

    fn tokenize(&self, texts: &[String]) -> Result<(Vec<Vec<u32>>, usize)> {
        let mut sequences = Vec::with_capacity(texts.len());
        let mut longest = 0usize;
        for text in texts {
            let prepared = self.spec.prepare_text(text);
            let encoding = self.tokenizer.encode(prepared.as_str(), true)?;
            let ids: Vec<u32> = encoding.get_ids().to_vec();
            longest = longest.max(ids.len());
            sequences.push(ids);
        }
        Ok((sequences, longest))
    }

    fn embed_batch(&self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        let trace = crate::backend::trace_enabled();
        let tokenize_started = Instant::now();
        let (sequences, longest) = self.tokenize(texts)?;
        let tokenize_ms = elapsed_ms(trace, tokenize_started);
        let batch = sequences.len();
        let width = longest.max(1);

        let mut ids: Vec<i64> = Vec::with_capacity(batch * width);
        let mut mask: Vec<i64> = Vec::with_capacity(batch * width);
        for sequence in &sequences {
            ids.extend(sequence.iter().map(|value| *value as i64));
            mask.extend((0..sequence.len()).map(|_| 1i64));
            let pad = width - sequence.len();
            ids.resize(ids.len() + pad, i64::from(self.pad_id));
            mask.extend(std::iter::repeat_n(0i64, pad));
        }

        let input_ids = ort::value::Tensor::from_array(([batch, width], ids))?;
        let attention_mask = ort::value::Tensor::from_array(([batch, width], mask))?;

        let run_started = Instant::now();
        let mut session = self
            .session
            .lock()
            .map_err(|_| EmbedError::new("embedder session lock poisoned"))?;
        let outputs = session.run(ort::inputs![
            "input_ids" => input_ids,
            "attention_mask" => attention_mask,
        ])?;
        let run_ms = elapsed_ms(trace, run_started);
        let (shape, hidden_states) = outputs[0].try_extract_tensor::<f32>()?;
        let hidden_size = *shape
            .last()
            .ok_or_else(|| EmbedError::new("model returned a rank-0 tensor"))?
            as usize;
        if shape.len() != 3 {
            return Err(EmbedError::new(format!(
                "expected (batch, sequence, hidden), got shape {shape:?}"
            )));
        }
        if hidden_size != self.spec.dimension {
            return Err(EmbedError::new(format!(
                "{}: graph emits {hidden_size}-dim vectors but spec expects {}",
                self.spec.id, self.spec.dimension
            )));
        }

        let pool_started = Instant::now();
        let mut vectors = Vec::with_capacity(batch);
        for (index, sequence) in sequences.iter().enumerate() {
            let start = index * width * hidden_size;
            let slice = hidden_states
                .get(start..start + width * hidden_size)
                .ok_or_else(|| EmbedError::new("output tensor shorter than declared shape"))?;
            let step_mask: Vec<i32> = (0..width)
                .map(|step| i32::from(step < sequence.len()))
                .collect();
            let mut vector = match self.spec.pooling {
                Pooling::Mean => pooling::mean_pool(slice, width, hidden_size, &step_mask),
                Pooling::Cls => pooling::cls_pool(slice, hidden_size),
            };
            if self.spec.normalize {
                pooling::l2_normalize(&mut vector);
            }
            vectors.push(vector);
        }
        if trace {
            let pool_ms = elapsed_ms(true, pool_started);
            let total_ms = elapsed_ms(true, tokenize_started);
            eprintln!(
                "[cortex-embed.trace] model={} batch={batch} width={width} \
                 tokenize={tokenize_ms:.1}ms run={run_ms:.1}ms pool={pool_ms:.1}ms \
                 total={total_ms:.1}ms",
                self.spec.id
            );
        }
        Ok(vectors)
    }
}

/// Trace tắt thì trả 0.0 — giữ format dòng log ổn định mà không in số rác.
fn elapsed_ms(enabled: bool, started: Instant) -> f64 {
    if enabled {
        started.elapsed().as_secs_f64() * 1000.0
    } else {
        0.0
    }
}

/// `pad_token_id` không có trong `tokenizer_config.json` của 2 model này (null),
/// nên resolve `<pad>` qua vocab — khớp `tokenizer.pad_token_id` của Python (= 1).
fn pad_token_id(tokenizer: &Tokenizer, config_path: &Path) -> Result<u32> {
    let config: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(config_path)?).map_err(|error| {
            EmbedError::new(format!("{}: {error}", config_path.display()))
        })?;
    let pad_token = config
        .get("pad_token")
        .and_then(serde_json::Value::as_str)
        .unwrap_or("<pad>");
    tokenizer.token_to_id(pad_token).ok_or_else(|| {
        EmbedError::new(format!(
            "pad token '{pad_token}' is missing from the vocabulary of {}",
            config_path.display()
        ))
    })
}

impl Embedder for OnnxEmbedder {
    fn embed(&self, texts: &[String]) -> Result<Vec<Vec<f32>>> {
        if texts.is_empty() {
            return Ok(Vec::new());
        }
        let mut out = Vec::with_capacity(texts.len());
        for chunk in texts.chunks(self.batch_size) {
            out.extend(self.embed_batch(chunk)?);
        }
        Ok(out)
    }

    fn dimension(&self) -> Option<usize> {
        Some(self.spec.dimension)
    }

    fn backend_name(&self) -> &'static str {
        "onnx"
    }
}
