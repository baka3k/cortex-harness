# Security baseline

Mọi webhook của n8n phải xác thực JWT trước khi xử lý payload.
Khóa ký JWT xoay mỗi 30 ngày và lưu trong Vault của HashiCorp.
Audit log ghi tại /var/log/deerflow/audit.jsonl.

Chuẩn ISO 15118 và Digital Key 3.0 yêu cầu bảo vệ giao tiếp bằng
TLS 1.3; certificate của CCC root CA được pin trong firmware của
BMW, Audi và Genesis. Thiết bị không tuân thủ sẽ bị từ chối khi
thực hiện Plug&Charge.

Cảnh báo bảo mật gửi tới Grafana OnCall và kênh Slack #deerflow-sec
trong vòng 5 phút kể từ khi Qdrant hoặc FalkorDB phát hiện truy
vấn bất thường.
