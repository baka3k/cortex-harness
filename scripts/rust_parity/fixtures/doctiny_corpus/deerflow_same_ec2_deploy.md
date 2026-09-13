# DeerFlow Deployment On Same Stock EC2

Mục tiêu: triển khai DeerFlow trên **chính EC2 hiện tại** của stock, không tách máy, không đổi runtime production hiện có của stock.

## 1. Layout thư mục chuẩn trên EC2

```bash
/home/ec2-user/stock/                 # stock app hiện tại
/home/ec2-user/stock/deer-flow/       # DeerFlow clone vào đây
```

## 2. Kiến trúc runtime

- Stock UI giữ nguyên:
  - Streamlit: `127.0.0.1:8501`
  - Nginx hệ thống: public `:80/:443`
- DeerFlow chạy Docker Compose trong `/home/ec2-user/stock/deer-flow`:
  - bind local-only: `127.0.0.1:2026`
- `stock-mcp` chạy host service:
  - bind local-only: `127.0.0.1:8789`
- Nginx route theo subdomain:
  - `stock.<domain>` -> `127.0.0.1:8501`
  - `deerflow.<domain>` -> `127.0.0.1:2026`

## 3. Deploy DeerFlow vào cùng EC2

```bash
cd /home/ec2-user/stock
chmod +x ./scripts/ec2/*.sh

./scripts/ec2/deploy_deerflow_on_stock_ec2.sh \
  --deerflow-repo-url <deer-flow-repo-url> \
  --deerflow-branch main \
  --stock-dir /home/ec2-user/stock \
  --deerflow-dir /home/ec2-user/stock/deer-flow \
  --bind-host 127.0.0.1 \
  --bind-port 2026
```

Script sẽ:
- clone/pull DeerFlow vào `deer-flow/`
- seed `config.yaml`, `extensions_config.json`, `.env` (nếu chưa có)
- chạy `./scripts/deploy.sh up` với `PORT=127.0.0.1:2026`

## 4. Cấu hình Nginx cho deerflow.<domain>

```bash
sudo ./scripts/ec2/install_deerflow_nginx_vhost.sh \
  --domain deerflow.<domain> \
  --upstream-host 127.0.0.1 \
  --upstream-port 2026
```

Nếu dùng TLS cert có sẵn:

```bash
sudo ./scripts/ec2/install_deerflow_nginx_vhost.sh \
  --domain deerflow.<domain> \
  --upstream-host 127.0.0.1 \
  --upstream-port 2026 \
  --ssl-cert /etc/letsencrypt/live/deerflow.<domain>/fullchain.pem \
  --ssl-key /etc/letsencrypt/live/deerflow.<domain>/privkey.pem
```

## 5. Cài `stock-mcp` service trên host

```bash
sudo ./scripts/ec2/install_stock_mcp_service.sh \
  --app-dir /home/ec2-user/stock \
  --user ec2-user \
  --host 127.0.0.1 \
  --port 8789 \
  --path /mcp
```

Sau khi cài:
- unit: `stock-mcp.service`
- env file: `/etc/stock-mcp.env`

## 6. Nối DeerFlow -> stock_mcp (MCP HTTP)

```bash
./scripts/ec2/configure_deerflow_stock_mcp.py \
  --deerflow-dir /home/ec2-user/stock/deer-flow \
  --server-name stock_mcp \
  --url http://host.docker.internal:8789/mcp \
  --restart
```

Script sẽ update `extensions_config.json` để bật server MCP `stock_mcp`.

## 7. Tạo custom agent `stock-suggest`

```bash
./scripts/ec2/bootstrap_stock_suggest_agent.py \
  --base-url http://127.0.0.1:2026 \
  --agent-name stock-suggest
```

Agent soul mặc định đã cấu hình:
- format trả lời `Action / Confidence / Why / Risks / Next step`
- ưu tiên gọi stock MCP tools trước web search cho gợi ý mã

## 8. Cài daily alert 16:30 ICT (SQLite watchlist + Telegram)

```bash
sudo ./scripts/ec2/install_stock_alert_timer.sh \
  --app-dir /home/ec2-user/stock \
  --user ec2-user \
  --service-name stock-alert \
  --watchlist main \
  --benchmark VNIndex \
  --calendar "Mon..Fri 16:30"
```

Điền Telegram secret:

```bash
sudo vi /etc/stock-alert.env
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...
```

Quản lý watchlist SQLite:

```bash
python -m stock_bot.manage_watchlist set --name main --symbols FPT,VNM,VCB
python -m stock_bot.manage_watchlist list
```

Run test thủ công:

```bash
sudo systemctl start stock-alert.service
journalctl -u stock-alert.service -n 100 --no-pager
```

## 9. Smoke test / nghiệm thu

```bash
./scripts/ec2/smoke_test_deerflow_stock.sh \
  --de