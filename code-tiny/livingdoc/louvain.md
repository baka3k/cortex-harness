Đây là quy trình chi tiết để triển khai Thuật toán Louvain nhằm gom nhóm các Function thành các InfraNode (Modular Concept).

Để làm việc này, bạn cần cài đặt plugin Graph Data Science (GDS) trên Neo4j.

Dưới đây là 4 bước thực hiện:

## Bước 0: Chuẩn bị dữ liệu & Cấu hình GDS
Giả sử trong Neo4j bạn đã có:
- Node: Function (được tạo từ Tree-sitter)
- Relationship: (:Function)-[:CALLS]->(:Function)

## Bước 1: Tạo Graph Projection (In-Memory)
Louvain chạy trên đồ thị ảo trong bộ nhớ (in-memory) chứ không chạy trực tiếp trên ổ đĩa để tối ưu tốc độ.

Chạy câu lệnh Cypher sau trong Neo4j Browser:

```bash
CALL gds.graph.project(
  'functionGraph',       // Tên graph ảo
  'Function',            // Label của node cần gom nhóm
  {
    CALLS: {
      type: 'CALLS',
      orientation: 'UNDIRECTED' // Quan trọng: Coi call graph là vô hướng để gom cụm tốt hơn
    }
  }
)
```
Tại sao là UNDIRECTED? Trong phân tích module phần mềm, việc A gọi B hay B gọi A đều cho thấy chúng có mối liên kết chặt chẽ (Coupling). Gom nhóm vô hướng giúp phát hiện các cụm chức năng (Cluster) tự nhiên hơn.

## Bước 2: Chạy Louvain và Ghi (Write) kết quả
Ta sẽ chạy thuật toán và ghi đè communityId vào từng node Function.

```bash
CALL gds.louvain.write(
  'functionGraph',
  {
    writeProperty: 'communityId' // Tên property sẽ lưu kết quả trên node Function
  }
)
YIELD communityCount, modularity, ranLevels
```
Sau bước này, mỗi node Function sẽ có thêm property communityId (ví dụ: 123, 456...). Những node có cùng ID thuộc về một nhóm chức năng.

## Bước 3: Materialize - Sinh ra InfraNode từ Community ID
Bây giờ ta sẽ biến các con số communityId vô tri thành các InfraNode thực sự trong đồ thị.
Chạy câu lệnh Cypher sau (Data Refactoring):

```bash
// 1. Tìm tất cả các Function có communityId
MATCH (f:Function) WHERE f.communityId IS NOT NULL
WITH f.communityId AS cid, collect(f) AS functions

// 2. Chỉ tạo InfraNode cho các cụm có kích thước đủ lớn (ví dụ > 3 function) để tránh rác
WHERE size(functions) > 3

// 3. Tạo (hoặc tìm) InfraNode đại diện cho cụm này
MERGE (infra:InfraNode {id: toString(cid)})
SET infra.status = 'pending_summary' // Đánh dấu để lát nữa LLM xử lý

// 4. Link các Function vào InfraNode
FOREACH (func IN functions |
  MERGE (func)-[:BELONGS_TO]->(infra)
)
```
Kết quả: Bạn đã có một lớp node mới nằm trên các Function. InfraNode (123) -> chứa 10 Function liên quan đến "Xử lý thanh toán".

## Tool: living-doc-louvain.py (Tự động hóa Bước 1-3)
Nếu muốn chạy toàn bộ Bước 1-3 bằng một lệnh duy nhất, dùng tool sau:

```bash
python living-doc-louvain.py
```

### Env vars/flags chính
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` (bắt buộc)
- `PROJECT_ID` (lọc Function theo project_id, dùng `CONTAINS`)
- `GDS_GRAPH_NAME` (default `functionGraph`)
- `NODE_LABEL` (default `Function`)
- `REL_TYPE` (default `CALLS`)
- `ORIENTATION` (default `UNDIRECTED`)
- `WRITE_PROPERTY` (default `communityId`)
- `MIN_COMMUNITY_SIZE` (default `4`)
- `INFRA_LABEL` (default `InfraNode`)
- `INFRA_ID_FIELD` (default `id`)
- `INFRA_STATUS` (default `pending_summary`)
- `BELONGS_REL` (default `BELONGS_TO`)
- `DROP_GRAPH=1` để drop graph in-memory nếu đã tồn tại
- `DROP_AFTER=1` để drop graph in-memory sau khi chạy xong

Ví dụ:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_password
export PROJECT_ID=my_project
export DROP_GRAPH=1
python living-doc-louvain.py
```

## Bước 4: Semantic Naming (Làm giàu ngữ nghĩa cho InfraNode)
Lúc này InfraNode chỉ có ID là số. Ta cần LLM đọc code của các function con để đặt tên cho nó (Labeling).

Workflow Python (Pseudo-code):
```python
# 1. Lấy danh sách InfraNode chưa có tên
query = "MATCH (i:InfraNode) WHERE i.status = 'pending_summary' RETURN i.id"
infra_nodes = neo4j_driver.execute(query)

for node in infra_nodes:
    # 2. Lấy summary/tên của tất cả function con thuộc InfraNode này
    sub_funcs_query = """
        MATCH (f:Function)-[:BELONGS_TO]->(i:InfraNode {id: $id}) 
        RETURN f.name, f.summary
    """
    context = neo4j_driver.execute(sub_funcs_query)
    
    # 3. Gửi cho LLM để đặt tên Concept
    prompt = f"""
    Dưới đây là danh sách các hàm trong một module phần mềm:
    {context}
    
    Hãy đặt một cái tên kỹ thuật chính xác cho module này (ví dụ: 'UserAuthModule', 'PaymentGatewayAdapter') 
    và viết một mô tả ngắn gọn về trách nhiệm của nó.
    Output JSON: {{ "name": "...", "description": "..." }}
    """
    
    result = call_llm(prompt)
    
    # 4. Update lại InfraNode trên Neo4j
    update_query = """
        MATCH (i:InfraNode {id: $id})
        SET i.name = $name, 
            i.description = $desc,
            i.status = 'ready'
    """
```

# NEXT STEP: Advanced
Hierarchical Louvain (Phân cấp): Code thường có cấu trúc lồng nhau (Function -> Class -> Module -> Package). Thuật toán Louvain mặc định hỗ trợ phân cấp (intermediateCommunities). Bạn có thể lưu lại các level khác nhau để tạo ra cây InfraNode: InfraNode_L1 (To) -> InfraNode_L2 (Nhỏ) -> Function.

Lọc nhiễu (Noise Filtering): Sẽ có những cụm "rác" (ví dụ cụm các hàm utils như toString, formatDate được gọi khắp nơi).

Giải pháp: Dùng chỉ số Degree Centrality hoặc PageRank trước. Nếu một cụm chứa quá nhiều node có PageRank thấp nhưng kết nối lung tung, có thể đánh dấu là Utility Cluster và bỏ qua việc tạo document link phức tạp.

Bước tiếp theo
Sau khi có InfraNode với tên gọi xịn xò (ví dụ: PaymentProcessing), bạn sẽ dùng chính tên và mô tả này để embedding và search trong HyperMind (Phase 2), khả năng khớp với document nghiệp vụ sẽ cao hơn gấp nhiều lần so với việc search từng tên hàm process_txn.
