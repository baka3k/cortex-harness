# Phase 03 benchmark — write 1k nodes (functions_full)

- rows: 1000, runs: 3, batch_size: 1000, backend: FalkorDB 127.0.0.1:6379
- Python (LanguageCodeWriter + FalkorDBDriver): runs=[0.094s, 0.039s, 0.037s] median=0.039s
- Rust (LanguageCodeWriter + FalkorDbStore): including process spawn + connect:
  runs=[0.701s, 0.051s, 0.046s] median=0.051s
- ratio (py/rust, incl. spawn): 0.76x

Ghi chú: đo wall-clock toàn bộ write path; Rust timing gồm process spawn + connect (khoảng ~50-100ms) nên bất lợi nhẹ cho Rust. Không đặt gate tuyệt đối theo phase-03.md.
