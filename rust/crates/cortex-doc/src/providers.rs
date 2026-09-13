//! Entity-extraction provider seam — port of the provider dispatch in
//! `doc-tiny/graphrag_ingest_langextract.py::build_graph_components` +
//! `doc-tiny/entity_extractors.py`.
//!
//! Providers:
//! - [`LangextractProvider`] — direct port: LLM API calls (Gemini / OpenAI /
//!   Ollama-compatible) over `reqwest`, identical env-var handling
//!   (`LANGEXTRACT_API_KEY`, `LANGEXTRACT_MODEL_ID`, `LANGEXTRACT_MODEL_URL`,
//!   `LLM_RETRY_COUNT`, `LLM_RETRY_BACKOFF_SECONDS`) and retry ladder.
//! - [`SpacyProvider`] — `en_core_web_spacy` statistical NER stays a Python
//!   sidecar (subprocess); EntityRuler JSON rule patterns run natively.
//! - [`GlinerProvider`] — stays a Python sidecar (subprocess), per phase 14.
//! - [`FixtureProvider`] — deterministic replay of recorded entities; the
//!   parity harness mocks BOTH sides with it (LLM/NER disabled identically).

use std::collections::HashMap;
use std::process::Command;

use serde_json::Value;

use crate::entity::normalize_entities;

pub type ProviderResult<T> = Result<T, ProviderError>;

#[derive(Debug)]
pub struct ProviderError(pub String);

impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for ProviderError {}

/// Returns `(entities, relations)` as raw payload dicts ready for
/// `build_graph_components_from_entities`.
pub trait EntityProvider {
    fn extract(&mut self, text: &str) -> ProviderResult<(Vec<Value>, Vec<Value>)>;
}

/// Resolve the python interpreter used by sidecars: `CORTEX_DOC_PYTHON` env,
/// falling back to the repo `.venv` then `python3`.
pub fn python_binary() -> String {
    if let Ok(path) = std::env::var("CORTEX_DOC_PYTHON")
        && !path.trim().is_empty()
    {
        return path;
    }
    for candidate in [".venv/bin/python", "venv/bin/python"] {
        if std::path::Path::new(candidate).exists() {
            return candidate.to_string();
        }
    }
    "python3".to_string()
}

/// Deterministic replay provider used for parity runs (mock on the Rust side,
/// monkeypatched `build_graph_components` on the Python side).
pub struct FixtureProvider {
    entities_by_paragraph: HashMap<String, Vec<Value>>,
}

impl FixtureProvider {
    pub fn from_json(payload: &Value) -> Self {
        let mut map = HashMap::new();
        if let Some(obj) = payload
            .get("entities_by_paragraph")
            .and_then(Value::as_object)
        {
            for (text, entities) in obj {
                if let Some(list) = entities.as_array() {
                    map.insert(text.clone(), list.clone());
                }
            }
        }
        Self {
            entities_by_paragraph: map,
        }
    }
}

impl EntityProvider for FixtureProvider {
    fn extract(&mut self, text: &str) -> ProviderResult<(Vec<Value>, Vec<Value>)> {
        Ok((
            self.entities_by_paragraph
                .get(text)
                .cloned()
                .unwrap_or_default(),
            Vec::new(),
        ))
    }
}

/// GLiNER stays a Python sidecar: one subprocess loads the model and predicts
/// entities for a batch of texts (JSON over stdin/stdout).
pub struct GlinerProvider {
    labels: Vec<String>,
    threshold: f64,
    model: String,
}

impl GlinerProvider {
    pub fn new(model: &str, labels: Vec<String>, threshold: f64) -> Self {
        Self {
            labels,
            threshold,
            model: model.to_string(),
        }
    }

    /// Port of `parse_gliner_labels`.
    pub fn parse_labels(raw: Option<&str>) -> Vec<String> {
        const DEFAULT: [&str; 8] = [
            "PERSON", "ORG", "PRODUCT", "GPE", "DATE", "TECH", "CRYPTO", "STANDARD",
        ];
        let Some(raw) = raw else {
            return DEFAULT.iter().map(|s| s.to_string()).collect();
        };
        let candidate = raw.trim();
        if candidate.is_empty() {
            return DEFAULT.iter().map(|s| s.to_string()).collect();
        }
        let path = std::path::Path::new(candidate);
        if path.is_file() {
            if let Ok(content) = std::fs::read_to_string(path) {
                let labels: Vec<String> = content
                    .split([',', '\n'])
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .collect();
                if !labels.is_empty() {
                    return labels;
                }
            }
            return DEFAULT.iter().map(|s| s.to_string()).collect();
        }
        let labels: Vec<String> = candidate
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .collect();
        if labels.is_empty() {
            DEFAULT.iter().map(|s| s.to_string()).collect()
        } else {
            labels
        }
    }
}

