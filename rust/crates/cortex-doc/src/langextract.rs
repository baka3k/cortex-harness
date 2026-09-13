//! Direct port of `entity_extractors.extract_entities_langextract` — the LLM
//! API call path (Gemini / OpenAI / Ollama-compatible) over `reqwest`, plus
//! `graphrag_query_langextract.llm_generate`.
//!
//! Parity note: the wire prompt is assembled from the identical prompt text
//! and few-shot examples; langextract's internal example serialization is an
//! implementation detail of that library, so live-LLM output is excluded
//! from the parity gate (the deterministic normalization + graph assembly of
//! its results IS covered).

use std::time::Duration;

use serde_json::Value;

use crate::entity::{normalize_entities, normalize_relations};
use crate::providers::{EntityProvider, ProviderError, ProviderResult};

/// Prompt description of `extract_entities_langextract` (byte-identical).
pub const EXTRACTION_PROMPT: &str = "You are an information extraction system. Extract high-signal entities and explicit\n\
relations from the input. Use exact text spans from the input for extraction_text.\n\
Prefer canonical names (no pronouns) and avoid generic words (e.g., \"system\", \"device\")\n\
unless they are clearly named entities in the text.\n\
\n\
Entity rules:\n\
- Include people, orgs, products, standards, protocols, locations, and key technical terms.\n\
- Use concise, normalized entity types: PERSON, ORG, PRODUCT, GPE, DATE, TECH, CRYPTO, STANDARD.\n\
- Deduplicate entities by exact surface form; do not invent new names.\n\
\n\
Relation rules:\n\
- Only create relations that are stated or strongly implied in the text.\n\
- Use short relation labels in UPPER_SNAKE_CASE (e.g., EMPLOYED_BY, PART_OF, USES, LOCATED_IN).\n\
- Source/target must match entity text spans.\n\
\n\
Classes:\n\
- entity: attributes {\"type\": \"...\"}.\n\
- relation: attributes {\"source\": \"...\", \"relation\": \"...\", \"target\": \"...\"}.";

/// Few-shot examples of `extract_entities_langextract` (same content, rendered
/// into the prompt as JSON lines).
pub const FEW_SHOT_EXAMPLES: &str = r#"Example 1:
text: Alice works at Acme and knows Bob.
extractions:
  - {"class": "entity", "text": "Alice", "attributes": {"type": "PERSON"}}
  - {"class": "entity", "text": "Acme", "attributes": {"type": "ORG"}}
  - {"class": "entity", "text": "Bob", "attributes": {"type": "PERSON"}}
  - {"class": "relation", "text": "works at", "attributes": {"source": "Alice", "relation": "EMPLOYED_BY", "target": "Acme"}}
  - {"class": "relation", "text": "knows", "attributes": {"source": "Alice", "relation": "KNOWS", "target": "Bob"}}

Example 2:
text: ISO 15118 enables Plug&Charge for the Digital Key project in Berlin.
extractions:
  - {"class": "entity", "text": "ISO 15118", "attributes": {"type": "STANDARD"}}
  - {"class": "entity", "text": "Plug&Charge", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "Digital Key", "attributes": {"type": "PRODUCT"}}
  - {"class": "entity", "text": "Berlin", "attributes": {"type": "GPE"}}
  - {"class": "relation", "text": "enables", "attributes": {"source": "ISO 15118", "relation": "ENABLES", "target": "Plug&Charge"}}
  - {"class": "relation", "text": "for", "attributes": {"source": "Plug&Charge", "relation": "USED_IN", "target": "Digital Key"}}
  - {"class": "relation", "text": "in", "attributes": {"source": "Digital Key", "relation": "LOCATED_IN", "target": "Berlin"}}

Example 3:
text: Car Connectivity Consortium (CCC) defines Digital Key. Digital Key uses AES-256 and ECC for encryption and key exchange. ISO/IEC 18013-5 specifies mDL security requirements.
extractions:
  - {"class": "entity", "text": "Car Connectivity Consortium", "attributes": {"type": "ORG"}}
  - {"class": "entity", "text": "CCC", "attributes": {"type": "ORG"}}
  - {"class": "entity", "text": "Digital Key", "attributes": {"type": "PRODUCT"}}
  - {"class": "entity", "text": "AES-256", "attributes": {"type": "CRYPTO"}}
  - {"class": "entity", "text": "ECC", "attributes": {"type": "CRYPTO"}}
  - {"class": "entity", "text": "ISO/IEC 18013-5", "attributes": {"type": "STANDARD"}}
  - {"class": "relation", "text": "defines", "attributes": {"source": "Car Connectivity Consortium", "relation": "DEFINES", "target": "Digital Key"}}
  - {"class": "relation", "text": "uses", "attributes": {"source": "Digital Key", "relation": "USES", "target": "AES-256"}}
  - {"class": "relation", "text": "uses", "attributes": {"source": "Digital Key", "relation": "USES", "target": "ECC"}}
  - {"class": "relation", "text": "specifies", "attributes": {"source": "ISO/IEC 18013-5", "relation": "SPECIFIES", "target": "mDL security requirements"}}

