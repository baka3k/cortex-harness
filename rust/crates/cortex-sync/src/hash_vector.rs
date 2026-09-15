//! `_hash_vector` — port of `code-tiny/tools/common/message_scan.py:393-401`.
//!
//! Phase-06 G3. The plan text labels this "change-detection for vector
//! skip"; the reference code says otherwise: it is the MESSAGE-LANE
//! FALLBACK VECTORIZER (used when the neural embedder is absent or fails —
//! `message_scan.py:477-485`). The parity surface is therefore: token regex
//! over Python's full-Unicode `str.lower()`, SHA-1 of each token as a
//! 160-bit big-endian integer mod `size`, bucket counts, L2 normalization
//! with Python's f64 arithmetic.
//!
//! The modulo is exact: iterating `(r*256 + byte) % size` over the digest
//! bytes computes the same remainder as Python's `int(hexdigest, 16) % size`
//! without any bignum.
//!
//! Wiring note: the consumer is the native message-vector lane (phase-05);
//! the G3 deliverable here is the bit-exact primitive + its fixture gate.
#![allow(dead_code)]

use sha1::{Digest, Sha1};

/// `re.findall(r"[A-Za-z0-9_:.]+", text.lower())` — ASCII class, so the two
/// engines agree once the text is lowercased with Python semantics.
fn token_re() -> &'static regex::Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(r"[A-Za-z0-9_:.]+").expect("ASCII token class compiles")
    })
}

/// `_hash_vector(text, size)`.
pub fn hash_vector(text: &str, size: usize) -> Result<Vec<f64>, String> {
    if size == 0 {
        return Err("hash_vector size must be positive".to_string());
    }
    let mut buckets = vec![0.0f64; size];
    // Python applies str.lower() to the WHOLE text first (context-sensitive
    // final-sigma can therefore depend on neighbours); `to_lowercase` on the
    // same full string keeps that context.
    for token in token_re().find_iter(&text.to_lowercase()) {
        let mut hasher = Sha1::new();
        hasher.update(token.as_str().as_bytes());
        let digest = hasher.finalize();
        let mut remainder: usize = 0;
        for byte in digest.iter() {
            remainder = (remainder * 256 + usize::from(*byte)) % size;
        }
        buckets[remainder] += 1.0;
    }
    let sum_squares: f64 = buckets.iter().map(|value| value * value).sum();
    let norm = sum_squares.sqrt();
    if norm > 0.0 {
        Ok(buckets.iter().map(|value| value / norm).collect())
    } else {
        Ok(buckets)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_text_returns_zero_buckets() {
        let vector = hash_vector("", 4).unwrap();
        assert_eq!(vector, vec![0.0, 0.0, 0.0, 0.0]);
    }

    #[test]
    fn non_positive_size_rejected() {
        assert!(hash_vector("x", 0).is_err());
    }

    #[test]
    fn single_token_bucket_matches_python_reference() {
        // Reference: python3 -c "import hashlib; print(int(hashlib.sha1(b'hello').hexdigest(),16) % 1024)" → 845
        let vector = hash_vector("hello", 1024).unwrap();
        let hot: Vec<usize> = vector
            .iter()
            .enumerate()
            .filter(|(_, value)| **value > 0.0)
            .map(|(index, _)| index)
            .collect();
        assert_eq!(hot.len(), 1);
        assert_eq!(
            hot[0], 845,
            "sha1('hello') 160-bit big-endian % 1024 must equal Python"
        );
        let norm: f64 = vector.iter().map(|value| value * value).sum();
        assert!((norm - 1.0).abs() < 1e-12);
    }
}
