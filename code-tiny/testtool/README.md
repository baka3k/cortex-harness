# MCP Test Tool

Lightweight interactive tester for MCP tools (call `tools/call` on an MCP server).

**Purpose**

- Let you quickly invoke MCP tools (search_by_code, search_functions, etc.) from a local CLI without the agent.
- Provide editable JSON payloads, optional file-based inputs, and result saving.

**Prerequisites**

- Python 3.8+ and project virtualenv active (repository uses `./.venv`).
- MCP server already running (unified MCP or FastMCP) and reachable. Default endpoint: `http://127.0.0.1:8788/mcp`.

Files

- `mcp_client.py` — minimal client for MCP streamable-http protocol (initialize, tools/list, tools/call).
- `tool_defaults.py` — default JSON payloads per tool. Edit to match your common test inputs.
- `mcp_tester.py` — interactive CLI menu to pick a tool, load/edit payload, run and save results.

Quick start

1. Activate virtualenv and start the CLI (MCP server must be running):

```bash
source .venv/bin/activate
python testtool/mcp_tester.py
```

2. Jump directly to a tool or use a custom endpoint:

```bash
python testtool/mcp_tester.py --tool search_by_code
python testtool/mcp_tester.py --endpoint http://127.0.0.1:8788/mcp
```

Menu workflow

- Select tool (enter number or name).
- You are prompted for a JSON input file path. Options:
  - Type a file path to load a JSON object to use as the request payload.
  - Press Enter to use the default or previously cached payload for that tool.
  - Type `b` to go back.
- After payload is loaded you can:
  - Press Enter to run the tool immediately.
  - `e` to edit payload in your `$EDITOR`.
  - `i` to edit key/value pairs inline.
  - `f` to load another JSON file.
  - `r` to reset payload to the defaults from `tool_defaults.py`.
- After run: result is printed and you can save it to a JSON file.

Default payloads and `search_by_code` example

- The `tool_defaults.py` file contains default payloads. It includes an example `search_by_code` payload (the same query you provided). You can also create a JSON file and load it when prompted.

Example input file for `search_by_code` (`/tmp/search_authentication.json`):

```json
{
  "query": "DataNormal|Authen|Login|SignIn|Account",
  "db": "neo4j",
  "top_k": 500,
  "content_mode": "summary",
  "include_raw_fields": false
}
```

Notes

- The tester calls `tools/call` on the MCP endpoint and therefore requires the MCP server to be running and reachable.
- The payload is cached per session so repeated runs of the same tool will reuse the last payload unless you reset or load a file.
- The `mcp_client` handles both JSON and SSE-style responses and will try to extract the final JSON result.

Advanced

- You can import `testtool.mcp_client.MCPClient` in other scripts to programmatically call tools.
- Edit `tool_defaults.py` to add or tweak default JSON for your most-used tools.

Troubleshooting

- If the tool list fails, check the MCP endpoint and server logs and try `--endpoint` to point to the correct URL.
- If responses look truncated, use the menu option to save results to a file.

License & maintenance

- This helper is intentionally minimal. Feel free to extend it (add test suites, CSV batch runs, or HTTP retries) to fit your workflow.
