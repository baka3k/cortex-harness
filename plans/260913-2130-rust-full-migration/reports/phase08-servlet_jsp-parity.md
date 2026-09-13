# Phase 08 — Servlet/JSP overlay parity (python vs rust)

- chạy: 2026-09-14 06:01:03
- testdata: `tests/fixtures/java-spring-overlays`
- rust bin: `/tmp/p08_ws/target/release/analyzer-servlet-jsp`
- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']` + `_start_id`/`_end_id`

### summary testdata_full

- py: `{"analyzer":"servlet_jsp","applied":141,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":141,"deleted":0,"diagnostics":1,"facts":63,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-060103/testdata_full_py/servlet_jsp_preview.json","relationships":78,"stage":"complete","truncation_count":0,"updated":0}`
- rs: `{"analyzer":"servlet_jsp","applied":143,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":143,"deleted":0,"diagnostics":4,"facts":66,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-060103/testdata_full_rs/servlet_jsp_preview.json","relationships":77,"stage":"complete","truncation_count":0,"updated":0}`

- testdata_full preview JSON artifact: py=159872B rs=164750B =FAIL=


### TESTDATA_FULL

- nodes: py=117 rust=120
- edges: py=158 rust=157
- diff_total: **285**

```json

{
  "nodes_only_py": {
    "ApiEndpoint|servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06",
      "symbol_id": "servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "extension",
      "path": "*.do",
      "raw_url_pattern": "*.do",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa",
      "symbol_id": "servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/AdminServlet.java",
      "start_line": 12,
      "end_line": 37,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "exact",
      "path": "/shop/admin",
      "raw_url_pattern": "/shop/admin",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::0906c51b5732a897c6b8::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::apiendpoint::0906c51b5732a897c6b8",
      "symbol_id": "servlet_jsp::apiendpoint::0906c51b5732a897c6b8",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "path-prefix",
      "path": "/legacy/*",
      "raw_url_pattern": "/legacy/*",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "Authority|servlet_jsp::authority::62eda10074ee4b16a728::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::authority::62eda10074ee4b16a728",
      "symbol_id": "servlet_jsp::authority::62eda10074ee4b16a728",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "Authority",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 40,
      "end_line": 42,
      "start_column": 3,
      "end_column": 19,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_xml",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "provenance": "web.xml",
      "role": "admin",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "Authority"
    },
    "ErrorPage|servlet_jsp::errorpage::5451e9ff1b97767361aa::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::errorpage::5451e9ff1b97767361aa",
      "symbol_id": "servlet_jsp::errorpage::5451e9ff1b97767361aa",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ErrorPage",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 36,
      "end_line": 39,
      "start_column": 3,
      "end_column": 16,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_xml",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "error_code": "404",
      "exception_type": "",
      "location": "/WEB-INF/jsp/error.jsp",
      "provenance": "web.xml",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ErrorPage"
    },
    "Filter|servlet_jsp::filter::9878f25252ef7de6cb4b::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::filter::9878f25252ef7de6cb4b",
      "symbol_id": "servlet_jsp::filter::9878f25252ef7de6cb4b",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "Filter",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/LegacyFilter.java",
      "start_line": 11,
      "end_line": 25,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.LegacyFilter",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "component_class": "com.example.shop.web.LegacyFilter",
      "component_name": "legacyFilter",
      "declaration_sources": [
        "web.xml"
      ],
      "dispatcher_types": [],
      "evidence": [
        "inherits:javax.servlet.Filter"
      ],
      "filter_class": "com.example.shop.web.LegacyFilter",
      "filter_name": "legacyFilter",
      "init_params": [],
      "jsp_file": "",
      "load_on_startup": "",
      "servlet_names": [],
      "url_patterns": [],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "Filter"
    },
    "FilterMapping|servlet_jsp::filtermapping::560ce98dc65649fd0ce1::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::filtermapping::560ce98dc65649fd0ce1",
      "symbol_id": "servlet_jsp::filtermapping::560ce98dc65649fd0ce1",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "FilterMapping",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 25,
      "end_line": 29,
      "start_column": 3,
      "end_column": 20,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "descriptor_order": 4,
      "dispatcher_types": [
        "REQUEST"
      ],
      "filter_name": "legacyFilter",
      "mapping_kind": "url-pattern",
      "order_status": "exact",
      "provenance": "web.xml",
      "servlet_names": [],
      "url_patterns": [
        "/legacy/*"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "FilterMapping"
    },
    "JSPView|servlet_jsp::artifact::63db68e2aa944dba1b1c::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::artifact::63db68e2aa944dba1b1c",
      "symbol_id": "servlet_jsp::artifact::63db68e2aa944dba1b1c",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JSPView",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/footer.jsp",
      "start_line": 1,
      "end_line": 1,
      "start_column": 1,
      "end_column": 1,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_jsp",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "artifact_kind": "jsp",
      "coverage_status": "complete",
      "module_path": ".",
      "truncated": false,
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JSPView"
    },
    "JSPView|servlet_jsp::artifact::364a3fe6d417ba282789::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::artifact::364a3fe6d417ba282789",
      "symbol_id": "servlet_jsp::artifact::364a3fe6d417ba282789",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JSPView",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 1,
      "end_line": 1,
      "start_column": 1,
      "end_column": 1,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_jsp",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "artifact_kind": "jsp",
      "coverage_status": "complete",
      "module_path": ".",
      "truncated": false,
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JSPView"
    },
    "JspExpression|servlet_jsp::jspexpression::ed9288f851c4ba93380d::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::ed9288f851c4ba93380d",
      "symbol_id": "servlet_jsp::jspexpression::ed9288f851c4ba93380d",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 9,
      "end_line": 9,
      "start_column": 19,
      "end_column": 45,
      "confidence": 1.0,
      "extraction_method": "jsp_el",
      "resolution_status": "resolved",
      "raw_value": "[REDACTED]",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "expression": "[REDACTED]",
      "functions": [],
      "property_paths": [
        "[REDACTED]"
      ],
      "variables": [
        "[REDACTED]"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JspExpression"
    },
    "JspExpression|servlet_jsp::jspexpression::d748708381cc56120c29::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::d748708381cc56120c29",
      "symbol_id": "servlet_jsp::jspexpression::d748708381cc56120c29",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 14,
      "end_line": 14,
      "start_column": 42,
      "end_column": 56,
      "confidence": 1.0,
      "extraction_method": "jsp_el",
      "resolution_status": "resolved",
      "raw_value": "${param.query}",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "expression": "${param.query}",
      "functions": [],
      "property_paths": [
        "param.query"
      ],
      "variables": [
        "param"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JspExpression"
    },
    "JspExpression|servlet_jsp::jspexpression::c240b63398a0af13bc2f::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::c240b63398a0af13bc2f",
      "symbol_id": "servlet_jsp::jspexpression::c240b63398a0af13bc2f",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 8,
      "end_line": 8,
      "start_column": 5,
      "end_column": 18,
      "confidence": 1.0,
      "extraction_method": "jsp_el",
      "resolution_status": "resolved",
      "raw_value": "${param.view}",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "expression": "${param.view}",
      "functions": [],
      "property_paths": [
        "param.view"
      ],
      "variables": [
        "param"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JspExpression"
    },
    "JspExpression|servlet_jsp::jspexpression::ad179efe0e20cdbe85c4::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::ad179efe0e20cdbe85c4",
      "symbol_id": "servlet_jsp::jspexpression::ad179efe0e20cdbe85c4",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      

```