Example 4:
text: The mobile endpoint uses PKI for authentication. Host Card Emulation (HCE) enables the mobile device to emulate a card for the vehicle. The vehicle endpoint validates the mobile endpoint over NFC.
extractions:
  - {"class": "entity", "text": "mobile endpoint", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "PKI", "attributes": {"type": "CRYPTO"}}
  - {"class": "entity", "text": "Host Card Emulation", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "HCE", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "mobile device", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "vehicle", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "vehicle endpoint", "attributes": {"type": "TECH"}}
  - {"class": "entity", "text": "NFC", "attributes": {"type": "TECH"}}
  - {"class": "relation", "text": "uses", "attributes": {"source": "mobile endpoint", "relation": "USES", "target": "PKI"}}
  - {"class": "relation", "text": "enables", "attributes": {"source": "Host Card Emulation", "relation": "ENABLES", "target": "mobile device"}}
  - {"class": "relation", "text": "emulate", "attributes": {"source": "mobile device", "relation": "EMULATES", "target": "card"}}
  - {"class": "relation", "text": "validates", "attributes": {"source": "vehicle endpoint", "relation": "VALIDATES", "target": "mobile endpoint"}}
  - {"class": "relation", "text": "over", "attributes": {"source": "vehicle endpoint", "relation": "COMMUNICATES_OVER", "target": "NFC"}}
"#;

/// `_extract_json_payload` — strip markdown fences from a raw LLM reply.
pub fn extract_json_payload(raw: &str) -> String {
    let content = raw.trim();
    if content.is_empty() {
        return "{}".to_string();
    }
    if content.starts_with("```") {
        let kept: Vec<&str> = content
            .lines()
            .filter(|line| !line.trim_start().starts_with("```"))
            .collect();
        let joined = kept.join("\n");
        let trimmed = joined.trim();
        return if trimmed.is_empty() {
            "{}".to_string()
        } else {
            trimmed.to_string()
        };
    }
    content.to_string()
}

fn env(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.trim().is_empty())
}

fn env_flag(name: &str) -> Option<bool> {
    let value = env(name)?;
    match value.trim().to_lowercase().as_str() {
        "1" | "true" | "yes" | "y" | "on" => Some(true),
        "0" | "false" | "no" | "n" | "off" => Some(false),
        _ => None,
    }
}

/// Langextract entity provider (LLM API calls via reqwest).
pub struct LangextractProvider {
    api_key: Option<String>,
    model_id: String,
    model_url: Option<String>,
    fence_output: Option<bool>,
    use_schema_constraints: bool,
    max_attempts: usize,
    backoff_seconds: f64,
}

impl LangextractProvider {
    /// Env handling mirrors `extract_entities_langextract`:
    /// `LANGEXTRACT_API_KEY` / `LANGEXTRACT_MODEL_ID` / `LANGEXTRACT_MODEL_URL`,
    /// `LANGEXTRACT_FENCE_OUTPUT`, `LANGEXTRACT_USE_SCHEMA_CONSTRAINTS`,
    /// `LLM_RETRY_COUNT`, `LLM_RETRY_BACKOFF_SECONDS`.
    pub fn from_env() -> ProviderResult<Self> {
        let api_key = env("LANGEXTRACT_API_KEY");
        let model_id =
            env("LANGEXTRACT_MODEL_ID").unwrap_or_else(|| "gemini-2.5-flash".to_string());
        let model_url = env("LANGEXTRACT_MODEL_URL");
        let mut fence_output = env_flag("LANGEXTRACT_FENCE_OUTPUT");
        let mut use_schema_constraints =
            env_flag("LANGEXTRACT_USE_SCHEMA_CONSTRAINTS").unwrap_or(true);
        let lower_model = model_id.to_lowercase();
        if lower_model.starts_with("gpt-") || lower_model.starts_with('o') {
            if env_flag("LANGEXTRACT_USE_SCHEMA_CONSTRAINTS").is_none() {
                use_schema_constraints = false;
            }
            if env_flag("LANGEXTRACT_FENCE_OUTPUT").is_none() {
                fence_output = Some(true);
            }
        }
        let attempts_raw = env("LLM_RETRY_COUNT").unwrap_or_else(|| "3".to_string());
        let max_attempts = attempts_raw.parse::<usize>().map(|v| v.max(1)).unwrap_or(3);
        let backoff_raw = env("LLM_RETRY_BACKOFF_SECONDS").unwrap_or_else(|| "2".to_string());
        let backoff_seconds = backoff_raw
            .parse::<f64>()
            .map(|v| v.max(0.0))
            .unwrap_or(2.0);
        Ok(Self {
            api_key,
            model_id,
            model_url,
            fence_output,
            use_schema_constraints,
            max_attempts,
            backoff_seconds,
        })
    }

