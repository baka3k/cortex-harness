//! Số học pooling/normalize — phần duy nhất của crate test được mà không cần
//! weight model (CI chạy `make rust-check` không có artifact vài GB).
//!
//! Công thức lấy đúng từ reference Python đã đối chiếu ở
//! `plans/260914-1706-onnx-embedding-spike/findings.md`:
//! - mean: `sum(h*mask)/clamp(sum(mask),1e-9)` — `mean_pooling` trong
//!   `xlm-roberta-flash-implementation/modeling_xlm_roberta.py:653-661` và
//!   `SentenceTransformer` `Pooling._forward_padded`.
//! - cls: `h[:,0]` — `bge-m3/1_Pooling/config.json: pooling_mode_cls_token=true`.
//! - normalize: module `2_Normalize` có trong `modules.json` của CẢ HAI model.
//! - chunk mean: `sum/count` bằng Python float (f64) và **không** re-normalize
//!   (`python_analyzer.py:1051-1074`) => vector multi-chunk có norm < 1.

/// Cộng dồn mean-pool theo attention mask. `tokens` là row-major `(seq, hidden)`.
///
/// Reference Python dùng `clamp(min=1e-9)` cho mẫu số (jina/ST) còn
/// `embed_runtime.mean_pool` dùng `clamp(min=1)` — hai cái chỉ khác nhau khi
/// seq toàn pad, không xảy ra vì special tokens luôn tồn tại, nên dùng một public
/// dạng 1e-9 cho cả hai.
pub fn mean_pool(tokens: &[f32], seq: usize, hidden: usize, mask: &[i32]) -> Vec<f32> {
    let mut out = vec![0f32; hidden];
    let mut count = 0f32;
    for step in 0..seq {
        if mask.get(step).copied().unwrap_or(0) == 0 {
            continue;
        }
        count += 1.0;
        let base = step * hidden;
        for (slot, value) in tokens[base..base + hidden].iter().copied().enumerate() {
            out[slot] += value;
        }
    }
    let denom = count.max(1e-9);
    for value in &mut out {
        *value /= denom;
    }
    out
}

/// CLS pooling: hàng token đầu tiên (đã có special token ở mọi lane Python).
pub fn cls_pool(tokens: &[f32], hidden: usize) -> Vec<f32> {
    tokens[..hidden.min(tokens.len())].to_vec()
}

/// L2-normalize; trả về norm trước khi chia (0.0 khi vector rỗng/đ-zero, giữ
/// nguyên vector — khác Python ở cho `norm <= 0` nhưng không lane nào chạm).
pub fn l2_normalize(vector: &mut [f32]) -> f32 {
    let norm = vector.iter().map(|value| value * value).sum::<f32>().sqrt();
    if norm > 0.0 {
        for value in &mut *vector {
            *value /= norm;
        }
    }
    norm
}

/// `--chunk-embed`: trung bình cộng các vector chunk đã unit-norm, cộng dồn f64
/// theo thứ tự chèn như Python, **không** re-normalize.
pub fn mean_of_chunks(chunks: &[Vec<f32>], dimension: usize) -> Vec<f32> {
    let mut sums = vec![0f64; dimension];
    let mut count = 0u64;
    for chunk in chunks {
        if chunk.len() != dimension {
            continue;
        }
        count += 1;
        for (slot, value) in chunk.iter().copied().enumerate() {
            sums[slot] += f64::from(value);
        }
    }
    if count == 0 {
        return Vec::new();
    }
    let denom = count as f64;
    sums.iter().map(|value| (value / denom) as f32).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(actual: &[f32], expected: &[f32], tol: f32) -> bool {
        actual.len() == expected.len()
            && actual
                .iter()
                .zip(expected)
                .all(|(a, e)| (a - e).abs() <= tol)
    }

    #[test]
    fn mean_pool_ignores_pad_positions() {
        // 2 token x 3 hidden: [1,0,0] và pad [7,7,7] -> chỉ token đầu được tính.
        let tokens = vec![1.0, 0.0, 0.0, 7.0, 7.0, 7.0];
        assert!(approx(
            &mean_pool(&tokens, 2, 3, &[1, 0]),
            &[1.0, 0.0, 0.0],
            1e-7
        ));
    }

    #[test]
    fn mean_pool_averages_real_tokens() {
        let tokens = vec![2.0, -4.0, 8.0, 4.0, 2.0, 0.0];
        assert!(approx(
            &mean_pool(&tokens, 2, 3, &[1, 1]),
            &[3.0, -1.0, 4.0],
            1e-7
        ));
    }

    #[test]
    fn mean_pool_of_all_pad_is_zero_not_nan() {
        let pooled = mean_pool(&[1.0, 2.0], 1, 2, &[0]);
        assert!(pooled.iter().all(|value| value.is_finite()));
        assert!(approx(&pooled, &[0.0, 0.0], 1e-7));
    }

    #[test]
    fn cls_pool_takes_first_row() {
        assert_eq!(cls_pool(&[9.0, 8.0, 1.0, 2.0], 2), vec![9.0, 8.0]);
    }

    #[test]
    fn normalize_produces_unit_vector() {
        let mut vector = vec![3.0, 4.0];
        let norm = l2_normalize(&mut vector);
        assert!((norm - 5.0).abs() < 1e-6);
        assert!((vector[0] - 0.6).abs() < 1e-6);
        assert!((vector[1] - 0.8).abs() < 1e-6);
    }

    #[test]
    fn normalize_leaves_zero_vector_alone() {
        let mut vector = vec![0.0, 0.0];
        assert_eq!(l2_normalize(&mut vector), 0.0);
        assert_eq!(vector, vec![0.0, 0.0]);
    }

    #[test]
    fn chunk_mean_is_not_renormalized() {
        // Hai unit vector trực giao -> norm = 1/sqrt(2) < 1 (đúng behavior Python).
        let chunks = vec![vec![1.0, 0.0], vec![0.0, 1.0]];
        let merged = mean_of_chunks(&chunks, 2);
        assert!(approx(&merged, &[0.5, 0.5], 1e-7));
        let norm = merged.iter().map(|v| v * v).sum::<f32>().sqrt();
        assert!((norm - std::f32::consts::FRAC_1_SQRT_2).abs() < 1e-6);
    }

    #[test]
    fn chunk_mean_skips_wrong_dimension() {
        // Chỉ chunk đúng dimension được tính: [2,3] là một trong một => chính nó.
        assert!(approx(
            &mean_of_chunks(&[vec![1.0], vec![2.0, 3.0]], 2),
            &[2.0, 3.0],
            1e-7
        ));
    }

    #[test]
    fn chunk_mean_returns_empty_when_no_chunk_matches() {
        assert!(mean_of_chunks(&[vec![1.0], vec![2.0]], 3).is_empty());
        assert!(mean_of_chunks(&[], 2).is_empty());
    }
}
