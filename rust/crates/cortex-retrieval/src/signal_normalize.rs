//! Port của `code-tiny/tools/common/signal_normalizer.py`.
//!
//! Normalize heterogeneous retrieval signals về [0, 1] để blend weighted.
//! Golden fixtures: `tests/fixtures/signal_golden.json`.

/// Tương đương `clamp(value, lo, hi)` — NaN → 0.0.
pub fn clamp(value: f64, lo: f64, hi: f64) -> f64 {
    if value.is_nan() {
        return 0.0;
    }
    lo.max(hi.min(value))
}

/// Python `round(x, 6)` — CPython dùng round-half-even trên biểu diễn thập phân
/// chính xác của double; Rust `{:.6}` formatting cũng round-half-even trên cùng
/// biểu diễn → kết quả giống hệt.
fn round6(value: f64) -> f64 {
    format!("{value:.6}").parse().unwrap_or(value)
}

/// `hi - lo`, hoặc 1.0 nếu span ≤ 1e-12 (avoid ZeroDivisionError).
fn safe_range(lo: f64, hi: f64) -> f64 {
    let r = hi - lo;
    if r > 1e-12 {
        r
    } else {
        1.0
    }
}

/// Min-max normalize list về [0, 1].
/// Edge cases giống Python: rỗng → [], tất cả identical → tất cả 0.0.
pub fn min_max_normalize(values: &[f64]) -> Vec<f64> {
    if values.is_empty() {
        return Vec::new();
    }
    let lo = values.iter().cloned().fold(f64::INFINITY, f64::min);
    let hi = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let span = safe_range(lo, hi);
    if (hi - lo).abs() < 1e-12 {
        return vec![0.0; values.len()];
    }
    values.iter().map(|v| (v - lo) / span).collect()
}

/// Tương đương `batch_normalize_signal` (phần tính toán): normalize `values`
/// theo anchor `lo/hi` nếu cả hai có, ngược lại min-max theo sample;
/// kết quả `round(x, 6)` như Python.
/// `values` cho phép `None` — Python `float(c.get(key) or 0.0)` → None/0 → 0.0.
pub fn batch_normalize(values: &[Option<f64>], lo: Option<f64>, hi: Option<f64>) -> Vec<f64> {
    let raw: Vec<f64> = values
        .iter()
        .map(|v| v.filter(|v| *v != 0.0).unwrap_or(0.0))
        .collect();
    let normed = match (lo, hi) {
        (Some(lo), Some(hi)) => {
            let span = safe_range(lo, hi);
            raw.iter()
                .map(|v| clamp((v - lo) / span, 0.0, 1.0))
                .collect::<Vec<_>>()
        }
        _ => min_max_normalize(&raw),
    };
    normed.into_iter().map(round6).collect()
}

/// Bounds mặc định cho các signal quen thuộc (copy nguyên từ `_DEFAULT_BOUNDS`).
pub fn default_bounds(key: &str) -> Option<(f64, f64)> {
    match key {
        "semantic" | "graph" | "freshness" | "confidence" | "usage" => Some((0.0, 1.0)),
        "keyword" => Some((0.0, 25.0)),
        _ => None,
    }
}

/// Tương đương `normalize_signals(raw, signal_bounds)` — normalize signal dict
/// cho 1 candidate. Bounds override theo key; key lạ → (0.0, 1.0).
pub fn normalize_signals(
    raw: &[(&str, f64)],
    signal_bounds: &[(&str, (f64, f64))],
) -> Vec<(String, f64)> {
    raw.iter()
        .map(|(key, value)| {
            let (lo, hi) = signal_bounds
                .iter()
                .find(|(k, _)| k == key)
                .map(|(_, b)| *b)
                .or_else(|| default_bounds(key))
                .unwrap_or((0.0, 1.0));
            let span = safe_range(lo, hi);
            let normed = clamp((value - lo) / span, 0.0, 1.0);
            ((*key).to_string(), round6(normed))
        })
        .collect()
}

/// Freshness theo exponential decay: `exp(-ln(2) * elapsed / half_life)`.
pub fn freshness_from_elapsed(elapsed_seconds: f64, half_life_days: f64) -> f64 {
    if elapsed_seconds <= 0.0 {
        return 1.0;
    }
    let half_life_s = half_life_days * 86_400.0;
    (-(2.0f64.ln()) * elapsed_seconds / half_life_s).exp()
}

/// Tương đương `freshness_from_dirty`:
/// dirty → 1.0; ISO có offset/Z hợp lệ → decay theo `now`;
/// ISO rỗng/lỗi parse → 0.3.
///
/// Lệch có chủ ý: Python crash `TypeError` với ISO naive (thiếu offset) do
/// trừ naive với aware datetime mà chỉ catch `ValueError`; Rust trả 0.3.
pub fn freshness_from_dirty_at(is_dirty: bool, last_updated_iso: &str, now_epoch_s: f64) -> f64 {
    if is_dirty {
        return 1.0;
    }
    if !last_updated_iso.is_empty()
        && let Some(ts) = parse_iso8601_utc(last_updated_iso)
    {
        let elapsed = now_epoch_s - ts;
        return freshness_from_elapsed(elapsed, 30.0);
    }
    0.3
}