    /// The `--entity-provider gemini` path of `build_graph_components`
    /// ( backed by `extract_entities_gemini`): `GEMINI_API_KEY` +
    /// `GEMINI_MODEL` (default `gemini-1.5-flash`), flat schema reply.
    pub fn from_gemini_env() -> ProviderResult<Self> {
        let api_key = env("GEMINI_API_KEY")
            .or_else(|| env("LANGEXTRACT_API_KEY"))
            .ok_or_else(|| ProviderError("Missing GEMINI_API_KEY env var".into()))?;
        let model_id = env("GEMINI_MODEL").unwrap_or_else(|| "gemini-1.5-flash".to_string());
        Ok(Self {
            api_key: Some(api_key),
            model_id,
            model_url: None,
            fence_output: Some(true),
            use_schema_constraints: true,
            max_attempts: 3,
            backoff_seconds: 2.0,
        })
    }

    fn build_prompt(&self, text: &str, fence: bool) -> String {
        let output_contract = if fence {
            "Return ONLY a JSON object: {\"extractions\": [{\"class\": \"entity\"|\"relation\", \"text\": \"...\", \"attributes\": {...}}]}"
        } else {
            "Return the extraction list as strict JSON with no surrounding prose."
        };
        format!(
            "{EXTRACTION_PROMPT}\n\n{FEW_SHOT_EXAMPLES}\nOutput format: {output_contract}\n\nText:\n{text}"
        )
    }

    /// Single LLM call, dispatched like langextract by model-id prefix.
    fn call_llm(&self, prompt: &str, fence: bool) -> ProviderResult<String> {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .map_err(|e| ProviderError(format!("http client: {e}")))?;
        let lower_model = self.model_id.to_lowercase();
        if lower_model.starts_with("gemini") {
            let key = self
                .api_key
                .clone()
                .or_else(|| env("GEMINI_API_KEY"))
                .ok_or_else(|| ProviderError("Missing LANGEXTRACT_API_KEY for Gemini.".into()))?;
            let url = format!(
                "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent",
                self.model_id
            );
            let mut body = serde_json::json!({
                "contents": [{"parts": [{"text": prompt}]}],
            });
            if fence {
                body["generationConfig"] =
                    serde_json::json!({"responseMimeType": "application/json"});
            }
            let response = client
                .post(url)
                .header("x-goog-api-key", key)
                .json(&body)
                .send()
                .map_err(|e| ProviderError(format!("gemini request: {e}")))?;
            let payload: Value = response
                .json()
                .map_err(|e| ProviderError(format!("gemini response: {e}")))?;
            if let Some(error) = payload.get("error") {
                return Err(ProviderError(format!(
                    "gemini api: {}",
                    error
                        .get("message")
                        .and_then(Value::as_str)
                        .unwrap_or("unknown")
                )));
            }
            let text = payload["candidates"][0]["content"]["parts"][0]["text"]
                .as_str()
                .unwrap_or("");
            return Ok(text.to_string());
        }
        if lower_model.starts_with("gpt-") || lower_model.starts_with('o') {
            let key = self
                .api_key
                .clone()
                .or_else(|| env("OPENAI_API_KEY"))
                .ok_or_else(|| ProviderError("Missing LANGEXTRACT_API_KEY for OpenAI.".into()))?;
            let mut body = serde_json::json!({
                "model": self.model_id,
                "messages": [{"role": "user", "content": prompt}],
            });
            if fence {
                body["response_format"] = serde_json::json!({"type": "json_object"});
            }
            let response = client
                .post("https://api.openai.com/v1/chat/completions")
                .header("Authorization", format!("Bearer {key}"))
                .json(&body)
                .send()
                .map_err(|e| ProviderError(format!("openai request: {e}")))?;
            let payload: Value = response
                .json()
                .map_err(|e| ProviderError(format!("openai response: {e}")))?;
            let text = payload["choices"][0]["message"]["content"]
                .as_str()
                .unwrap_or("");
            return Ok(text.to_string());
        }
        if let Some(url) = &self.model_url {
            return ollama_generate(prompt, &self.model_id, url);
        }
        Err(ProviderError(format!(
            "Unsupported model_id for extraction: {}",
            self.model_id
        )))
    }

