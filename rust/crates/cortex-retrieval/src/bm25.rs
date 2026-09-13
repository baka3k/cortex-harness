//! Port của `code-tiny/tools/common/bm25_ranker.py`.
//!
//! Replicate đúng semantics của `rank_bm25` 0.2.2 (`BM25Okapi`, k1=1.5, b=0.75,
//! epsilon=0.25) và wrapper `BM25Ranker` (normalize theo max, drop raw ≤ 0).
//! Golden fixtures: `tests/fixtures/bm25_golden.json`, sinh bằng
//! `uv run --no-project --with rank_bm25==0.2.2 python scripts/rust_parity/gen_bm25_fixtures.py`.

use std::collections::{BTreeMap, HashMap};

/// Python: `re.findall(r"[a-z0-9_]+", text.lower())`.
fn tokenize(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    for ch in text.to_lowercase().chars() {
        if matches!(ch, 'a'..='z' | '0'..='9' | '_') {
            cur.push(ch);
        } else if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// Tài liệu đầu vào cho index: `id` ứng `id_field`, `text` ứng `text_field` của bản Python.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Document {
    pub id: String,
    pub text: String,
}

impl Document {
    pub fn new(id: impl Into<String>, text: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            text: text.into(),
        }
    }
}

/// BM25Okapi — replicate `rank_bm25.bm25.BM25Okapi` (k1=1.5, b=0.75, epsilon=0.25).
struct Bm25Okapi {
    idf: HashMap<String, f64>,
    doc_freqs: Vec<HashMap<String, f64>>,
    doc_len: Vec<f64>,
    avgdl: f64,
    corpus_size: usize,
    k1: f64,
    b: f64,
}

const K1: f64 = 1.5;
const B: f64 = 0.75;
const EPSILON: f64 = 0.25;

impl Bm25Okapi {
    /// Lệch có chủ ý so với Python: corpus rỗng làm `BM25Okapi([])` chia 0 crash;
    /// ở đây trả `None` để wrapper trả `{}` (xem phase-02, documented divergence).
    fn build(corpus: &[Vec<String>]) -> Option<Self> {
        let corpus_size = corpus.len();
        if corpus_size == 0 {
            return None;
        }

        let mut doc_freqs = Vec::with_capacity(corpus_size);
        let mut doc_len = Vec::with_capacity(corpus_size);
        let mut df: HashMap<String, usize> = HashMap::new();
        for tokens in corpus {
            let mut freq: HashMap<String, f64> = HashMap::new();
            for tok in tokens {
                *freq.entry(tok.clone()).or_insert(0.0) += 1.0;
            }
            for term in freq.keys() {
                *df.entry(term.clone()).or_insert(0) += 1;
            }
            doc_freqs.push(freq);
            doc_len.push(tokens.len() as f64);
        }

        // rank_bm25: idf = ln(N - n + 0.5) - ln(n + 0.5); term idf âm
        // → epsilon * average_idf (average tính trên toàn bộ raw idf của vocab).
        let mut idf = HashMap::with_capacity(df.len());
        let mut idf_sum = 0.0;
        let mut negative = Vec::new();
        for (term, n) in &df {
            let value = (corpus_size as f64 - *n as f64 + 0.5).ln() - (*n as f64 + 0.5).ln();
            idf_sum += value;
            if value < 0.0 {
                negative.push(term.clone());
            }
            idf.insert(term.clone(), value);
        }
        let average_idf = idf_sum / df.len() as f64;
        let eps = EPSILON * average_idf;
        for term in negative {
            idf.insert(term, eps);
        }

        let total_len: f64 = doc_len.iter().sum();
        Some(Self {
            idf,
            doc_freqs,
            doc_len,
            avgdl: total_len / corpus_size as f64,
            corpus_size,
            k1: K1,
            b: B,
        })
    }

    /// Thứ tự cộng điểm replicate `BM25Okapi.get_scores`: duyệt query tokens
    /// theo thứ tự gốc (token lặp cộng mỗi lần), term ngoài vocab bỏ qua.
    /// Biểu thức giữ nguyên thứ tự float của numpy: `idf * (tf*(k1+1)) / denom`.
    fn get_scores(&self, query: &[String]) -> Vec<f64> {
        let mut scores = vec![0.0; self.corpus_size];
        for q in query {
            let Some(&idf) = self.idf.get(q) else {
                continue;
            };
            for (i, doc_freq) in self.doc_freqs.iter().enumerate() {
                let tf = doc_freq.get(q).copied().unwrap_or(0.0);
                let denom = tf + self.k1 * (1.0 - self.b + self.b * self.doc_len[i] / self.avgdl);
                scores[i] += idf * (tf * (self.k1 + 1.0)) / denom;
            }
        }
        scores
    }
}