/// Parse ISO-8601 timestamp (bắt buộc có offset hoặc `Z`) → epoch seconds UTC.
/// Trả None cho naive timestamp (Python sẽ crash TypeError — xem doc comment).
/// Fractional seconds được bỏ qua (độ chính xác giây — đủ cho age/freshness).
pub fn parse_iso8601_utc(s: &str) -> Option<f64> {
    let s = s.trim();
    let (body, offset_s) = if let Some(stripped) = s.strip_suffix('Z') {
        (stripped, 0.0)
    } else {
        // rfind sau vị trí giờ (tránh dấu '-' trong date "2026-01-01");
        // không có offset hoặc offset lệch format → naive timestamp
        // (Python crash TypeError, Rust trả None — documented divergence).
        let pos = s.rfind(['+', '-'])?;
        let candidate = &s[pos..];
        let re = regex::Regex::new(r"^[+-]\d{2}:?\d{2}$").ok()?;
        if !re.is_match(candidate) {
            return None;
        }
        let sign = if candidate.starts_with('-') { -1.0 } else { 1.0 };
        let digits: String = candidate[1..].chars().filter(|c| *c != ':').collect();
        let hours: f64 = digits[0..2].parse().ok()?;
        let minutes: f64 = digits[2..4].parse().ok()?;
        (&s[..pos], sign * (hours * 3600.0 + minutes * 60.0))
    };

    let parts: Vec<&str> = body
        .split(['-', 'T', ':', '.'])
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() < 6 {
        return None;
    }
    let year: f64 = parts[0].parse().ok()?;
    let month: f64 = parts[1].parse().ok()?;
    let day: f64 = parts[2].parse().ok()?;
    let hour: f64 = parts[3].parse().ok()?;
    let minute: f64 = parts[4].parse().ok()?;
    let second: f64 = parts[5].parse().ok()?;
    let days = days_from_civil(year as i64, month as u32, day as u32)?;
    Some(days as f64 * 86400.0 + hour * 3600.0 + minute * 60.0 + second - offset_s)
}

/// Days since 1970-01-01 (Howard Hinnant's algorithm).
fn days_from_civil(y: i64, m: u32, d: u32) -> Option<i64> {
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
        return None;
    }
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = ((m + 9) % 12) as i64;
    let doy = (153 * mp + 2) / 5 + d as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146097 + doe - 719468)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamp_handles_nan_and_bounds() {
        assert_eq!(clamp(f64::NAN, 0.0, 1.0), 0.0);
        assert_eq!(clamp(1.5, 0.0, 1.0), 1.0);
        assert_eq!(clamp(-0.5, 0.0, 1.0), 0.0);
        assert_eq!(clamp(0.42, 0.0, 1.0), 0.42);
    }

    #[test]
    fn min_max_edges() {
        assert!(min_max_normalize(&[]).is_empty());
        // Docstring Python nói single value → [1.0] nhưng CODE trả [0.0]
        // (trùng branch all-identical) — replicate code, không replicate docstring.
        assert_eq!(min_max_normalize(&[7.0]), vec![0.0]);
        assert_eq!(min_max_normalize(&[3.0, 3.0]), vec![0.0, 0.0]);
        // Docstring Python ghi [0.0, 0.4, 0.8, 1.0] — sai số học (span thật 1.1);
        // code Python + golden fixture xác nhận [0.0, 0.3636..., 0.7272..., 1.0].
        let normed = min_max_normalize(&[0.1, 0.5, 0.9, 1.2]);
        for (a, b) in normed.iter().zip([0.0, 0.4 / 1.1, 0.8 / 1.1, 1.0]) {
            assert!((a - b).abs() < 1e-12, "{a} vs {b}");
        }
    }

    #[test]
    fn round6_is_python_half_even() {
        assert_eq!(round6(0.1234564), 0.123456);
        assert_eq!(round6(0.1234565), 0.123456, "half → even (Python round)");
        assert_eq!(round6(0.1234566), 0.123457);
    }

    #[test]
    fn freshness_decay() {
        assert_eq!(freshness_from_elapsed(0.0, 30.0), 1.0);
        assert_eq!(freshness_from_elapsed(-5.0, 30.0), 1.0);
        let day = 86_400.0;
        // 30 ngày với half-life 30 ngày → 0.5
        assert!((freshness_from_elapsed(30.0 * day, 30.0) - 0.5).abs() < 1e-12);
    }

    #[test]
    fn dirty_and_invalid_iso() {
        assert_eq!(freshness_from_dirty_at(true, "", 0.0), 1.0);
        assert_eq!(freshness_from_dirty_at(false, "", 0.0), 0.3);
        assert_eq!(freshness_from_dirty_at(false, "not-a-date", 0.0), 0.3);
        // Naive ISO — Python crash TypeError, Rust trả 0.3 (documented divergence)
        assert_eq!(freshness_from_dirty_at(false, "2026-01-01T00:00:00", 0.0), 0.3);
    }

    #[test]
    fn iso_with_offset_parses() {
        // 2026-01-01T00:00:00+00:00 → epoch 1767225600
        let ts = parse_iso8601_utc("2026-01-01T00:00:00+00:00").unwrap();
        assert!((ts - 1_767_225_600.0).abs() < 1e-6);
        let ts_z = parse_iso8601_utc("2026-01-01T00:00:00Z").unwrap();
        assert!((ts_z - ts).abs() < 1e-12);
        // +07:00 offset trừ đi
        let ts_vn = parse_iso8601_utc("2026-01-01T07:00:00+07:00").unwrap();
        assert!((ts_vn - 1_767_225_600.0).abs() < 1e-6);
    }
}