### summary inc_seed

- py: `{"analyzer":"servlet_jsp","applied":141,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":141,"deleted":0,"diagnostics":1,"facts":63,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-060103/inc_seed_py/servlet_jsp_preview.json","relationships":78,"stage":"complete","truncation_count":0,"updated":0}`
- rs: `{"analyzer":"servlet_jsp","applied":143,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":143,"deleted":0,"diagnostics":4,"facts":66,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-060103/inc_seed_rs/servlet_jsp_preview.json","relationships":77,"stage":"complete","truncation_count":0,"updated":0}`

- inc_seed preview JSON artifact: py=159872B rs=164750B =FAIL=


### INC_SEED

- nodes: py=117 rust=120
- edges: py=158 rust=157
- diff_total: **285**

```json

{
  "nodes_only_py": {
    "ApiEndpoint|servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06",
      "symbol_id": "servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "extension",
      "path": "*.do",
      "raw_url_pattern": "*.do",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa",
      "symbol_id": "servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/AdminServlet.java",
      "start_line": 12,
      "end_line": 37,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "exact",
      "path": "/shop/admin",
      "raw_url_pattern": "/shop/admin",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::0906c51b5732a897c6b8::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::apiendpoint::0906c51b5732a897c6b8",
      "symbol_id": "servlet_jsp::apiendpoint::0906c51b5732a897c6b8",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "path-prefix",
      "path": "/legacy/*",
      "raw_url_pattern": "/legacy/*",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "Authority|servlet_jsp::authority::62eda10074ee4b16a728::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::authority::62eda10074ee4b16a728",
      "symbol_id": "servlet_jsp::authority::62eda10074ee4b16a728",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "Authority",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 40,
      "end_line": 42,
      "start_column": 3,
      "end_column": 19,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_xml",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "provenance": "web.xml",
      "role": "admin",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "Authority"
    },
    "ErrorPage|servlet_jsp::errorpage::5451e9ff1b97767361aa::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::errorpage::5451e9ff1b97767361aa",
      "symbol_id": "servlet_jsp::errorpage::5451e9ff1b97767361aa",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "ErrorPage",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 36,
      "end_line": 39,
      "start_column": 3,
      "end_column": 16,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_xml",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "error_code": "404",
      "exception_type": "",
      "location": "/WEB-INF/jsp/error.jsp",
      "provenance": "web.xml",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ErrorPage"
    },
    "Filter|servlet_jsp::filter::9878f25252ef7de6cb4b::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::filter::9878f25252ef7de6cb4b",
      "symbol_id": "servlet_jsp::filter::9878f25252ef7de6cb4b",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "Filter",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/LegacyFilter.java",
      "start_line": 11,
      "end_line": 25,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.LegacyFilter",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "component_class": "com.example.shop.web.LegacyFilter",
      "component_name": "legacyFilter",
      "declaration_sources": [
        "web.xml"
      ],
      "dispatcher_types": [],
      "evidence": [
        "inherits:javax.servlet.Filter"
      ],
      "filter_class": "com.example.shop.web.LegacyFilter",
      "filter_name": "legacyFilter",
      "init_params": [],
      "jsp_file": "",
      "load_on_startup": "",
      "servlet_names": [],
      "url_patterns": [],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "Filter"
    },
    "FilterMapping|servlet_jsp::filtermapping::560ce98dc65649fd0ce1::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::filtermapping::560ce98dc65649fd0ce1",
      "symbol_id": "servlet_jsp::filtermapping::560ce98dc65649fd0ce1",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "FilterMapping",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 25,
      "end_line": 29,
      "start_column": 3,
      "end_column": 20,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "descriptor_order": 4,
      "dispatcher_types": [
        "REQUEST"
      ],
      "filter_name": "legacyFilter",
      "mapping_kind": "url-pattern",
      "order_status": "exact",
      "provenance": "web.xml",
      "servlet_names": [],
      "url_patterns": [
        "/legacy/*"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "FilterMapping"
    },
    "JSPView|servlet_jsp::artifact::63db68e2aa944dba1b1c::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::artifact::63db68e2aa944dba1b1c",
      "symbol_id": "servlet_jsp::artifact::63db68e2aa944dba1b1c",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JSPView",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/footer.jsp",
      "start_line": 1,
      "end_line": 1,
      "start_column": 1,
      "end_column": 1,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_jsp",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "artifact_kind": "jsp",
      "coverage_status": "complete",
      "module_path": ".",
      "truncated": false,
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JSPView"
    },
    "JSPView|servlet_jsp::artifact::364a3fe6d417ba282789::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::artifact::364a3fe6d417ba282789",
      "symbol_id": "servlet_jsp::artifact::364a3fe6d417ba282789",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JSPView",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 1,
      "end_line": 1,
      "start_column": 1,
      "end_column": 1,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_jsp",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "artifact_kind": "jsp",
      "coverage_status": "complete",
      "module_path": ".",
      "truncated": false,
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JSPView"
    },
    "JspExpression|servlet_jsp::jspexpression::ed9288f851c4ba93380d::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::ed9288f851c4ba93380d",
      "symbol_id": "servlet_jsp::jspexpression::ed9288f851c4ba93380d",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 9,
      "end_line": 9,
      "start_column": 19,
      "end_column": 45,
      "confidence": 1.0,
      "extraction_method": "jsp_el",
      "resolution_status": "resolved",
      "raw_value": "[REDACTED]",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "expression": "[REDACTED]",
      "functions": [],
      "property_paths": [
        "[REDACTED]"
      ],
      "variables": [
        "[REDACTED]"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JspExpression"
    },
    "JspExpression|servlet_jsp::jspexpression::d748708381cc56120c29::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::d748708381cc56120c29",
      "symbol_id": "servlet_jsp::jspexpression::d748708381cc56120c29",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 14,
      "end_line": 14,
      "start_column": 42,
      "end_column": 56,
      "confidence": 1.0,
      "extraction_method": "jsp_el",
      "resolution_status": "resolved",
      "raw_value": "${param.query}",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "expression": "${param.query}",
      "functions": [],
      "property_paths": [
        "param.query"
      ],
      "variables": [
        "param"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JspExpression"
    },
    "JspExpression|servlet_jsp::jspexpression::c240b63398a0af13bc2f::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::c240b63398a0af13bc2f",
      "symbol_id": "servlet_jsp::jspexpression::c240b63398a0af13bc2f",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/catalog.jsp",
      "start_line": 8,
      "end_line": 8,
      "start_column": 5,
      "end_column": 18,
      "confidence": 1.0,
      "extraction_method": "jsp_el",
      "resolution_status": "resolved",
      "raw_value": "${param.view}",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "expression": "${param.view}",
      "functions": [],
      "property_paths": [
        "param.view"
      ],
      "variables": [
        "param"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JspExpression"
    },
    "JspExpression|servlet_jsp::jspexpression::ad179efe0e20cdbe85c4::generation::b2a64f2f6255180e7f806dee": {
      "semantic_id": "servlet_jsp::jspexpression::ad179efe0e20cdbe85c4",
      "symbol_id": "servlet_jsp::jspexpression::ad179efe0e20cdbe85c4",
      "generation_id": "b2a64f2f6255180e7f806dee",
      "kind": "JspExpression",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      

```


