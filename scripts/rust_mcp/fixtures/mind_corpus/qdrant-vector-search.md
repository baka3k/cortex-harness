# Qdrant vector search internals

Qdrant là vector database hỗ trợ HNSW index với khoảng cách cosine
và euclid. Mỗi point trong Qdrant gồm vector 1024 chiều (bge-m3) và
payload JSON chứa source_id, paragraph_id và entity_mentions.

Collection trong Qdrant được shard theo project: collection mindfix_doc
chứa tài liệu của project này. Khi truy vấn semantic_search, hệ thống
embed câu hỏi bằng bge-m3 rồi search top-k trong collection tương ứng.

Để tối ưu Qdrant, DeerFlow bật on-disk payload index cho trường
source_id và quantization scalar int8, giúp giảm RAM 4 lần mà mất
chưa tới 1% độ chính xác recall@10.