/// Wrapper tương đương `BM25Ranker` trong Python.
#[derive(Default)]
pub struct Bm25Ranker {
    symbol_ids: Vec<String>,
    bm25: Option<Bm25Okapi>,
}

impl Bm25Ranker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Tương đương `build_index(documents, text_field, id_field)`.
    pub fn build_index(&mut self, documents: &[Document]) {
        self.symbol_ids = documents.iter().map(|d| d.id.clone()).collect();
        let corpus: Vec<Vec<String>> = documents.iter().map(|d| tokenize(&d.text)).collect();
        self.bm25 = Bm25Okapi::build(&corpus);
    }

    /// Tương đương `score(query)` — trả `{symbol_id: score ∈ (0, 1]}`;
    /// rỗng khi index chưa build, query token hoá rỗng, hoặc max raw ≤ 0.
    pub fn score(&self, query: &str) -> BTreeMap<String, f64> {
        let Some(bm25) = &self.bm25 else {
            return BTreeMap::new();
        };
        let tokens = tokenize(query);
        if tokens.is_empty() {
            return BTreeMap::new();
        }
        let raw = bm25.get_scores(&tokens);
        let max_score = raw.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        if max_score <= 0.0 {
            return BTreeMap::new();
        }
        let mut out = BTreeMap::new();
        for (sid, s) in self.symbol_ids.iter().zip(&raw) {
            if *s > 0.0 {
                out.insert(sid.clone(), (*s / max_score).min(1.0));
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ranker(docs: &[(&str, &str)]) -> Bm25Ranker {
        let mut r = Bm25Ranker::new();
        r.build_index(
            &docs
                .iter()
                .map(|(id, text)| Document::new(*id, *text))
                .collect::<Vec<_>>(),
        );
        r
    }

    #[test]
    fn tokenizer_matches_python_regex() {
        assert_eq!(
            tokenize("Payment_Service.processPayment() -- v2!"),
            vec!["payment_service", "processpayment", "v2"]
        );
        // Python .lower() không strip diacritics: "xử lý thanh toán" → runs [a-z0-9_]+
        assert_eq!(
            tokenize("Xử Lý Thanh Toán"),
            vec!["x", "l", "thanh", "to", "n"]
        );
        assert_eq!(tokenize(""), Vec::<String>::new());
    }

    #[test]
    fn empty_query_returns_empty() {
        let r = ranker(&[("a", "alpha beta")]);
        assert!(r.score("").is_empty());
        assert!(r.score("!!!").is_empty());
    }

    #[test]
    fn unbuilt_index_returns_empty() {
        assert!(Bm25Ranker::new().score("anything").is_empty());
    }

    #[test]
    fn empty_corpus_returns_empty() {
        // Python BM25Okapi([]) crash — documented divergence.
        let mut r = Bm25Ranker::new();
        r.build_index(&[]);
        assert!(r.score("term").is_empty());
    }

    #[test]
    fn duplicate_query_term_scores_twice() {
        // Corpus đủ lớn để idf("payment") dương (df=2/6).
        let docs = [
            ("d1", "payment retry policy alpha"),
            ("d2", "payment flow overview beta"),
            ("d3", "gamma delta epsilon zeta"),
            ("d4", "eta theta iota kappa"),
            ("d5", "lambda mu nu xi"),
            ("d6", "omicron pi rho sigma"),
        ];
        let once = ranker(&docs).score("payment");
        let twice = ranker(&docs).score("payment payment");
        assert_eq!(once["d1"], 1.0, "top doc normalize về 1.0");
        assert!(!once["d2"].is_nan() && once["d2"] > 0.0);
        // Token lặp nhân đều raw scores → sau normalize theo max, kết quả giống hệt.
        let diff: f64 = once
            .keys()
            .map(|k| (once[k] - twice[k]).abs())
            .sum();
        assert!(diff < 1e-12, "dup query term phải tuyến tính sau normalize");
    }

    #[test]
    fn negative_idf_terms_use_epsilon_floor() {
        // "term" xuất hiện trong 3/3 doc → idf âm → eps = 0.25 * average_idf
        // (âm, vì term quá phổ biến kéo avg xuống). Docs chỉ khớp "term" nhận raw
        // âm → bị drop theo filter raw > 0; chỉ d1 (khớp thêm "alpha") còn lại.
        // Đây chính là behavior Python (rank_bm25 0.2.2 + BM25Ranker.score).
        let docs = [
            ("d1", "term alpha"),
            ("d2", "term beta"),
            ("d3", "term gamma"),
        ];
        let scores = ranker(&docs).score("term alpha");
        assert_eq!(scores.len(), 1, "docs chỉ khớp negative-idf term bị drop");
        assert!((scores["d1"] - 1.0).abs() < 1e-12);
    }
}
