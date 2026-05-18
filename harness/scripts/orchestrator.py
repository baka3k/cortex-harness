#!/usr/bin/env python3
"""Minimal session orchestrator for the harness lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionLog:
    session_id: str
    task_id: str
    task_type: str
    started_at: str
    ended_at: str | None
    status: str
    rounds_used: int
    verify_critical_passed: bool
    verify_attempts: list[dict[str, Any]]
    scope_violations: list[str]
    notes: list[str]
    context_file: str


class OrchestratorError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        f.write("\n")


def pick_task(features: list[dict[str, Any]], task_id: str | None) -> dict[str, Any]:
    if task_id:
        for t in features:
            if t.get("id") == task_id:
                return t
        raise OrchestratorError(f"Task not found: {task_id}")

    for t in features:
        if t.get("status") == "todo":
            return t

    raise OrchestratorError("No todo task available")


def run_command(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def append_progress(progress_file: Path, text: str) -> None:
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    with progress_file.open("a", encoding="utf-8") as f:
        f.write(text)


def parse_yaml_scalar(raw: str) -> Any:
    val = raw.strip()
    if val == "":
        return ""
    if val.startswith('"') and val.endswith('"') and len(val) >= 2:
        return val[1:-1]
    if val.startswith("'") and val.endswith("'") and len(val) >= 2:
        return val[1:-1]
    lower = val.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("null", "none"):
        return None
    if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
        try:
            return int(val)
        except ValueError:
            pass
    return val


def load_simple_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.lstrip().startswith("-"):
                continue

            indent = len(line) - len(line.lstrip(" "))
            content = line.strip()
            if ":" not in content:
                continue

            key, value = content.split(":", 1)
            key = key.strip()
            value = value.strip()

            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1] if stack else root

            if value == "":
                node: dict[str, Any] = {}
                parent[key] = node
                stack.append((indent, node))
            else:
                parent[key] = parse_yaml_scalar(value)

    return root


def cfg_get(config: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = config
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def env_or_cfg(env_name: str, config: dict[str, Any], cfg_path: str, default: str = "") -> str:
    env_val = os.getenv(env_name)
    if env_val is not None and env_val != "":
        return env_val
    val = cfg_get(config, cfg_path, default)
    if val is None:
        return ""
    return str(val)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run minimal harness session")
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default=".harness/config.yaml")
    parser.add_argument("--state", default=".harness/state/feature_list.json")
    parser.add_argument("--progress", default=".harness/state/progress.md")
    parser.add_argument("--session-log-dir", default=".harness/state/session_log")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--agent-command", default="")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    config_path = (root / args.config).resolve()
    state_path = (root / args.state).resolve()
    progress_path = (root / args.progress).resolve()
    session_log_dir = (root / args.session_log_dir).resolve()

    config = load_simple_yaml(config_path)

    payload = load_json(state_path)
    features = payload.get("features", [])
    task = pick_task(features, args.task_id)

    session_id = f"session-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    task_id = str(task.get("id"))
    task_type = str(task.get("type", "feature"))
    started = now_iso()

    max_rounds_cfg = int(cfg_get(config, "budget.max_rounds", 2) or 2)
    max_rounds = args.max_rounds if args.max_rounds is not None else max_rounds_cfg

    task["status"] = "in_progress"
    task["session_id"] = session_id
    save_json(state_path, payload)

    context_file = f".harness/state/session_log/{session_id}.context.json"

    log = SessionLog(
        session_id=session_id,
        task_id=task_id,
        task_type=task_type,
        started_at=started,
        ended_at=None,
        status="in_progress",
        rounds_used=0,
        verify_critical_passed=False,
        verify_attempts=[],
        scope_violations=[],
        notes=[],
        context_file=context_file,
    )

    append_progress(
        progress_path,
        (
            "\n## Session\n"
            f"- Session ID: {session_id}\n"
            f"- Task ID: {task_id}\n"
            f"- Started at: {started}\n"
            f"- Status: in_progress\n"
        ),
    )

    init_code, init_out = run_command(["bash", ".harness/scripts/init.sh"], root)
    if init_code != 0:
        task["status"] = "blocked"
        save_json(state_path, payload)
        log.status = "blocked"
        log.notes.append("init.sh failed")
        log.notes.append(init_out.strip())
        log.ended_at = now_iso()
        session_log_path = session_log_dir / f"{session_id}.json"
        save_json(session_log_path, asdict(log))
        append_progress(progress_path, "- Status: blocked\n- Summary: init.sh failed\n")
        return 1

    graph_mcp_url = env_or_cfg("GRAPH_MCP_URL", config, "mcp.graph_mcp_url", "http://127.0.0.1:8788/mcp")
    mind_mcp_url = env_or_cfg("MIND_MCP_URL", config, "mcp.mind_mcp_url", "http://127.0.0.1:8789/mcp")
    graph_mcp_tool = env_or_cfg("GRAPH_MCP_TOOL", config, "mcp.graph_mcp_tool", "")
    mind_mcp_tool = env_or_cfg("MIND_MCP_TOOL", config, "mcp.mind_mcp_tool", "")
    graph_args_json = env_or_cfg("GRAPH_MCP_TOOL_ARGS_JSON", config, "mcp.graph_mcp_tool_args_json", "")
    mind_args_json = env_or_cfg("MIND_MCP_TOOL_ARGS_JSON", config, "mcp.mind_mcp_tool_args_json", "")
    mind_fb_enabled = env_or_cfg("MIND_FALLBACK_ENABLED", config, "mcp.mind_fallback_enabled", "1")
    mind_fb_queries = env_or_cfg("MIND_FALLBACK_QUERIES", config, "mcp.mind_fallback_queries", "")
    mind_fb_top_k = env_or_cfg("MIND_FALLBACK_TOP_K", config, "mcp.mind_fallback_top_k", "8")
    mind_fb_max_attempts = env_or_cfg("MIND_FALLBACK_MAX_ATTEMPTS", config, "mcp.mind_fallback_max_attempts", "0")
    budget_max_tool_calls = env_or_cfg("BUDGET_MAX_TOOL_CALLS", config, "mcp.budget_max_tool_calls", "50")

    # Build context once per session using graph_mcp + mind_mcp endpoints.
    ctx_code, ctx_out = run_command(
        [
            "python3",
            ".harness/scripts/context_selector.py",
            "--state",
            ".harness/state/feature_list.json",
            "--task-id",
            task_id,
            "--output",
            context_file,
            "--graph-mcp-url",
            graph_mcp_url,
            "--mind-mcp-url",
            mind_mcp_url,
            "--graph-mcp-tool",
            graph_mcp_tool,
            "--mind-mcp-tool",
            mind_mcp_tool,
            "--graph-mcp-tool-args-json",
            graph_args_json,
            "--mind-mcp-tool-args-json",
            mind_args_json,
            "--mind-fallback-enabled",
            mind_fb_enabled,
            "--mind-fallback-queries",
            mind_fb_queries,
            "--mind-fallback-top-k",
            mind_fb_top_k,
            "--mind-fallback-max-attempts",
            mind_fb_max_attempts,
            "--budget-max-tool-calls",
            budget_max_tool_calls,
        ],
        root,
    )
    if ctx_code != 0:
        task["status"] = "blocked"
        save_json(state_path, payload)
        log.status = "blocked"
        log.notes.append("context_selector.py failed")
        log.notes.append(ctx_out.strip())
        log.ended_at = now_iso()
        session_log_path = session_log_dir / f"{session_id}.json"
        save_json(session_log_path, asdict(log))
        append_progress(progress_path, "- Status: blocked\n- Summary: context selection failed\n")
        return 1

    log.notes.append("context_selected")

    for round_no in range(1, max_rounds + 1):
        log.rounds_used = round_no

        if args.agent_command.strip():
            code, out = run_command(["bash", "-lc", args.agent_command], root)
            log.verify_attempts.append(
                {
                    "round": round_no,
                    "phase": "agent_execution",
                    "exit_code": code,
                    "output_tail": out[-2000:],
                }
            )

        verify_env = {**os.environ}
        verify_critical = cfg_get(config, "verify.critical", {}) or {}
        if verify_critical.get("test_cmd"):
            verify_env["CRITICAL_TEST_CMD"] = str(verify_critical["test_cmd"])
        if verify_critical.get("lint_cmd"):
            verify_env["CRITICAL_LINT_CMD"] = str(verify_critical["lint_cmd"])
        if verify_critical.get("type_cmd"):
            verify_env["CRITICAL_TYPE_CMD"] = str(verify_critical["type_cmd"])
        verify_code, verify_out = run_command(["bash", ".harness/scripts/verify.sh"], root, env=verify_env)
        log.verify_attempts.append(
            {
                "round": round_no,
                "phase": "verify",
                "exit_code": verify_code,
                "output_tail": verify_out[-2000:],
            }
        )

        if verify_code == 0:
            log.verify_critical_passed = True
            task["status"] = "done"
            break

    if task.get("status") != "done":
        task["status"] = "blocked"

    save_json(state_path, payload)

    log.status = str(task.get("status"))
    log.ended_at = now_iso()

    session_log_path = session_log_dir / f"{session_id}.json"
    save_json(session_log_path, asdict(log))

    append_progress(
        progress_path,
        (
            f"- Ended at: {log.ended_at}\n"
            f"- Status: {log.status}\n"
            f"- Critical verify result: {log.verify_critical_passed}\n"
            f"- Context file: {context_file}\n"
            "- Summary: patch/diff output is required for human approval\n"
        ),
    )

    print(json.dumps(asdict(log), ensure_ascii=True, indent=2))
    return 0 if log.status == "done" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OrchestratorError as exc:
        print(f"[orchestrator][error] {exc}", file=sys.stderr)
        raise SystemExit(2)