    /// Parse a raw LLM reply into `(entities, relations)` normalized payloads.
    /// Accepts both the langextract `{"extractions": [...]}` shape and the
    /// flat `{"entities": [...], "relations": [...]}` schema.
    pub fn parse_reply(&self, raw: &str, text: &str) -> (Vec<Value>, Vec<Value>) {
        let content = extract_json_payload(raw);
        let data: Value = serde_json::from_str(&content).unwrap_or(Value::Null);
        let mut entities: Vec<Value> = Vec::new();
        let mut relations: Vec<Value> = Vec::new();
        if let Some(extractions) = data.get("extractions").and_then(Value::as_array) {
            for extraction in extractions {
                let class = extraction
                    .get("class")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .trim()
                    .to_lowercase();
                let text_value = extraction
                    .get("text")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .trim()
                    .to_string();
                let attrs = extraction.get("attributes").cloned().unwrap_or(Value::Null);
                if class == "entity" {
                    let attr_type = attrs
                        .get("type")
                        .map(|v| match v {
                            Value::String(s) => s.clone(),
                            other => other.to_string(),
                        })
                        .unwrap_or_default();
                    entities.push(serde_json::json!({
                        "name": text_value,
                        "type": if attr_type.is_empty() { "UNKNOWN".to_string() } else { attr_type },
                    }));
                } else if class == "relation" {
                    let attr = |key: &str| {
                        attrs
                            .get(key)
                            .map(|v| match v {
                                Value::String(s) => s.clone(),
                                other => other.to_string(),
                            })
                            .unwrap_or_default()
                    };
                    relations.push(serde_json::json!({
                        "source": attr("source"),
                        "relation": attr("relation"),
                        "target": attr("target"),
                    }));
                }
            }
        } else {
            if let Some(list) = data.get("entities").and_then(Value::as_array) {
                entities = list.clone();
            }
            if let Some(list) = data.get("relations").and_then(Value::as_array) {
                relations = list.clone();
            }
        }
        (
            normalize_entities(&entities, Some(text)),
            normalize_relations(&relations),
        )
    }
}

impl EntityProvider for LangextractProvider {
    fn extract(&mut self, text: &str) -> ProviderResult<(Vec<Value>, Vec<Value>)> {
        // Retry ladder of `_run_langextract`: initial flags, then strict
        // fenced JSON, then fenced without schema constraints.
        let mut retry_plan: Vec<(Option<bool>, bool)> = vec![
            (self.fence_output, self.use_schema_constraints),
            (Some(true), true),
            (Some(true), false),
        ];
        while retry_plan.len() < self.max_attempts {
            retry_plan.push((Some(true), false));
        }
        let mut last_error = String::new();
        for attempt in 0..self.max_attempts {
            let (fence_opt, _schema_constraints) = retry_plan[attempt.min(retry_plan.len() - 1)];
            let fence = fence_opt.unwrap_or(false);
            let prompt = self.build_prompt(text, fence);
            match self.call_llm(&prompt, fence) {
                Ok(raw) => {
                    let (entities, relations) = self.parse_reply(&raw, text);
                    if !entities.is_empty() || !relations.is_empty() || raw.trim().is_empty() {
                        return Ok((entities, relations));
                    }
                    last_error = "empty extraction payload".to_string();
                }
                Err(error) => last_error = error.to_string(),
            }
            if attempt + 1 < self.max_attempts && self.backoff_seconds > 0.0 {
                std::thread::sleep(Duration::from_secs_f64(self.backoff_seconds));
            }
        }
        // Python prints + returns empty after exhausting retries.
        eprintln!("LangExtract retry failed, skipping paragraph: {last_error}");
        Ok((Vec::new(), Vec::new()))
    }
}

