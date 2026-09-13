# Stock REST API for n8n

REST API này dùng lại logic của `analyze_stock.py` qua `stock_mcp.analysis_runner`, để n8n gọi trực tiếp bằng **HTTP Request** node.

## 1) Chạy local

```bash
# Cài dependencies
python -m pip install -r requirements_stock_mcp.txt

# Chạy API server (default: 127.0.0.1:8790)
python -m stock_mcp.http_api
```

Hoặc override host/port:

```bash
STOCK_API_HOST=0.0.0.0 STOCK_API_PORT=8790 python -m stock_mcp.http_api
```

## 2) Optional auth token

Nếu đặt `STOCK_API_TOKEN`, mọi request phải kèm 1 trong 2 cách:

- Header `Authorization: Bearer <token>`
- Header `X-API-Key: <token>`

## 3) Endpoints chính cho n8n

- `GET /health`
- `POST /api/v1/analyze`
- `GET /api/v1/decision/{symbol}`
- `GET /api/v1/news/{symbol}` (mới, tin tốt/xấu ngắn hạn)
- `GET /api/v1/latest/{symbol}`
- `GET /api/v1/watchlists`
- `GET /api/v1/watchlists/{name}`
- `PUT /api/v1/watchlists/{name}`
- `POST /api/v1/watchlists/{name}/analyze`

## 4) Request/response mẫu

### 4.1 Lấy decision nhanh cho chat bot

```bash
curl -s "http://127.0.0.1:8790/api/v1/decision/FPT?refresh=true&benchmark=VNIndex&include_news=true&news_max_items=3"
```

Response (rút gọn):

```json
{
  "symbol": "FPT",
  "action": "BUY",
  "confidence": "0.73",
  "ai_weighted_recommendation": "BUY",
  "ai_ml_recommendation": "HOLD",
  "ai_llm_action": "BUY",
  "ai_llm_confidence": "0.73",
  "risk": "signal=BUY, close=122500",
  "news_short_term": {
    "ok": true,
    "impact_bias": "MIXED",
    "positive_news": ["..."],
    "negative_news": ["..."],
    "sentiment_summary": "..."
  },
  "as_of": "2026-04-06T10:20:00+07:00"
}
```

### 4.1.1 Chỉ lấy tin ngắn hạn (good/bad news)

```bash
curl -s "http://127.0.0.1:8790/api/v1/news/FPT?max_items=3&cache_hours=6&include_sources=false"
```

### 4.2 Phân tích đầy đủ một mã

```bash
curl -s -X POST "http://127.0.0.1:8790/api/v1/analyze" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "FPT",
    "benchmark": "VNIndex",
    "provider": "minimax",
    "llm_model": "gemini-2.5-flash",
    "include_log_tail": true
  }'
```

### 4.3 Cập nhật watchlist

```bash
curl -s -X PUT "http://127.0.0.1:8790/api/v1/watchlists/main" \
  -H "Content-Type: application/json" \
  -d '{"symbols": ["FPT", "VHM", "VNM"], "enabled": true}'
```

### 4.4 Scan watchlist

```bash
curl -s -X POST "http://127.0.0.1:8790/api/v1/watchlists/main/analyze" \
  -H "Content-Type: application/json" \
  -d '{"benchmark": "VNIndex", "max_symbols": 10}'
```

## 5) Deploy service trên EC2

Script mới:

```bash
./scripts/ec2/install_stock_api_service.sh --app-dir /home/ec2-user/stock --port 8790
```

Script này tạo service systemd `stock-api` với `ExecStart=<venv>/python -m stock_mcp.http_api`.

## 6) MCP tools tương ứng (cho n8n Agent / DeerFlow Agent)

Trong `stock_mcp.server` đã map thêm các tool để agent gọi trực tiếp:

- `health_check`
- `analyze_symbol`
- `get_symbol_decision` (mới)
- `get_symbol_news` (mới, expose `fetch_news_for_ticker`)
- `analyze_watchlist`
- `get_latest_report`
- `get_watchlist_config`
- `set_watchlist_config`
- `list_watchlist_configs`

Chạy MCP server:

```bash
python -m stock_mcp.server --host 127.0.0.1 --port 8789 --path /mcp
```