const GLINER_SIDECAR: &str = r#"
import json, sys
from gliner import GLiNER
req = json.loads(sys.stdin.read())
model = GLiNER.from_pretrained(req["model"], local_files_only=bool(req.get("local_only", False)))
out = [model.predict_entities(t, req["labels"], threshold=req["threshold"]) for t in req["texts"]]
print(json.dumps(out))
"#;

impl EntityProvider for GlinerProvider {
    fn extract(&mut self, text: &str) -> ProviderResult<(Vec<Value>, Vec<Value>)> {
        self.extract_batch(&[text.to_string()]).map(|mut rows| {
            let entities = rows.pop().unwrap_or_default();
            (entities, Vec::new())
        })
    }
}

impl GlinerProvider {
    /// Batch prediction (mirrors `extract_entities_gliner_batch`).
    pub fn extract_batch(&mut self, texts: &[String]) -> ProviderResult<Vec<Vec<Value>>> {
        if texts.is_empty() {
            return Ok(Vec::new());
        }
        let request = serde_json::json!({
            "texts": texts,
            "labels": self.labels,
            "threshold": self.threshold,
            "model": self.model,
            "local_only": std::env::var("HF_HUB_OFFLINE").map(|v| !matches!(v.as_str(), "0" | "false" | "no")).unwrap_or(false),
        });
        let output = Command::new(python_binary())
            .arg("-c")
            .arg(GLINER_SIDECAR)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .and_then(|mut child| {
                use std::io::Write;
                child
                    .stdin
                    .as_mut()
                    .expect("piped stdin")
                    .write_all(request.to_string().as_bytes())?;
                let out = child.wait_with_output()?;
                if out.status.success() {
                    Ok(out.stdout)
                } else {
                    Err(std::io::Error::other(
                        String::from_utf8_lossy(&out.stderr).into_owned(),
                    ))
                }
            })
            .map_err(|e| ProviderError(format!("gliner sidecar failed: {e}")))?;
        let predictions: Vec<Vec<Value>> = serde_json::from_slice(&output)
            .map_err(|e| ProviderError(format!("gliner sidecar output: {e}")))?;
        Ok(texts
            .iter()
            .zip(predictions)
            .map(|(text, items)| normalize_entities(&items, Some(text)))
            .collect())
    }
}

/// spaCy provider: EntityRuler JSON patterns run natively (deterministic
/// rule matching subset); the statistical `en_core_web_*` models stay a
/// Python sidecar.
pub struct SpacyProvider {
    mode: SpacyMode,
}

enum SpacyMode {
    Ruler(RulerRules),
    Model(String),
}

pub struct RulerRules {
    /// (label, lowercase token sequence)
    phrases: Vec<(String, Vec<String>)>,
}

impl SpacyProvider {
    /// `model` = spaCy model name; `ruler_json` = optional EntityRuler JSON
    /// file/directory. When only rules are given, matching runs natively.
    pub fn new(model: &str, ruler_json: Option<&str>) -> ProviderResult<Self> {
        if let Some(path) = ruler_json
            && !path.trim().is_empty()
        {
            return Ok(Self {
                mode: SpacyMode::Ruler(load_ruler_rules(path)?),
            });
        }
        Ok(Self {
            mode: SpacyMode::Model(model.to_string()),
        })
    }
}

