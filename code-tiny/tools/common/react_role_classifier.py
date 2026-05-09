"""
react_role_classifier.py
========================
LLM-based React role classifier for TypeScript functions.

Called from ts_analyzer after tree-sitter static analysis, this module
upgrades "uncertain" react_role assignments (functions that are
PascalCase+JSX but have no navigation signals) by asking the LLM to
inspect the actual source code.

Architecture
────────────
  Static analysis (_detect_react_role) sets a first-pass react_role.
  This module is called ONLY for "uncertain" candidates:
    - react_role == "component"  (PascalCase+JSX, no nav signal detected)
    - react_role == ""           (PascalCase+JSX was borderline)

  A single batch API call is made per FILE (not per function), which
  includes all uncertain candidates in that file. The LLM sees:
    - The file path (gives directory context: screens/, pages/, ...)
    - For each candidate: function name + first 30 lines of code

  The LLM responds with a compact JSON array:
    [{"name": "SelectApprover", "role": "screen"}, ...]

  react_role on the FunctionDef objects is updated in-place.

Gating
──────
  This classifier is DISABLED by default. Opt in via env var:
    REACT_ROLE_LLM_CLASSIFY=1  (or: true / yes)

  Other env vars (share with living-doc pipeline):
    LLM_API_BASE   — OpenAI-compatible base URL (default: https://api.openai.com/v1)
    LLM_API_KEY    — required when enabled
    LLM_MODEL      — model name (default: gpt-4o-mini)
    LLM_TIMEOUT    — seconds per request (default: 20)

  When disabled: this module is imported but classify_file() returns
  immediately without any network call, adding zero overhead to scans.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from tools.ts.ts_analyzer import FunctionDef


# ── Configuration ─────────────────────────────────────────────────────────────

def _is_enabled() -> bool:
    val = os.environ.get("REACT_ROLE_LLM_CLASSIFY", "").strip().lower()
    return val in {"1", "true", "yes"}


def _api_base() -> str:
    return os.environ.get("LLM_API_BASE", "https://api.openai.com/v1").rstrip("/")


def _api_key() -> str:
    return os.environ.get("LLM_API_KEY", "")


def _model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


def _timeout() -> int:
    try:
        return int(os.environ.get("LLM_TIMEOUT", "20"))
    except ValueError:
        return 20


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a React / React-Native expert.
Given a file path and a list of exported React components from that file,
classify each one as exactly one of: screen | component | hook | util

Definitions:
- screen : A top-level navigation destination. It is registered in a navigator
  (Stack, Tab, Drawer), receives `navigation` / `route` props, uses navigation
  hooks (useNavigation, useRoute, useFocusEffect, ...), calls
  navigation.navigate / router.push, or its file lives in screens/ pages/
  routes/ navigation/ views/ directories. A screen almost always renders a full
  view, often with <SafeAreaView> or <ScrollView> at the root.
- component : A reusable UI piece. It does NOT navigate and is composed inside
  screens or other components. Examples: Button, Card, Avatar, Modal, InputField.
- hook : Starts with "use" + uppercase letter. Encapsulates stateful logic.
- util : A plain function with no JSX and no React state.

Rules:
1. Return ONLY a raw JSON array — no markdown, no explanation.
2. Each item: {"name": "<function name>", "role": "screen|component|hook|util"}
3. If you are uncertain between screen and component, choose based on whether
   it would be registered as a route/navigator entry in the app.

Examples:
- SelectApprover in screens/approval/SelectApprover.tsx → screen
- ApprovalCard in components/ApprovalCard.tsx → component
- useApprovalFlow in hooks/useApprovalFlow.ts → hook\
"""


# ── Code snippet truncation ───────────────────────────────────────────────────

def _truncate_code(code: str, max_lines: int = 35) -> str:
    """Keep first `max_lines` lines of source to save tokens."""
    lines = code.split("\n")
    if len(lines) <= max_lines:
        return code
    return "\n".join(lines[:max_lines]) + "\n// ... (truncated)"


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _http_post(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── JSON extraction (robust) ──────────────────────────────────────────────────

def _extract_json_array(content: str) -> list | None:
    if not content:
        return None
    cleaned = re.sub(r"```(?:json)?\s*\n?", "", content).strip()
    # Find outermost [...] block
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

_VALID_ROLES = {"screen", "component", "hook", "util", "middleware"}


def classify_file(
    functions: "List[FunctionDef]",
    file_path: str,
    verbose: bool = False,
) -> None:
    """LLM-classify uncertain react_role candidates in a single file.

    Mutates react_role on each qualifying FunctionDef in-place.
    Returns immediately (no-op) if REACT_ROLE_LLM_CLASSIFY is not set.

    Args:
        functions:  All FunctionDef objects parsed from the file.
        file_path:  Relative path of the file (for directory signals in prompt).
        verbose:    Print classification results to stdout when True.
    """
    if not _is_enabled():
        return

    api_key = _api_key()
    if not api_key:
        if verbose:
            print("[react_role_classifier] LLM_API_KEY not set — skipping", flush=True)
        return

    # Select uncertain candidates:
    #   - react_role already set to "component" by static analysis (borderline)
    #   - react_role "" on a PascalCase function (static missed it)
    candidates = [
        f for f in functions
        if f.react_role in {"component", ""}
        and f.name
        and f.name[0].isupper()
        # Hooks are already definitive — skip them
        and not (f.name.startswith("use") and len(f.name) > 3 and f.name[3].isupper())
    ]

    if not candidates:
        return

    # Build user message: file path + one block per candidate
    blocks: list[str] = [f"File: {file_path}\n"]
    for func in candidates:
        snippet = _truncate_code(func.code, max_lines=35)
        blocks.append(f"--- {func.name} ---\n{snippet}\n")
    user_message = "\n".join(blocks)

    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0,
        "max_tokens": 256,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    url = f"{_api_base()}/chat/completions"

    try:
        response = _http_post(url, headers, payload, timeout=_timeout())
        raw = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (urllib.error.URLError, OSError, KeyError) as exc:
        if verbose:
            print(f"[react_role_classifier] API error for {file_path}: {exc}", flush=True)
        return

    results = _extract_json_array(raw)
    if not results:
        if verbose:
            print(f"[react_role_classifier] Bad LLM response for {file_path}: {raw[:120]}", flush=True)
        return

    # Build name → role lookup (LLM may return any order)
    name_to_role: dict[str, str] = {}
    for item in results:
        if isinstance(item, dict):
            name = item.get("name", "")
            role = str(item.get("role", "")).strip().lower()
            if name and role in _VALID_ROLES:
                name_to_role[name] = role

    # Apply overrides — only upgrade/correct, never downgrade to "util"/"" for
    # functions static analysis already identified as hooks/middleware
    for func in candidates:
        new_role = name_to_role.get(func.name)
        if new_role and new_role != func.react_role:
            if verbose:
                print(
                    f"[react_role_classifier] {func.name}: "
                    f"{func.react_role!r} → {new_role!r}  ({file_path})",
                    flush=True,
                )
            func.react_role = new_role
