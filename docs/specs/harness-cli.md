# harness-cli — Plan & Function List

## Overview

Add `dev harness` command group to `cli/dev.py`.
This wraps the harness orchestration layer (session lifecycle, context selection,
verify gates) as a first-class CLI command group alongside `dev sync` and `dev mcp`.

The harness scripts live in `harness/` inside this repo and are copied into a
`.harness/` directory at the root of any target project during `dev harness init`.

---

## Directory layout (in cortex-harness)

```
harness/
  scripts/
    init.sh               — MCP health check, copied verbatim
    verify.sh             — Test / lint gate, copied verbatim
    context_selector.py   — Graph-first context builder, copied verbatim
    orchestrator.py       — Session lifecycle manager, copied verbatim
  templates/
    config.yaml           — Harness config template
    AGENT.md              — Agent working contract
    feature_template.json — Task schema reference
    session_template.json — Session log schema reference
    state/
      feature_list.json   — Initial empty backlog
```

After `dev harness init --project-dir <path>`:

```
<project>/
  .harness/
    config.yaml
    AGENT.md
    scripts/
      init.sh
      verify.sh
      context_selector.py
      orchestrator.py
    templates/
      feature_template.json
      session_template.json
    state/
      feature_list.json
      session_log/          — written by orchestrator at runtime
      progress.md           — appended by orchestrator at runtime
```

---

## CLI command tree

```
dev harness
  init              Bootstrap .harness/ in a target project
  status            Show task backlog counts + MCP endpoint health
  task
    list            List all tasks (table view)
    add             Add a new task interactively
    show <id>       Show full JSON for one task
  run               Run orchestrator.py for next todo task (or --task-id)
  context <id>      Run context_selector.py and print result
  verify            Run verify.sh and print result
```

---

## Function list (additions to cli/dev.py)

### Constants

| Name | Value |
|------|-------|
| `HARNESS_SCRIPTS` | `REPO_ROOT / "harness" / "scripts"` |
| `HARNESS_TEMPLATES` | `REPO_ROOT / "harness" / "templates"` |

### Helper functions

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_harness_dir` | `(project_dir: Path) -> Path` | Returns `project_dir / ".harness"` |
| `_harness_state` | `(project_dir: Path) -> Path` | Returns `project_dir / ".harness/state"` |
| `_harness_feature_list` | `(project_dir: Path) -> Path` | Returns state dir / `feature_list.json` |
| `_harness_load_features` | `(project_dir: Path) -> dict` | Load feature_list.json; exit 1 if missing |
| `_harness_save_features` | `(project_dir: Path, payload: dict) -> None` | Save feature_list.json with indent=2 |
| `_harness_next_task_id` | `(features: list) -> str` | Auto-increment task ID: `task-NNN` |

### CLI commands

| Command | Click decorator | Key behaviour |
|---------|-----------------|---------------|
| `harness` | `@cli.group()` | Group root, no-op entry |
| `harness init` | `@harness.command("init")` | Copy scripts + templates into `<project>/.harness/`; create state dirs; prompt for MCP URLs and verify commands; write config.yaml |
| `harness status` | `@harness.command("status")` | Load feature_list.json; print counts by status; check MCP endpoints from config.yaml using curl-like probe |
| `harness task` | `@harness.group("task")` | Sub-group |
| `harness task list` | `@harness_task.command("list")` | Print aligned table of all tasks |
| `harness task add` | `@harness_task.command("add")` | Prompt for fields; append to feature_list.json |
| `harness task show` | `@harness_task.command("show")` | Pretty-print one task as JSON |
| `harness run` | `@harness.command("run")` | `subprocess.run(["python3", orchestrator.py, ...])` in `project_dir`; streams stdout |
| `harness context` | `@harness.command("context")` | `subprocess.run(["python3", context_selector.py, ...])` |
| `harness verify` | `@harness.command("verify")` | `subprocess.run(["bash", verify.sh])` in `project_dir` |

---

## Implementation notes

- `harness init` copies scripts with `shutil.copy2` to preserve permissions (+x on sh files).
- `harness init` prompts for: graph_mcp_url, mind_mcp_url, verify test_cmd, verify lint_cmd.
- `harness run` delegates fully to orchestrator.py — no session state in dev.py.
- `harness context` defaults output to stdout (`--output -`).
- MCP health check in `harness status` reuses the same HTTP probe logic as `init.sh` but inline via urllib.
- Task ID format: `task-001`, `task-002`, ... (zero-padded to 3 digits).