/// Load EntityRuler patterns: a JSON list or `{"patterns": [...]}` per file;
/// each pattern is either a plain string (phrase) or a token dict sequence
/// with `LOWER`/`ORTH`/`TEXT` keys. Mirrors the validation of
/// `build_spacy_pipeline`.
pub fn load_ruler_rules(path: &str) -> ProviderResult<RulerRules> {
    let path = std::path::Path::new(path);
    if !path.exists() {
        return Err(ProviderError(format!(
            "EntityRuler path not found: {}",
            path.display()
        )));
    }
    let files: Vec<std::path::PathBuf> = if path.is_dir() {
        let mut files: Vec<std::path::PathBuf> = std::fs::read_dir(path)
            .map_err(|e| ProviderError(e.to_string()))?
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().map(|x| x == "json").unwrap_or(false))
            .collect();
        files.sort();
        if files.is_empty() {
            return Err(ProviderError(format!(
                "No JSON files found in rules directory: {}",
                path.display()
            )));
        }
        files
    } else {
        vec![path.to_path_buf()]
    };

    let mut phrases = Vec::new();
    for file in files {
        let content = std::fs::read_to_string(&file)
            .map_err(|e| ProviderError(format!("{}: {e}", file.display())))?;
        let payload: Value = serde_json::from_str(&content).map_err(|e| {
            ProviderError(format!("Invalid EntityRuler JSON: {}: {e}", file.display()))
        })?;
        let patterns = match payload.get("patterns") {
            Some(list) => list.clone(),
            None => payload,
        };
        let Some(items) = patterns.as_array() else {
            return Err(ProviderError(format!(
                "EntityRuler JSON must be a list or {{\"patterns\": [...]}} format: {}",
                file.display()
            )));
        };
        if items.is_empty() {
            return Err(ProviderError(format!(
                "EntityRuler JSON has no patterns: {}",
                file.display()
            )));
        }
        for item in items {
            let label = item
                .get("label")
                .and_then(Value::as_str)
                .ok_or_else(|| ProviderError(format!("Invalid pattern (missing label): {item}")))?;
            match item.get("pattern") {
                Some(Value::String(phrase)) => {
                    phrases.push((label.to_string(), tokenize_lower(phrase)));
                }
                Some(Value::Array(tokens)) => {
                    let mut seq = Vec::new();
                    for token in tokens {
                        let lowered = token
                            .get("LOWER")
                            .or_else(|| token.get("ORTH"))
                            .or_else(|| token.get("TEXT"))
                            .and_then(Value::as_str)
                            .map(str::to_lowercase);
                        match lowered {
                            Some(word) => seq.push(word),
                            None => {
                                return Err(ProviderError(format!(
                                    "Unsupported token pattern (need LOWER/ORTH/TEXT): {token}"
                                )));
                            }
                        }
                    }
                    phrases.push((label.to_string(), seq));
                }
                other => {
                    return Err(ProviderError(format!(
                        "Invalid pattern (string or token list required): {other:?}"
                    )));
                }
            }
        }
    }
    Ok(RulerRules { phrases })
}

/// Whitespace tokenization + punctuation splitting (token-level view of
/// `tokenize_with_offsets`, used for ruler phrase patterns).
fn tokenize_lower(text: &str) -> Vec<String> {
    text.split_whitespace()
        .flat_map(|word| {
            let mut parts = Vec::new();
            let mut run = String::new();
            for c in word.chars() {
                if c.is_ascii_punctuation() {
                    if !run.is_empty() {
                        parts.push(run.clone());
                        run.clear();
                    }
                    parts.push(c.to_string());
                } else {
                    run.push(c);
                }
            }
            if !run.is_empty() {
                parts.push(run);
            }
            parts
        })
        .map(|part| part.to_lowercase())
        .collect()
}

impl EntityProvider for SpacyProvider {
    fn extract(&mut self, text: &str) -> ProviderResult<(Vec<Value>, Vec<Value>)> {
        match &self.mode {
            SpacyMode::Ruler(rules) => {
                // Deterministic subset: match lowercased token n-grams against
                // ruler phrases; leftmost match wins, longer phrases win on
                // ties, non-overlapping (approximation of spaCy's
                // EntityRuler filter for exact-phrase patterns).
                let tokens = tokenize_with_offsets(text);
                let mut candidates: Vec<(usize, usize, &str)> = Vec::new();
                for (label, phrase) in &rules.phrases {
                    let n = phrase.len();
                    if n == 0 || tokens.len() < n {
                        continue;
                    }
                    for start in 0..=(tokens.len() - n) {
                        if tokens[start..start + n]
                            .iter()
                            .zip(phrase.iter())
                            .all(|(token, want)| token.lower == *want)
                        {
                            candidates.push((start, start + n, label));
                        }
                    }
                }
                candidates.sort_by(|a, b| a.0.cmp(&b.0).then((b.1 - b.0).cmp(&(a.1 - a.0))));
                let mut raw: Vec<Value> = Vec::new();
                let mut last_end = 0usize;
                for (start, end, label) in candidates {
                    if start < last_end {
                        continue;
                    }
                    last_end = end;
                    let surface = &text[tokens[start].byte_start..tokens[end - 1].byte_end];
                    raw.push(serde_json::json!({
                        "name": surface,
                        "type": label,
                        "start_char": text[..tokens[start].byte_start].chars().count(),
                        "end_char": text[..tokens[end - 1].byte_end].chars().count(),
                    }));
                }
                Ok((normalize_entities(&raw, Some(text)), Vec::new()))
            }
            SpacyMode::Model(model) => {
                let request = serde_json::json!({ "texts": [text], "model": model });
                let rows = spacy_sidecar(&request)?;
                let entities = rows.into_iter().next().unwrap_or_default();
                Ok((normalize_entities(&entities, Some(text)), Vec::new()))
            }
        }
    }
}

struct TokenOffset {
    lower: String,
    byte_start: usize,
    byte_end: usize,
}

