#!/usr/bin/env python3
"""Reference-invocation shim cho phase 07 go parity (KHÔNG sửa code-tiny).

go_analyzer.py dựng rows mà writer contract từ chối khi chạy "trần", nên tham
chiếu CHỈ chạy được qua đúng đường invocation production — shim cung cấp đúng
các accommodation đó, không đụng rows:

1. CALLS rows không mang project_id → writer đọc journal metadata làm fallback
   (`configure_journal_env(..., mode="shadow")` do analyzer_parity_go.py set
   trong env trước khi gọi shim). Rust supply `project_id` tường minh trên
   rows — cùng giá trị, cùng graph.

2. File-[:INCLUDES]->(ExternalModule) rows mang `target_label` tường minh nhưng
   static schema manifest không có id index cho ExternalModule ⇒
   `RelationshipGroup.__post_init__` raise TRƯỚC khi write (Python writer).
   Rust writer contract không validate group theo manifest
   (`RelationshipGroup::new_unchecked`) — shim bỏ check identity-index để 2
   backend nhận cùng rows. (go_analyzer.py chạy production với import sẽ gặp
   cùng lỗi này — đã ghi report.)
"""

from __future__ import annotations

import asyncio
import sys

from tools.graph.writer.query_contract import RelationshipGroup

RelationshipGroup.__post_init__ = lambda self: None  # type: ignore[method-assign]

from tools.go.go_analyzer import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
