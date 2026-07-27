#!/usr/bin/env python3
"""
MCP Tool Tester — interactive CLI to call MCP tools without an agent.

Usage:
    python testtool/mcp_tester.py                       # interactive menu
    python testtool/mcp_tester.py --tool search_by_code # jump directly to a tool
    python testtool/mcp_tester.py --endpoint http://... # custom endpoint

Controls inside the menu:
    <number>   select tool
    /text      filter tool list by name
    q / quit   exit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import subprocess
import textwrap
import time
from typing import Any, Dict, List, Optional, Tuple

# Allow running from repo root
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from testtool.mcp_client import MCPClient, MCPError  # noqa: E402
from testtool.tool_defaults import (                  # noqa: E402
    OTHER_CATEGORY,
    TOOL_CATEGORIES,
    category_of,
    get_default,
)

# ── readline (best-effort) ───────────────────────────────────────────────────
try:
    import readline
    readline.set_history_length(200)
except ImportError:
    pass

# ── ANSI colours (disable if not a tty) ─────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


BOLD   = lambda s: _c("1", s)
DIM    = lambda s: _c("2", s)
GREEN  = lambda s: _c("32", s)
CYAN   = lambda s: _c("36", s)
YELLOW = lambda s: _c("33", s)
RED    = lambda s: _c("31", s)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hr(char: str = "─", width: int = 72) -> str:
    return DIM(char * width)


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{CYAN('›')} {text}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


def _confirm(text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    ans = _prompt(f"{text} ({hint})", "y" if default else "n").lower()
    return ans in ("y", "yes", "")


def _pprint(data: Any, indent: int = 2) -> str:
    """Pretty-print with truncation hint."""
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    lines = text.splitlines()
    MAX_LINES = 200
    if len(lines) > MAX_LINES:
        shown = "\n".join(lines[:MAX_LINES])
        return shown + f"\n{DIM(f'... ({len(lines) - MAX_LINES} more lines hidden — save to file to see all)')}"
    return text


def _open_in_editor(content: str) -> str:
    """Open content in $EDITOR and return modified text."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    suffix = ".json"
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False,
                                     encoding="utf-8") as fh:
        fh.write(content)
        path = fh.name
    try:
        subprocess.call([editor, path])
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def _save_result(data: Any) -> None:
    path = _prompt("Save to file", "result.json")
    if not path:
        return
    if not path.endswith(".json"):
        path += ".json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(GREEN(f"  Saved → {os.path.abspath(path)}"))


# ── Tool list display ─────────────────────────────────────────────────────────

def _matches(tool: Dict[str, Any], text: str) -> bool:
    """Case-insensitive match against ``tool['name']`` and the first description line."""
    if not text:
        return True
    needle = text.lower()
    if needle in (tool.get("name", "") or "").lower():
        return True
    desc = (tool.get("description", "") or "").split("\n", 1)[0]
    return needle in desc.lower()


def _filter_tools(
    tools: List[Dict[str, Any]],
    filter_text: str,
    scope_category: str,
) -> List[Dict[str, Any]]:
    """Apply description-aware filter, then optional category scope."""
    visible = [t for t in tools if _matches(t, filter_text)]
    if scope_category:
        visible = [
            t for t in visible
            if category_of(t.get("name", "")) == scope_category
        ]
    return visible


