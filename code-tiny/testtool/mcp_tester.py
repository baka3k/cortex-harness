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
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Allow running from repo root
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from testtool.mcp_client import MCPClient, MCPError  # noqa: E402
from testtool.tool_defaults import (                  # noqa: E402
    OTHER_CATEGORY,
    TOOL_CATEGORIES,
    TOOL_DEFAULTS,
    category_of,
    get_default,
)
from dataclasses import dataclass, field, asdict  # noqa: E402

# ── Hybrid discover reconciliation ────────────────────────────────────────────
# Tool names declared in TOOL_DEFAULTS — cheap membership lookup for the
# "no default" hint shown next to server-only tools in the Other bucket.
_DEFAULT_NAMES: frozenset = frozenset(TOOL_DEFAULTS.keys())


@dataclass
class SyncReport:
    """Outcome of reconciling live ``tools/list`` against ``TOOL_DEFAULTS``.

    Drives the startup banner, run-all filtering, and the
    ``⚠ no default`` / ``✗ offline`` decorations in the tool menu.
    """

    known: List[Dict[str, Any]] = field(default_factory=list)
    server_only: List[Dict[str, Any]] = field(default_factory=list)
    stale: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def live_total(self) -> int:
        return len(self.known) + len(self.server_only)

    def runnable(self, filtered: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Subset of ``filtered`` that is safe to call (live + not stale)."""
        stale_names = {t["name"] for t in self.stale}
        return [t for t in filtered if t.get("name") not in stale_names]


def _reconcile_tools(
    live_tools: List[Dict[str, Any]],
    default_names: frozenset,
) -> SyncReport:
    """Split live tools into known / server-only / stale sets.

    A tool is **known** when the server registers it AND it appears in
    ``TOOL_DEFAULTS``; **server-only** when the server registers it but
    has no default; **stale** when a default exists but the server no
    longer registers the tool (would always fail a ``tools/call``).
    """
    live_names = {t.get("name", "") for t in live_tools if t.get("name")}
    known: List[Dict[str, Any]] = []
    server_only: List[Dict[str, Any]] = []
    for t in live_tools:
        name = t.get("name", "")
        if name in default_names:
            known.append(t)
        else:
            server_only.append(t)
    stale = [
        {"name": n, "description": "(default only — not registered by server)"}
        for n in sorted(default_names - live_names)
    ]
    return SyncReport(known=known, server_only=server_only, stale=stale)


def _print_sync_report(report: SyncReport) -> None:
    """Emit the one-line sync summary plus any drift warnings."""
    print(
        GREEN(f"  {report.live_total} tools available.")
        + DIM(
            f"  ({len(report.known)} known · "
            f"{len(report.server_only)} server-only · "
            f"{len(report.stale)} stale)"
        )
    )
    if report.server_only:
        names = ", ".join(t.get("name", "?") for t in report.server_only)
        print(YELLOW(f"  ⚠ No default payload for: {names}"))
    if report.stale:
        names = ", ".join(t.get("name", "?") for t in report.stale)
        print(YELLOW(f"  ⚠ Stale defaults (no matching server tool): {names}"))

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
    sync_report: Optional[SyncReport] = None,
) -> List[Dict[str, Any]]:
    """Print categorized tool list and return the visible subset in display order.

    Numbers in the menu are assigned category-by-category, so the returned list
    is flattened in the same order — a user typing ``33`` gets the tool shown
    at row 33, regardless of how the MCP server ordered its tool list.

    The ``sync_report`` (when supplied) drives the drift hints: server-only
    tools get ``⚠ no default`` next to their name, and stale tools render
    with ``✗ offline``.
    """
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
    display_ordered: List[Dict[str, Any]] = []
    for category, items in buckets:
        if not items:
            continue
        print(BOLD(f"  ▸ {category} ") + DIM(f"({len(items)})"))
        for tool in items:
            display_ordered.append(tool)
            name = tool.get("name", "?")
            desc = tool.get("description", "")
            first_line = desc.split("\n", 1)[0][:72] if desc else ""
            num = CYAN(f"{len(display_ordered):>3}.")
            # Drift decorations: only attach when a sync_report is provided.
            hint = ""
            if sync_report is not None:
                if name not in _DEFAULT_NAMES:
                    hint = f" {YELLOW('⚠ no default')}"
                elif not any(t.get("name") == name for t in sync_report.known):
                    hint = f" {DIM('✗ offline')}"
            print(f"  {num} {BOLD(name)}{hint}")
            if first_line:
                print(f"       {DIM(first_line)}")
        print()

    if shown == 0:
        print(DIM("  (no tools match the current filter/scope)"))
        print()
    print(_hr())
    print(
        DIM(
            f"  {shown}/{total} tools  |  <number> select  /text filter  "
            f"c category  * all  a run-all  q quit"
        )
    )
    print(_hr())
    return display_ordered


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


def _ask_input_source(tool_name: str) -> Optional[Dict[str, Any]]:
    """
    Ask user for a JSON input file, or load the on-disk default payload.

    The default is read fresh from ``input_exam/{tool_name}.json`` (or
    ``TOOL_DEFAULTS``) on every call — nothing is cached in RAM, so edits to
    the file between runs are picked up immediately.
    Returns the loaded payload, or None to go back.
    """
    print()
    print(f"  {DIM('JSON input file path')}  {DIM('(Enter = load default from file, b = back)')}")
    try:
        raw = input(f"  {CYAN('›')} file: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if raw.lower() in ("b", "back", "q"):
        return None

    if raw == "":
        # Always reload from disk / TOOL_DEFAULTS — no in-memory cache.
        payload = get_default(tool_name)
        _d = os.path.join(
            os.path.dirname(__file__), "input_exam", f"{tool_name}.json"
        )
        source = (
            os.path.relpath(_d)
            if os.path.isfile(_d)
            else "TOOL_DEFAULTS"
        )
    else:
        payload = _load_payload_from_file(raw)
        if payload is None:
            return None
        source = os.path.basename(raw)

    print(GREEN(f"  Loaded from: {source}"))
    return payload


# ── Payload editor ────────────────────────────────────────────────────────────

def _edit_payload(tool_name: str, tool_schema: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """
    Step 1: ask for JSON file (or load default from disk).
    Step 2: show payload and offer edit/run/back.
    Returns final dict or None to cancel.

    No payload is retained between calls — each invocation reloads from file.
    """
    print()
    print(BOLD(f"  Tool: {GREEN(tool_name)}"))
    if tool_schema and tool_schema.get("description"):
        desc = tool_schema["description"].split("\n")[0][:80]
        print(f"  {DIM(desc)}")
    print(_hr())

    # Step 1: load input
    payload = _ask_input_source(tool_name)
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
            loaded = _ask_input_source(tool_name)
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

def _result_is_empty(result: Any) -> bool:
    """Heuristic: empty list / dict / dict-of-empty-lists counts as ``EMPTY``.

    A false ``EMPTY`` classification is harmless (the tool still ran). The
    goal is to distinguish "no data" from "no problem".
    """
    if result is None:
        return True
    if isinstance(result, list):
        return len(result) == 0
    if isinstance(result, dict):
        if not result:
            return True
        return all(
            isinstance(v, (list, dict)) and len(v) == 0
            for v in result.values()
        )
    if isinstance(result, str):
        return not result.strip()
    return False


@dataclass
class _ToolRunResult:
    """Outcome of a single tool call.

    Used by both the interactive single-run path (printed inline) and the
    run-all batch path (aggregated into a summary table and log file).
    """

    tool: str
    status: str  # "pass" | "empty" | "fail"
    elapsed: float
    payload: Dict[str, Any]
    result: Any = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    response_snippet: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe form (dataclasses.asdict plus non-iterable-safe fields)."""
        d = asdict(self)
        # ``result`` may contain non-serializable types; coerce to string.
        try:
            json.dumps(d["result"])
        except (TypeError, ValueError):
            d["result"] = repr(d["result"])
        return d


# Cap on raw response text captured on failure — keep failure detail useful
# without flooding the operator's terminal.
_RESPONSE_SNIPPET_LIMIT = 500


def _execute_call(
    client: MCPClient,
    tool_name: str,
    payload: Dict[str, Any],
) -> _ToolRunResult:
    """Call ``tools/call`` and wrap the outcome in a ``_ToolRunResult``.

    Time and traceback are captured uniformly; status is decided by the
    caller (``_run_tool`` for inline display, ``_run_all`` for batch runs).
    """
    t0 = time.time()
    try:
        result = client.call_tool(tool_name, payload)
        elapsed = time.time() - t0
        return _ToolRunResult(
            tool=tool_name,
            status="empty" if _result_is_empty(result) else "pass",
            elapsed=elapsed,
            payload=payload,
            result=result,
        )
    except MCPError as exc:
        return _ToolRunResult(
            tool=tool_name,
            status="fail",
            elapsed=time.time() - t0,
            payload=payload,
            error=f"MCPError: {exc}",
            traceback=traceback.format_exc(),
            response_snippet=str(exc)[:_RESPONSE_SNIPPET_LIMIT],
        )
    except Exception as exc:
        return _ToolRunResult(
            tool=tool_name,
            status="fail",
            elapsed=time.time() - t0,
            payload=payload,
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        )


def _print_status(status: str) -> str:
    """Colorized status label used by both single-run and summary blocks."""
    if status == "pass":
        return GREEN("PASS")
    if status == "empty":
        return YELLOW("EMPTY")
    if status == "fail":
        return RED("FAIL")
    return DIM(status.upper())


def _print_failure_block(run: _ToolRunResult) -> None:
    """Inline failure detail block for single-tool runs.

    Mirrors the log-entry shape so operators see the same context either way.
    """
    elapsed_str = f"{run.elapsed:.2f}s"
    print(_hr())
    print(BOLD(f"  {RED('FAIL')}  {run.tool}  ") + DIM(f"({elapsed_str})"))
    print(_hr())
    print(DIM("  payload: ") + json.dumps(run.payload, ensure_ascii=False))
    if run.error:
        print(DIM("  error:   ") + run.error)
    if run.response_snippet and run.response_snippet != run.error:
        print(DIM("  server:  ") + run.response_snippet)
    if run.traceback:
        print(DIM("  trace:"))
        print(textwrap.indent(run.traceback.rstrip(), "    "))
    print(_hr())


def _run_all(
    client: MCPClient,
    scope: List[Dict[str, Any]],
    sync_report: Optional[SyncReport],
) -> None:
    """Run every tool in ``scope`` with its default payload, continue on error.

    Inline progress + summary table + structured log file (``outputs/``).

    The ``activate_project_removed`` tool is **skipped** (always returns a
    deprecation stub). Stale tools (defaults with no matching server tool)
    are excluded up-front — calling them would only surface a transport
    error and waste a slot.
    """
    if not scope:
        print(YELLOW("  No tools in scope — adjust the filter or `*` first."))
        return

    now = datetime.now()
    started_at = now.strftime("%Y-%m-%dT%H:%M:%S")
    stamp = now.strftime("%Y%m%d-%H%M%S")

    # Filter out tools that are unsafe to call (stale + the deprecation stub).
    skip_names = {"activate_project_removed"}
    if sync_report is not None:
        skip_names |= {t.get("name", "") for t in sync_report.stale}
    runnable = [t for t in scope if t.get("name") not in skip_names]
    skipped = [t for t in scope if t.get("name") in skip_names
               and t.get("name") == "activate_project_removed"]
    stale = [t for t in scope if t.get("name") in skip_names
             and t.get("name") != "activate_project_removed"]

    total = len(runnable) + len(skipped) + len(stale)
    print()
    print(_hr())
    print(
        BOLD(f"  Run-all  {DIM(f'{len(runnable)} tools')}")
        + (f" {DIM('(1 skipped)')}" if skipped else "")
    )
    print(_hr())

    results: List[_ToolRunResult] = []
    aborted = False
    try:
        for idx, tool in enumerate(runnable, 1):
            name = tool.get("name", "?")
            payload = get_default(name)
            run = _execute_call(client, name, payload)
            results.append(run)
            label = _print_status(run.status)
            elapsed_str = f"{run.elapsed:.2f}s"
            print(
                f"  [{CYAN(f'{idx}/{len(runnable)}')}] "
                f"{BOLD(name)} … {label} {DIM(elapsed_str)}"
            )
            _emit_inline_progress(run)
    except KeyboardInterrupt:
        print(YELLOW("\n  Aborted by user — partial summary follows."))
        aborted = True

    finished_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # Tally by status.
    by_status: Dict[str, List[_ToolRunResult]] = {"pass": [], "empty": [], "fail": []}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    # Pre-populate the skip groups so they appear in the summary.
    skip_results: List[_ToolRunResult] = []
    for t in skipped:
        skip_results.append(_ToolRunResult(
            tool=t.get("name", "?"), status="skip", elapsed=0.0, payload={},
        ))

    print()
    print(_hr())
    summary_line = _print_summary(by_status, skip_results, results)
    print(_hr())

    failures = [r for r in results if r.status == "fail"]
    if failures:
        print()
        print(BOLD(f"  Failures ({len(failures)}):"))
        print(_hr())
        for r in failures:
            _print_failure_block(r)
        print(_hr())

    # Persist the run to outputs/testtool-runall-{timestamp}.json.
    log_path = _save_run_log(
        stamp=stamp,
        started_at=started_at,
        finished_at=finished_at,
        aborted=aborted,
        scope_size=total,
        results=results,
        skip_results=skip_results,
        summary_line=summary_line,
    )
    if log_path is not None:
        print(GREEN(f"  Log saved → {log_path}"))


def _emit_inline_progress(run: _ToolRunResult) -> None:
    """One additional line of context on FAIL — keeps the progress scannable."""
    if run.status == "fail" and run.error:
        print(DIM(f"          {run.error}"))


def _print_summary(
    by_status: Dict[str, List[_ToolRunResult]],
    skip_results: List[_ToolRunResult],
    results: List[_ToolRunResult],
) -> str:
    """Render the summary block; return the single-line tally for the log."""
    counts = {
        "pass": len(by_status.get("pass", [])),
        "empty": len(by_status.get("empty", [])),
        "fail": len(by_status.get("fail", [])),
        "skip": len(skip_results),
    }
    total_elapsed = sum(r.elapsed for r in results)

    def _names(rs: List[_ToolRunResult]) -> str:
        names = [r.tool for r in rs]
        if len(names) <= 6:
            return ", ".join(names) if names else "—"
        return ", ".join(names[:6]) + f", … (+{len(names) - 6} more)"

    line = (
        f"  PASS {counts['pass']:>3}   "
        f"EMPTY {counts['empty']:>3}   "
        f"FAIL {counts['fail']:>3}   "
        f"SKIP {counts['skip']:>3}   "
        f"{DIM(f'total {total_elapsed:.1f}s')}"
    )
    print(line)
    if counts["pass"]:
        print(DIM(f"  pass  : ") + _names(by_status["pass"]))
    if counts["empty"]:
        print(DIM(f"  empty : ") + _names(by_status["empty"]))
    if counts["fail"]:
        print(DIM(f"  fail  : ") + _names(by_status["fail"]))
    if counts["skip"]:
        print(DIM(f"  skip  : ") + _names(skip_results))
    return line.strip()


def _save_run_log(
    stamp: str,
    started_at: str,
    finished_at: str,
    aborted: bool,
    scope_size: int,
    results: List[_ToolRunResult],
    skip_results: List[_ToolRunResult],
    summary_line: str,
) -> Optional[str]:
    """Persist the run to ``outputs/testtool-runall-{stamp}.json``.

    Returns the absolute log path on success, ``None`` if writing fails.
    """
    try:
        out_dir = os.path.join(os.getcwd(), "outputs")
        os.makedirs(out_dir, exist_ok=True)
        log_path = os.path.join(out_dir, f"testtool-runall-{stamp}.json")
        payload = {
            "started_at": started_at,
            "finished_at": finished_at,
            "aborted": aborted,
            "scope_size": scope_size,
            "summary": summary_line,
            "results": [r.to_dict() for r in results + skip_results],
        }
        with open(log_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        return os.path.abspath(log_path)
    except OSError as exc:
        print(YELLOW(f"  Log save failed: {exc}"))
        return None


def _run_tool(client: MCPClient, tool_name: str, payload: Dict[str, Any]) -> None:
    """Single-tool entry: execute, then display result or failure block."""
    print()
    print(DIM(f"  Calling {tool_name} …"))
    run = _execute_call(client, tool_name, payload)

    if run.status == "fail":
        _print_failure_block(run)
        # Post-failure actions: retry or continue (no save — nothing to save).
        while True:
            print(f"  {CYAN('r')} retry   {CYAN('Enter')} continue")
            try:
                act = input(f"  {CYAN('›')} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if act == "r":
                run = _execute_call(client, tool_name, payload)
                if run.status == "fail":
                    _print_failure_block(run)
                    continue
                break
            break
        if run.status != "fail":
            print(_hr())
            print(BOLD(f"  Result  {DIM(f'({run.elapsed:.2f}s)')}"))
            print(_hr())
            print(textwrap.indent(_pprint(run.result), "  "))
            print(_hr())
        return

    # PASS / EMPTY — normal single-run display path.
    status_label = _print_status(run.status)
    print(_hr())
    print(BOLD(f"  Result  {status_label}  ") + DIM(f"({run.elapsed:.2f}s)"))
    print(_hr())
    pretty = _pprint(run.result)
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
            _save_result(run.result)
        else:
            break


# ── Main interactive loop ─────────────────────────────────────────────────────

def interactive(
    client: MCPClient,
    tools: List[Dict[str, Any]],
    start_tool: Optional[str] = None,
    sync_report: Optional[SyncReport] = None,
) -> None:
    # No in-RAM payload cache — every tool run reloads from input_exam/{tool}.json
    # (or TOOL_DEFAULTS) so file edits are picked up immediately.
    filter_text = ""
    scope_category = ""

    if start_tool:
        # Jump directly to the specified tool — bypass menu (CLI flag path).
        match = next((t for t in tools if t["name"] == start_tool), None)
        if match:
            payload = _edit_payload(start_tool, tool_schema=match)
            if payload is not None:
                _run_tool(client, start_tool, payload)
        else:
            print(RED(f"  Tool '{start_tool}' not found."))

    while True:
        filtered = _render_tool_list(tools, filter_text, scope_category, sync_report)

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

        # 'a' runs every visible tool with its default payload.
        # Guarded the same way 'c' is: only run-all if no visible tool is
        # literally named 'a' (none today; documented to keep the shortcut
        # predictable).
        if (raw == "a" or raw.lower() == "run-all") and not any(
            t["name"] == "a" for t in filtered
        ):
            _run_all(client, filtered, sync_report)
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
        payload = _edit_payload(tool_name, tool_schema=tool)
        if payload is not None:
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

    sync_report = _reconcile_tools(tools, _DEFAULT_NAMES)
    _print_sync_report(sync_report)
    print()

    interactive(client, tools, start_tool=args.tool, sync_report=sync_report)


if __name__ == "__main__":
    main()