### summary inc_run

- py: `{"analyzer":"servlet_jsp","applied":160,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":160,"deleted":0,"diagnostics":2,"facts":69,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-060103/inc_run_py/servlet_jsp_preview.json","relationships":91,"stage":"complete","truncation_count":0,"updated":0}`
- rs: `{"analyzer":"servlet_jsp","applied":165,"artifacts":18,"baseline_advanced":true,"coverage_status":"complete","created":165,"deleted":0,"diagnostics":5,"facts":72,"modules":1,"preserved":0,"preview":"/Users/hieplq1.aip/AI/cortex-harness/.cache/p08_overlays/p08_sjsp-20260914-060103/inc_run_rs/servlet_jsp_preview.json","relationships":93,"stage":"complete","truncation_count":0,"updated":0}`

- inc_run preview JSON artifact: py=180613B rs=189335B =FAIL=


### INC_RUN

- nodes: py=124 rust=127
- edges: py=173 rust=175
- diff_total: **326**

```json

{
  "nodes_only_py": {
    "ApiEndpoint|servlet_jsp::apiendpoint::f909558bee3228a7a930::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::apiendpoint::f909558bee3228a7a930",
      "symbol_id": "servlet_jsp::apiendpoint::f909558bee3228a7a930",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doPost/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doPost"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doPost/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "POST",
      "mapping_kind": "path-prefix",
      "path": "/legacy/*",
      "raw_url_pattern": "/legacy/*",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06",
      "symbol_id": "servlet_jsp::apiendpoint::8460e4ef6d0519a7ec06",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "extension",
      "path": "*.do",
      "raw_url_pattern": "*.do",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::4edcf6d866ac6d612607::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::apiendpoint::4edcf6d866ac6d612607",
      "symbol_id": "servlet_jsp::apiendpoint::4edcf6d866ac6d612607",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/AdminServlet.java",
      "start_line": 12,
      "end_line": 43,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doPost/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doPost"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doPost/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "POST",
      "mapping_kind": "exact",
      "path": "/shop/admin",
      "raw_url_pattern": "/shop/admin",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa",
      "symbol_id": "servlet_jsp::apiendpoint::0c65a5d48328d5d10cfa",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/AdminServlet.java",
      "start_line": 12,
      "end_line": 43,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "exact",
      "path": "/shop/admin",
      "raw_url_pattern": "/shop/admin",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::0906c51b5732a897c6b8::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::apiendpoint::0906c51b5732a897c6b8",
      "symbol_id": "servlet_jsp::apiendpoint::0906c51b5732a897c6b8",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doGet"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doGet/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "GET",
      "mapping_kind": "path-prefix",
      "path": "/legacy/*",
      "raw_url_pattern": "/legacy/*",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "ApiEndpoint|servlet_jsp::apiendpoint::05088a1213e1f53bc532::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::apiendpoint::05088a1213e1f53bc532",
      "symbol_id": "servlet_jsp::apiendpoint::05088a1213e1f53bc532",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ApiEndpoint",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 16,
      "end_line": 20,
      "start_column": 3,
      "end_column": 21,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.AdminServlet.doPost/2@src/main/java/com/example/shop/web/AdminServlet.java",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "controller_class": "com.example.shop.web.AdminServlet",
      "declaration_sources": [
        "annotation",
        "web.xml"
      ],
      "handler_names": [
        "doPost"
      ],
      "handler_symbol_ids": [
        "com.example.shop.web.AdminServlet.doPost/2@src/main/java/com/example/shop/web/AdminServlet.java"
      ],
      "http_method": "POST",
      "mapping_kind": "extension",
      "path": "*.do",
      "raw_url_pattern": "*.do",
      "servlet_class": "com.example.shop.web.AdminServlet",
      "servlet_name": "legacy",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ApiEndpoint"
    },
    "Authority|servlet_jsp::authority::62eda10074ee4b16a728::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::authority::62eda10074ee4b16a728",
      "symbol_id": "servlet_jsp::authority::62eda10074ee4b16a728",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "Authority",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 40,
      "end_line": 42,
      "start_column": 3,
      "end_column": 19,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_xml",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "provenance": "web.xml",
      "role": "admin",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "Authority"
    },
    "ErrorPage|servlet_jsp::errorpage::5451e9ff1b97767361aa::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::errorpage::5451e9ff1b97767361aa",
      "symbol_id": "servlet_jsp::errorpage::5451e9ff1b97767361aa",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "ErrorPage",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 36,
      "end_line": 39,
      "start_column": 3,
      "end_column": 16,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_xml",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "error_code": "404",
      "exception_type": "",
      "location": "/WEB-INF/jsp/error.jsp",
      "provenance": "web.xml",
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "ErrorPage"
    },
    "Filter|servlet_jsp::filter::9878f25252ef7de6cb4b::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::filter::9878f25252ef7de6cb4b",
      "symbol_id": "servlet_jsp::filter::9878f25252ef7de6cb4b",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "Filter",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/java/com/example/shop/web/LegacyFilter.java",
      "start_line": 11,
      "end_line": 25,
      "start_column": 1,
      "end_column": 2,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "com.example.shop.web.LegacyFilter",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "component_class": "com.example.shop.web.LegacyFilter",
      "component_name": "legacyFilter",
      "declaration_sources": [
        "web.xml"
      ],
      "dispatcher_types": [],
      "evidence": [
        "inherits:javax.servlet.Filter"
      ],
      "filter_class": "com.example.shop.web.LegacyFilter",
      "filter_name": "legacyFilter",
      "init_params": [],
      "jsp_file": "",
      "load_on_startup": "",
      "servlet_names": [],
      "url_patterns": [],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "Filter"
    },
    "FilterMapping|servlet_jsp::filtermapping::560ce98dc65649fd0ce1::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::filtermapping::560ce98dc65649fd0ce1",
      "symbol_id": "servlet_jsp::filtermapping::560ce98dc65649fd0ce1",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "FilterMapping",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/web.xml",
      "start_line": 25,
      "end_line": 29,
      "start_column": 3,
      "end_column": 20,
      "confidence": 1.0,
      "extraction_method": "servlet_jsp_resolver",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "descriptor_order": 4,
      "dispatcher_types": [
        "REQUEST"
      ],
      "filter_name": "legacyFilter",
      "mapping_kind": "url-pattern",
      "order_status": "exact",
      "provenance": "web.xml",
      "servlet_names": [],
      "url_patterns": [
        "/legacy/*"
      ],
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "FilterMapping"
    },
    "JSPView|servlet_jsp::artifact::63db68e2aa944dba1b1c::generation::0a6839d5d6ab98c0fa32f00c": {
      "semantic_id": "servlet_jsp::artifact::63db68e2aa944dba1b1c",
      "symbol_id": "servlet_jsp::artifact::63db68e2aa944dba1b1c",
      "generation_id": "0a6839d5d6ab98c0fa32f00c",
      "kind": "JSPView",
      "project_name": "parity_servlet_jsp",
      "module_id": "servlet_jsp_module::cdb4ee2aea69cc6a8333",
      "language": "servlet_jsp",
      "framework": "servlet_jsp",
      "file_path": "src/main/webapp/WEB-INF/jsp/footer.jsp",
      "start_line": 1,
      "end_line": 1,
      "start_column": 1,
      "end_column": 1,
      "confidence": 1.0,
      "extraction_method": "tree_sitter_jsp",
      "resolution_status": "resolved",
      "raw_value": "",
      "resolved_value": "",
      "source_symbol_id": "",
      "parser_version": "servlet-jsp-v2026-07-13-1",
      "artifact_kind": "jsp",
      "coverage_status": "complete",
      "module_path": ".",
      "truncated": false,
      "project_id_normalized": "parity_servlet_jsp",
      "_label": "JSPView"
    },
    "JSPView|servlet_jsp::artifact::364a3fe6d417ba282789::generation::0a6839d5d6ab98c0fa32f00c": {
  

```


## Kết luận

- FAILURES: ['testdata_full: summary JSON byte-identical', 'testdata_full preview JSON: artifact byte-identical', 'TESTDATA_FULL: graph diff rỗng ngoài mask', 'inc_seed: summary JSON byte-identical', 'inc_seed preview JSON: artifact byte-identical', 'INC_SEED: graph diff rỗng ngoài mask', 'inc_run: summary JSON byte-identical', 'inc_run preview JSON: artifact byte-identical', 'INC_RUN: graph diff rỗng ngoài mask']