def _bucket_by_category(
    tools: List[Dict[str, Any]],
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Group tools by category.

    Preserves the declared order in ``TOOL_CATEGORIES`` and appends the
    ``OTHER_CATEGORY`` bucket last so future tools render without code edits.
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for cat in TOOL_CATEGORIES:
        buckets[cat] = []
    buckets.setdefault(OTHER_CATEGORY, [])
    for t in tools:
        cat = category_of(t.get("name", ""))
        buckets.setdefault(cat, []).append(t)

    ordered: List[Tuple[str, List[Dict[str, Any]]]] = [
        (cat, items) for cat, items in buckets.items() if items
    ]
    # Ensure "Other" bucket is last if it has entries.
    other_idx = next(
        (i for i, (c, _) in enumerate(ordered) if c == OTHER_CATEGORY),
        None,
    )
    if other_idx is not None and other_idx != len(ordered) - 1:
        ordered.append(ordered.pop(other_idx))
    return ordered


def _choose_category(
    tools: List[Dict[str, Any]],
    current_scope: str,
) -> Optional[str]:
    """Show category summary and let the user pick one.

    Returns:
        ``""``        — switch to "all categories" (``*``).
        category name — scope to that category.
        ``None``      — no change (empty input, EOF, or unknown name).
    """
    # Per-category live counts so users see distribution before picking.
    counts: Dict[str, int] = {cat: 0 for cat in TOOL_CATEGORIES}
    counts.setdefault(OTHER_CATEGORY, 0)
    for t in tools:
        cat = category_of(t.get("name", ""))
        counts[cat] = counts.get(cat, 0) + 1

    options: List[str] = list(TOOL_CATEGORIES) + [OTHER_CATEGORY]

    print()
    print(BOLD("  Categories:"))
    for i, cat in enumerate(options, 1):
        marker = DIM(" (current)") if cat == current_scope else ""
        print(f"   {CYAN(f'{i:>2}.')} {cat} {DIM(f'({counts[cat]})')}{marker}")
    print(f"   {CYAN(' *')} all categories {DIM(f'({sum(counts.values())})')}")
    print(DIM("  Enter a number or category name. Enter = cancel."))

    try:
        raw = input(f"  {CYAN('›')} category: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if raw == "":
        return None
    if raw.lower() in ("*", "all"):
        return ""

    try:
        idx = int(raw)
        if 1 <= idx <= len(options):
            return options[idx - 1]
    except ValueError:
        pass

    for cat in options:
        if cat.lower() == raw.lower():
            return cat

    print(RED(f"  Unknown category: {raw!r}"))
    return None


def _render_tool_list(
    tools: List[Dict[str, Any]],
    filter_text: str = "",
    scope_category: str = "",
) -> List[Dict[str, Any]]:
    """Print categorized tool list and return the visible subset."""
    visible = _filter_tools(tools, filter_text, scope_category)
    total = len(tools)
    shown = len(visible)
    scope_label = scope_category or "all"
    filter_label = filter_text or '""'

    print()
    print(_hr())
    print(
        BOLD("  MCP Tool Tester   ")
        + DIM(f"scope: {scope_label}   ")
        + DIM(f"filter: {filter_label}   ")
        + DIM(f"({total} tools)")
    )
    print(_hr())

    buckets = _bucket_by_category(visible)
    idx = 0
    for category, items in buckets:
        print(BOLD(f"  ▸ {category} ") + DIM(f"({len(items)})"))
        for tool in items:
            idx += 1
            name = tool.get("name", "?")
            desc = tool.get("description", "")
            first_line = desc.split("\n", 1)[0][:72] if desc else ""
            num = CYAN(f"{idx:>3}.")
            print(f"  {num} {BOLD(name)}")
            if first_line:
                print(f"       {DIM(first_line)}")
        print()

    if shown == 0:
        print(DIM("  (no tools match the current filter/scope)"))
        print()
    print(_hr())
    print(
        DIM(
            f"  {shown}/{total} tools  |  /filter  c category  * all  "
            f"<number> select  q quit"
        )
    )
    print(_hr())
    return visible


# ── Payload loader ────────────────────────────────────────────────────────────

def _load_payload_from_file(path: str) -> Optional[Dict[str, Any]]:
    """Load JSON payload from file. Returns None on error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            print(RED(f"  File must contain a JSON object, got {type(data).__name__}."))
            return None
        return data
    except FileNotFoundError:
        print(RED(f"  File not found: {path}"))
        return None
    except json.JSONDecodeError as exc:
        print(RED(f"  JSON parse error in {path}:"))
        print(RED(f"    Line {exc.lineno}, column {exc.colno}: {exc.msg}"))
        if exc.lineno:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
                    if 0 < exc.lineno <= len(lines):
                        error_line = lines[exc.lineno - 1].rstrip()
                        print(RED(f"    {error_line}"))
                        if exc.colno:
                            pointer = " " * (exc.colno - 1) + "^"
                            print(RED(f"    {pointer}"))
            except:
                pass
        return None


def _ask_input_source(tool_name: str, cached: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Ask user for a JSON input file, or use cached/default payload.
    Returns the loaded payload, or None to go back.
    """
    default_label = "cached" if cached else "default"
    print()
    print(f"  {DIM('JSON input file path')}  {DIM(f'(Enter = use {default_label} payload, b = back)')}")
    try:
        raw = input(f"  {CYAN('›')} file: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if raw.lower() in ("b", "back", "q"):
        return None

    if raw == "":
        # Use cached/default
        if cached:
            payload = cached
            source = default_label
        else:
            payload = get_default(tool_name)
            # Show user the actual file path when loaded from file
            _d = os.path.join(
                os.path.dirname(__file__), "input", f"{tool_name}.json"
            )
            source = os.path.relpath(_d) if os.path.isfile(_d) else "default"
    else:
        payload = _load_payload_from_file(raw)
        if payload is None:
            return None
        source = os.path.basename(raw)

    print(GREEN(f"  Loaded from: {source}"))
    return payload


# ── Payload editor ────────────────────────────────────────────────────────────

def _edit_payload(tool_name: str, current: Dict[str, Any], tool_schema: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """
    Step 1: ask for JSON file (or use current/default).
    Step 2: show payload and offer edit/run/back.
    Returns final dict or None to cancel.
    """
    print()
    print(BOLD(f"  Tool: {GREEN(tool_name)}"))
    if tool_schema and tool_schema.get("description"):
        desc = tool_schema["description"].split("\n")[0][:80]
        print(f"  {DIM(desc)}")
    print(_hr())

    # Step 1: load input
    payload = _ask_input_source(tool_name, current)
    if payload is None:
        return None

    # Step 2: review + run loop
    while True:
        print()
        print(BOLD("  Payload:"))
        print(textwrap.indent(json.dumps(payload, ensure_ascii=False, indent=2), "    "))
        print()
        print(f"  {CYAN('Enter')} run   "
              f"{CYAN('e')} edit in $EDITOR   "
              f"{CYAN('i')} edit inline   "
              f"{CYAN('f')} load another file   "
              f"{CYAN('r')} reset to default   "
              f"{CYAN('b')} back")
        print()
        try:
            choice = input(f"  {CYAN('›')} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if choice in ("b", "q", "back"):
            return None

        if choice in ("", "run"):
            return payload

        if choice == "f":
            loaded = _ask_input_source(tool_name, payload)
            if loaded is not None:
                payload = loaded

        elif choice == "e":
            raw = _open_in_editor(json.dumps(payload, ensure_ascii=False, indent=2))
            try:
                payload = json.loads(raw)
                print(GREEN("  Payload updated."))
            except json.JSONDecodeError as exc:
                print(RED(f"  JSON parse error: {exc}  (keeping previous payload)"))

        elif choice == "i":
            print("  Enter key=value pairs (type 'done' to finish, 'clear' to reset):")
            while True:
                try:
                    kv = input(f"    {CYAN('key')}: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if kv in ("done", ""):
                    break
                if kv == "clear":
                    payload = {}
                    print(GREEN("  Payload cleared."))
                    continue
                try:
                    val_raw = input(f"    {CYAN('value')} (JSON): ").strip()
                    payload[kv] = json.loads(val_raw)
                except json.JSONDecodeError:
                    payload[kv] = val_raw
                print(GREEN(f"  Set {kv} = {payload[kv]!r}"))

        elif choice == "r":
            payload = get_default(tool_name)
            print(GREEN("  Reset to default."))


# ── Call & display result ─────────────────────────────────────────────────────

def _run_tool(client: MCPClient, tool_name: str, payload: Dict[str, Any]) -> None:
    print()
    print(DIM(f"  Calling {tool_name} …"))
    t0 = time.time()
    try:
        result = client.call_tool(tool_name, payload)
        elapsed = time.time() - t0
        print(_hr())
        print(BOLD(f"  Result  {DIM(f'({elapsed:.2f}s)')}"))
        print(_hr())
        pretty = _pprint(result)
        print(textwrap.indent(pretty, "  "))
        print(_hr())
        # Post-run actions
        while True:
            print(f"  {CYAN('s')} save result   {CYAN('Enter')} continue")
            try:
                act = input(f"  {CYAN('›')} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if act == "s":
                _save_result(result)
            else:
                break
    except MCPError as exc:
        print(RED(f"  MCP Error: {exc}"))
    except Exception as exc:
        print(RED(f"  Unexpected error: {exc}"))


# ── Main interactive loop ─────────────────────────────────────────────────────

def interactive(client: MCPClient, tools: List[Dict[str, Any]], start_tool: Optional[str] = None) -> None:
    # Per-session payload cache (retains edits across reruns)
    payload_cache: Dict[str, Dict[str, Any]] = {}
    filter_text = ""
    scope_category = ""

    if start_tool:
        # Jump directly to the specified tool — bypass menu (CLI flag path).
        match = next((t for t in tools if t["name"] == start_tool), None)
        if match:
            cached = payload_cache.get(start_tool, get_default(start_tool))
            payload = _edit_payload(start_tool, cached, tool_schema=match)
            if payload is not None:
                payload_cache[start_tool] = payload
                _run_tool(client, start_tool, payload)
        else:
            print(RED(f"  Tool '{start_tool}' not found."))

    while True:
        filtered = _render_tool_list(tools, filter_text, scope_category)

        try:
            raw = input(f"\n  {CYAN('Select')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if raw.lower() in ("q", "quit", "exit"):
            break

        if raw.startswith("/"):
            filter_text = raw[1:].strip()
            continue

        if raw == "":
            # Enter clears filter; scope is left unchanged so users can keep
            # browsing a focused bucket. Press '*' to widen scope.
            filter_text = ""
            continue

        if raw == "*":
            scope_category = ""
            continue

        # 'c' opens the category browser, unless a real tool is literally named
        # 'c' (none today; documented to keep the shortcut predictable).
        if raw == "c" and not any(t["name"] == "c" for t in filtered):
            new_scope = _choose_category(tools, scope_category)
            if new_scope is not None:
                scope_category = new_scope
            continue

        try:
            idx = int(raw)
        except ValueError:
            # Try as tool name
            match = next((t for t in filtered if t["name"] == raw), None)
            if not match:
                print(RED(f"  Unknown input: {raw!r}"))
                continue
            tool = match
        else:
            if idx < 1 or idx > len(filtered):
                print(RED(f"  Out of range (1–{len(filtered)})"))
                continue
            tool = filtered[idx - 1]

        tool_name = tool["name"]
        cached = payload_cache.get(tool_name, get_default(tool_name))
        payload = _edit_payload(tool_name, cached, tool_schema=tool)
        if payload is not None:
            payload_cache[tool_name] = payload
            _run_tool(client, tool_name, payload)

    print(DIM("\n  Bye.\n"))


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive MCP tool tester")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("MCP_ENDPOINT", "http://127.0.0.1:8788/mcp"),
        help="MCP streamable-http endpoint (default: http://127.0.0.1:8788/mcp)",
    )
    parser.add_argument(
        "--tool",
        default=None,
        help="Jump directly to this tool name on startup",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Request timeout in seconds (default: 120)",
    )
    args = parser.parse_args()

    client = MCPClient(endpoint=args.endpoint, timeout=args.timeout)

    print()
    print(BOLD("  ╔══════════════════════════════════╗"))
    print(BOLD("  ║     MCP Tool Tester              ║"))
    print(BOLD("  ╚══════════════════════════════════╝"))
    print(f"  Endpoint: {CYAN(client.endpoint)}")
    print()

    # Initialize session
    print(DIM("  Initializing MCP session…"))
    try:
        info = client.initialize()
        server_name = info.get("serverInfo", {}).get("name", "?")
        proto = info.get("protocolVersion", "?")
        print(GREEN(f"  Connected  server={BOLD(server_name)}  protocol={proto}"))
    except Exception as exc:
        print(RED(f"  Init failed: {exc}"))
        print(RED("  Is the MCP server running?"))
        sys.exit(1)

    # Discover tools
    print(DIM("  Fetching tool list…"))
    try:
        tools = client.list_tools()
    except Exception as exc:
        print(RED(f"  tools/list failed: {exc}"))
        sys.exit(1)

    if not tools:
        print(YELLOW("  No tools returned by server. Using defaults only."))
        # Build minimal tool list from defaults
        from testtool.tool_defaults import TOOL_DEFAULTS
        tools = [{"name": k, "description": ""} for k in sorted(TOOL_DEFAULTS)]

    print(GREEN(f"  {len(tools)} tools available.\n"))

    interactive(client, tools, start_tool=args.tool)


if __name__ == "__main__":
    main()
