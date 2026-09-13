# Phase 08 — WAVE D batch 4: framework overlays + database analyzers

## Scope — overlays = detector-gated (chạy SAU analyzer gốc, detector quyết định)

| Overlay | LOC | Detector gate |
|---|---|---|
| `tools/servlet_jsp/` | 7.3k | web.xml/JSP scan — Servlet, Filter, JspTag, ErrorPage... |
| `tools/mybatis/` | 4.7k | MyBatis XML mapper scan — Mapper/ResultMap/SqlStatement/Cache... |
| `tools/spring/` | 3.2k | SpringBean/Configuration/Component scan |
| `tools/struts/` | 1.6k | Struts config |
| `tools/aspnet_framework/` + `aspnet_core/` | 1.4k | ASP.NET (Framework + Core) |
| `tools/web_framework/` | 0.4k | generic web detection |
| `tools/sql/` + `plsql/` + `database_schema/` | ~5.2k | DatabaseTable/Column/Procedure (schema-first) |
| `fastapi_django` + `express_js` + `laravel` | trong tools tương ứng | detector per framework |

Đặc thù: overlays **không tự parse source mới** — đọc nodes do analyzer gốc ghi (Java/Python/
JS...) + config/XML, rồi thêm edges/labels (ví dụ mybatis đọc MyBatisXmlDocument + map vào
Mapper methods). Vì thế phụ thuộc: batch gốc tương ứng đã port (servlet_jsp/mybatis cần java;
express_js cần js; fastapi_django cần python; laravel cần php).

## Thứ tự

sql/plsql/database_schema (độc lập) → spring + fastapi_django + express_js + laravel
(detector đơn giản) → mybatis → struts + aspnet×2 → servlet_jsp (7.3k, để cuối).

## Parity

- Dual-run phụ thuộc: chạy analyzer gốc (Python hoặc Rust đã port) trước, rồi overlay
  Python vs Rust trên **cùng graph đầu vào** → diff rỗng.
- procsample là testdata vàng: Pro*C + servlet/mybatis thật.
- stock: fastapi_django + express_js detector thật (FastAPI backend thấy rõ trong graph).

## Gate

- [ ] Detector: cùng input → cùng quyết định gate (bật/tắt overlay) trên stock + procsample
      + testdata (bảng quyết định so exact).
- [ ] 10/10 overlay parity pass (diff rỗng ngoài mask).
- [ ] **Wave D đóng**: `dev sync code all` (orchestrator Python) chạy với TOÀN BỘ analyzers
      Rust qua `CORTEX_RUST_ANALYZER=rust` trên stock — summary khớp Python run.