/// Whitespace tokenization + punctuation trimming with byte offsets kept so
/// surfaces can be sliced straight out of the original text. Interior
/// punctuation is emitted as its own single-char token (approximating spaCy's
/// rule tokenizer: `"Plug&Charge"` → `plug`, `&`, `charge`).
fn tokenize_with_offsets(text: &str) -> Vec<TokenOffset> {
    let mut tokens = Vec::new();
    let mut cursor = 0usize;
    for word in text.split_whitespace() {
        // Words arrive in order; locate each from the previous hit onward.
        let Some(rel) = text[cursor..].find(word) else {
            continue;
        };
        let base = cursor + rel;
        cursor = base + word.len();
        let bytes = word.as_bytes();
        let is_punct = |c: u8| c.is_ascii_punctuation();
        let mut index = 0usize;
        while index < word.len() {
            if is_punct(bytes[index]) {
                tokens.push(TokenOffset {
                    lower: word[index..index + 1].to_lowercase(),
                    byte_start: base + index,
                    byte_end: base + index + 1,
                });
                index += 1;
                continue;
            }
            let run_start = index;
            while index < word.len() && !is_punct(bytes[index]) {
                index += 1;
            }
            tokens.push(TokenOffset {
                lower: word[run_start..index].to_lowercase(),
                byte_start: base + run_start,
                byte_end: base + index,
            });
        }
    }
    tokens
}

const SPACY_SIDECAR: &str = r#"
import json, sys
import spacy
req = json.loads(sys.stdin.read())
nlp = spacy.load(req["model"])
out = []
for t in req["texts"]:
    doc = nlp(t)
    out.append([{"name": e.text, "type": e.label_, "start_char": e.start_char, "end_char": e.end_char} for e in doc.ents])
print(json.dumps(out))
"#;

fn spacy_sidecar(request: &Value) -> ProviderResult<Vec<Vec<Value>>> {
    let output = Command::new(python_binary())
        .arg("-c")
        .arg(SPACY_SIDECAR)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .and_then(|mut child| {
            use std::io::Write;
            child
                .stdin
                .as_mut()
                .expect("piped stdin")
                .write_all(request.to_string().as_bytes())?;
            let out = child.wait_with_output()?;
            if out.status.success() {
                Ok(out.stdout)
            } else {
                Err(std::io::Error::other(
                    String::from_utf8_lossy(&out.stderr).into_owned(),
                ))
            }
        })
        .map_err(|e| ProviderError(format!("spacy sidecar failed: {e}")))?;
    serde_json::from_slice(&output).map_err(|e| ProviderError(format!("spacy sidecar output: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gliner_labels_default_and_file() {
        assert_eq!(
            GlinerProvider::parse_labels(None),
            vec![
                "PERSON", "ORG", "PRODUCT", "GPE", "DATE", "TECH", "CRYPTO", "STANDARD"
            ]
        );
        assert_eq!(
            GlinerProvider::parse_labels(Some("")),
            GlinerProvider::parse_labels(None)
        );
        assert_eq!(
            GlinerProvider::parse_labels(Some(" A , B,, C ")),
            vec!["A", "B", "C"]
        );
    }

    #[test]
    fn ruler_rules_load_and_match() {
        let tmp = tempfile::tempdir().unwrap();
        let rules = tmp.path().join("rules.json");
        std::fs::write(
            &rules,
            r#"[{"label": "ORG", "pattern": "Acme Systems"}, {"label": "TECH", "pattern": [{"LOWER": "plug"}, {"LOWER": "&"}, {"LOWER": "charge"}]}]"#,
        )
        .unwrap();
        let mut provider =
            SpacyProvider::new("en_core_web_sm", Some(rules.to_str().unwrap())).unwrap();
        let (entities, relations) = provider
            .extract("Acme Systems enables Plug&Charge.")
            .unwrap();
        assert!(relations.is_empty());
        assert_eq!(entities.len(), 2);
        assert_eq!(entities[0]["type"], "ORG");
        assert_eq!(entities[0]["name"], "Acme Systems");
        assert_eq!(entities[0]["start_char"], serde_json::json!(0));
        assert_eq!(entities[0]["end_char"], serde_json::json!(12));
        assert_eq!(entities[1]["type"], "TECH");
        assert_eq!(entities[1]["name"], "Plug&Charge");
        assert_eq!(entities[1]["start_char"], serde_json::json!(21));
    }

    #[test]
    fn fixture_provider_replays() {
        let payload = serde_json::json!({
            "entities_by_paragraph": {
                "hello": [{"name": "World", "type": "GPE"}]
            }
        });
        let mut provider = FixtureProvider::from_json(&payload);
        let (entities, _) = provider.extract("hello").unwrap();
        assert_eq!(entities.len(), 1);
        let (entities, _) = provider.extract("missing").unwrap();
        assert!(entities.is_empty());
    }
}
