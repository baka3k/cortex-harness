# FalkorDB operations runbook

FalkorDB là graph database chạy trên Redis, hỗ trợ truy vấn Cypher với
extension GRAPH.QUERY. Cụm production của DeerFlow sử dụng FalkorDB 4.0
trên EC2 với persistent storage qua AOF everysec.

Khi FalkorDB hết bộ nhớ, thao tác GRAPH.RO_QUERY sẽ trả lỗi OOM và
cần restart service bằng systemd. Log của FalkorDB ghi tại
/var/log/falkordb/redis.log và được ship sang Grafana qua Loki.

Backup của FalkorDB chạy hằng tuần bằng GRAPH.DUMP sang S3, trong khi
Qdrant backup vector collection cùng lúc để đảm bảo tính nhất quán
giữa graph và vector index của DeerFlow.
