# DeerFlow system architecture

DeerFlow là deep-research pipeline gồm 4 tầng: planner, researcher,
coder và reporter. Tầng researcher dùng DeerFlow retrieval kết hợp
FalkorDB graph context và Qdrant vector context để tổng hợp câu trả
lời có trích dẫn.

Planner của DeerFlow chạy trên LLM GPT-4o với prompt template quản
lý bằng LangExtract. Coder thực thi sandbox Python trên Docker,
ghi kết quả vào MinIO trước khi reporter tổng hợp báo cáo cuối.

Toàn bộ DeerFlow chạy trên AWS ap-southeast-1, EC2 c6i.2xlarge,
với Grafana giám sát và S3 lưu artifact. Deploy dùng Docker
Compose trong dev và EKS cho production.
