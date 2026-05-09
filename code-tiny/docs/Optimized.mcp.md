Android MCP – điểm mạnh

- API linh hoạt nhờ payload + \_merge_payload +
  \_coerce_payload cho hầu hết tools, giúp gọi từ client ổn
  định hơn. mcp/android/android_mcp.py
- \_paths_to_graph xử lý được nhiều dạng path (list, dict, rel
  rời), nên ít lỗi khi Neo4j trả về kiểu khác nhau. mcp/
  android/android_mcp.py
- Hệ trace_flow / trace_flow_between_module với rel_types
  giúp truy vết đa quan hệ (UI, intent, event…). mcp/android/
  android_mcp.py
- Search predicate phong phú cho Android domain (manifest,
  component, route, resource...). mcp/android/android_mcp.py
- Có debug trong find_path_between_module, dễ chẩn đoán DB/
  data. mcp/android/android_mcp.py

Android MCP – điểm yếu

- Thiếu get_ipc_message và list_possible_calls như bên C++.
  mcp/android/android_mcp.py
- query_subgraph/find_paths chỉ dùng CALLS, không có
  POSSIBLE_CALLS/CALLS_FUNCTION_POINTER. mcp/android/
  android_mcp.py
- Một số truy vấn file path chưa lower-case, có thể bỏ sót
  khi case khác nhau (ví dụ listup_function_mathching_file).
  mcp/android/android_mcp.py
- INSTRUCTIONS không liệt kê annotate_node dù tool có tồn
  tại. mcp/android/android_mcp.py

C++ MCP – điểm mạnh

- Có get_ipc_message đọc dữ liệu IPC từ temp/
  ipc_messages.json. mcp/cplus/cplus_mcp.py
- Hỗ trợ virtual dispatch và function pointer thông qua
  list_possible_calls, include_possible, include_fp. mcp/
  cplus/cplus_mcp.py
- List parser đa dạng ["cplus","java","kotlin"]. mcp/cplus/
  cplus_mcp.py
- Nhiều truy vấn file path đã lower-case để tránh miss. mcp/
  cplus/cplus_mcp.py

C++ MCP – điểm yếu

- Thiếu cơ chế payload nên client khó “override” params đồng
  nhất giữa tools. mcp/cplus/cplus_mcp.py
- Input validation mỏng hơn (nhiều tool không check list
  rỗng/None). mcp/cplus/cplus_mcp.py
- \_paths_to_graph chỉ xử lý path.nodes chuẩn, dễ lỗi nếu
  Neo4j trả dạng list/dict. mcp/cplus/cplus_mcp.py
- Thiếu trace_flow/trace_flow_between_module và thiếu
  rel_types normalization. mcp/cplus/cplus_mcp.py
- INSTRUCTIONS cũng không liệt kê annotate_node. mcp/cplus/
  cplus_mcp.py

Đề xuất bổ sung để “học” điểm mạnh của nhau

1. Thêm payload + \_merge_payload + \_coerce_payload cho C++
   MCP để đồng nhất cách gọi tool. mcp/cplus/cplus_mcp.py
2. Nâng \_paths_to_graph của C++ theo logic Android để xử lý
   list/dict path linh hoạt. mcp/cplus/cplus_mcp.py
3. Thêm trace_flow/trace_flow_between_module cho C++ (dùng
   rel_types tùy chọn), có thể reuse \_normalize_rel_types +
   \_build_rel_match từ Android. mcp/cplus/cplus_mcp.py
4. Bổ sung get_ipc_message vào Android MCP nếu IPC là năng
   lực chung. mcp/android/android_mcp.py
5. Bổ sung list_possible_calls + include_possible/include_fp
   cho Android MCP (có thể no-op nếu graph không có edge).
   mcp/android/android_mcp.py
6. Chuẩn hóa file-path matching theo lower-case cho Android ở
   các query có file_path CONTAINS. mcp/android/
   android_mcp.py
7. Đồng bộ INSTRUCTIONS ở cả hai để liệt kê đầy đủ tools đang
   tồn tại (đặc biệt annotate_node). mcp/android/
   android_mcp.py, mcp/cplus/cplus_mcp.py
8. Nếu muốn giảm trùng lặp, tách common utilities ra module
   chung (ví dụ mcp/common/mcp_utils.py) và import từ cả hai.

Nếu bạn muốn, mình có thể triển khai các thay đổi trên thành
patch cụ thể. Bạn muốn mình:

1. Chỉ đưa patch cho C++ để match Android (payload + paths +
   trace_flow)?
2. Chỉ đưa patch cho Android để match C++ (IPC + possible
   calls + case-insensitive file match)?
3. Làm cả hai theo hướng đồng bộ hoá toàn diện?