/// Port of `graphrag_query_langextract.llm_generate` (Gemini / OpenAI /
/// Ollama-compatible), used for the optional answer stage.
pub fn llm_generate(
    prompt: &str,
    model_id: &str,
    model_url: Option<&str>,
) -> ProviderResult<String> {
    let model_id = model_id.trim();
    let lower_model = model_id.to_lowercase();
    if lower_model.starts_with("gemini") {
        let api_key = env("GEMINI_API_KEY")
            .or_else(|| env("LANGEXTRACT_API_KEY"))
            .ok_or_else(|| {
                ProviderError("Missing GEMINI_API_KEY or LANGEXTRACT_API_KEY for Gemini.".into())
            })?;
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .map_err(|e| ProviderError(e.to_string()))?;
        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
        );
        let body = serde_json::json!({"contents": [{"parts": [{"text": prompt}]}]});
        let response = client
            .post(url)
            .header("x-goog-api-key", api_key)
            .json(&body)
            .send()
            .map_err(|e| ProviderError(e.to_string()))?;
        let payload: Value = response.json().map_err(|e| ProviderError(e.to_string()))?;
        return Ok(payload["candidates"][0]["content"]["parts"][0]["text"]
            .as_str()
            .unwrap_or("")
            .to_string());
    }
    if lower_model.starts_with("gpt-") || lower_model.starts_with('o') {
        let api_key = env("OPENAI_API_KEY").or_else(|| env("LANGEXTRACT_API_KEY"));
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .map_err(|e| ProviderError(e.to_string()))?;
        let body = serde_json::json!({
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": "Answer the question using the provided graph context and citations if possible.",
                },
                {"role": "user", "content": prompt},
            ],
        });
        let mut request = client
            .post("https://api.openai.com/v1/chat/completions")
            .json(&body);
        if let Some(key) = api_key {
            request = request.header("Authorization", format!("Bearer {key}"));
        }
        let response = request.send().map_err(|e| ProviderError(e.to_string()))?;
        let payload: Value = response.json().map_err(|e| ProviderError(e.to_string()))?;
        return Ok(payload["choices"][0]["message"]["content"]
            .as_str()
            .unwrap_or("")
            .to_string());
    }
    if let Some(url) = model_url {
        return ollama_generate(prompt, model_id, url);
    }
    Err(ProviderError(format!(
        "Unsupported model_id for generation: {model_id}"
    )))
}

/// `_ollama_generate` — POST `{url}/api/generate` with `stream: false`.
pub fn ollama_generate(prompt: &str, model_id: &str, model_url: &str) -> ProviderResult<String> {
    let endpoint = format!("{}/api/generate", model_url.trim_end_matches('/'));
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| ProviderError(e.to_string()))?;
    let response = client
        .post(endpoint)
        .json(&serde_json::json!({
            "model": model_id,
            "prompt": prompt,
            "stream": false,
        }))
        .send()
        .map_err(|e| ProviderError(e.to_string()))?;
    let payload: Value = response.json().map_err(|e| ProviderError(e.to_string()))?;
    Ok(payload
        .get("response")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fence_stripping() {
        assert_eq!(
            extract_json_payload("```json\n{\"a\": 1}\n```"),
            "{\"a\": 1}"
        );
        assert_eq!(extract_json_payload("{\"a\": 1}"), "{\"a\": 1}");
        assert_eq!(extract_json_payload(""), "{}");
    }

    #[test]
    fn parse_langextract_shape() {
        let provider = LangextractProvider::from_env().unwrap();
        let raw = r#"{"extractions": [
            {"class": "entity", "text": "Acme", "attributes": {"type": "ORG"}},
            {"class": "relation", "text": "in", "attributes": {"source": "Acme", "relation": "LOCATED_IN", "target": "Hanoi"}}
        ]}"#;
        let (entities, relations) = provider.parse_reply(raw, "Acme in Hanoi");
        assert_eq!(entities.len(), 1);
        assert_eq!(entities[0]["name"], "Acme");
        assert_eq!(entities[0]["type"], "ORG");
        assert_eq!(entities[0]["start_char"], serde_json::json!(0));
        assert_eq!(relations.len(), 1);
        assert_eq!(relations[0]["relation"], "LOCATED_IN");
    }

    #[test]
    fn parse_flat_gemini_shape() {
        let provider = LangextractProvider::from_env().unwrap();
        let raw = r#"{"entities": [{"name": "ISO 15118", "type": "STANDARD"}], "relations": []}"#;
        let (entities, relations) = provider.parse_reply(raw, "ISO 15118");
        assert_eq!(entities.len(), 1);
        assert_eq!(entities[0]["type"], "STANDARD");
        assert!(relations.is_empty());
    }

    #[test]
    fn parse_invalid_payload_is_empty_not_error() {
        let provider = LangextractProvider::from_env().unwrap();
        let (entities, relations) = provider.parse_reply("not json at all", "x");
        assert!(entities.is_empty());
        assert!(relations.is_empty());
    }
}
