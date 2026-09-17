# Phase 02 — doctor remote hiển thị Browser UI URL

## Mục tiêu
`dev doctor` với project `storage_backend: remote` và falkordb reachable →
dòng check có thêm `Browser UI: http://<host>:<port>` để user mở luôn.

## Changes
File: `scripts/mcp-lifecycle.py`

1. Helper:
   ```python
   def _falkordb_ui_url(uri: str) -> str | None:
       """Derive the Browser UI URL from a falkordb URI; None when undervable."""
       candidate = uri.strip()
       if "://" in candidate:
           scheme, _, rest = candidate.partition("://")
           if scheme not in {"falkor", "falkors", "redis", "rediss"}:
               return None  # unix:// hoặc scheme lạ
           candidate = rest
       host, sep, _port = candidate.rpartition(":")
       if not sep or not host:
           host = candidate or "localhost"
       return f"http://{host}:{falkordb_ui_port()}"
   ```
   (Mirror logic parse của `falkordb_driver.py:335-350`.)
2. Trong `doctor_remote_checks`, với result `backend == "falkordb"`, reachable
   và không skipped:
   ```python
   message = f"{result.url} — {result.message}"
   ui_url = _falkordb_ui_url(remote_config.falkordb_uri or "")
   if ui_url:
       message += f" — Browser UI: {ui_url}"
   doctor_check(..., message)
   ```

## Notes
- Không tạo check riêng (Decision 02 trong plan.md) — UI URL là hint, remote
  host có thể không expose 3000.
- Tôn trọng `FALKORDB_UI_PORT` qua `falkordb_ui_port()` từ Phase 01.
- `unix://` → `None` → không append.

## Tests (`tests/test_doctor_remote.py`)
- `test_reachable_falkordb_shows_browser_ui_url`: probe reachable với
  `falkordb_uri="localhost:6379"` → output chứa
  `Browser UI: http://localhost:3000`.
- `test_falkordb_uri_with_scheme_parsed`: `redis://db.internal:6379` →
  `Browser UI: http://db.internal:3000`.
- `test_ui_port_env_override`: monkeypatch `FALKORDB_UI_PORT=3001` → URL :3001.
- `test_unix_uri_has_no_ui_url`: `unix:///tmp/x.sock` → không có `Browser UI`.
