# AGENT WORKING CONTRACT

CortexHarness — cognition-aware context orchestration for AI coding agents.
Uses Graph (Neo4j) + Vector (Qdrant) to build persistent contextual memory,
served via MCP to this agent through `code-tiny` (port 8788) and `doc-tiny` (port 8789).

## Startup Workflow

Before writing any code:

1. **Confirm working directory** with `pwd`
2. **Read this file** completely
3. **Check `.harness/` exists** — if not, run `dev harness init` to bootstrap
4. **Run `.harness/scripts/init.sh`** to verify MCP endpoints are reachable
5. **Read `.harness/state/feature_list.json`** to see the task backlog
6. **Pick one `todo` task** — work on the highest-priority item only
7. **Review recent commits** with `git log --oneline -5`
8. **Build context** with `dev harness context <task-id>` before editing

If MCP endpoints are unreachable → run `dev mcp start` first.
If baseline verification is failing → repair that before adding new scope.

## Working Rules

- **One task at a time**: Pick exactly one `todo` task from `feature_list.json`
- **Graph-first scope**: Use `graph_entry_node` to anchor context before editing
- **Verify before done**: Never mark task `done` without running `dev harness verify`
- **Log scope violations**: If you edit a file outside `related_files`, note it in progress.md
- **Leave clean state**: Next session must run `.harness/scripts/init.sh` without errors

## Task Lifecycle

```
todo → in_progress → done
                  ↘ blocked  (if verify fails after max_rounds)
```

Update `status` in `.harness/state/feature_list.json` as you work.
The orchestrator manages `session_id` automatically — do not set it manually.

## Required Artifacts

| File | Purpose |
|------|---------|
| `.harness/config.yaml` | MCP endpoints, budget limits, verify commands |
| `.harness/state/feature_list.json` | Task backlog (source of truth) |
| `.harness/state/progress.md` | Session continuity log |
| `.harness/scripts/init.sh` | MCP health check + env validation |
| `.harness/scripts/verify.sh` | Critical test / lint / type gate |
| `.harness/scripts/context_selector.py` | Graph + vector context builder |
| `.harness/scripts/orchestrator.py` | Session lifecycle manager |

## Definition of Done

A task is done **only when ALL of the following are true**:

- [ ] Implementation complete and scoped to `related_files` / `related_modules`
- [ ] `dev harness verify` exits 0 (all critical checks pass)
- [ ] Task `status` set to `done` in `feature_list.json`
- [ ] Session log written to `.harness/state/session_log/`
- [ ] Repository restartable from `.harness/scripts/init.sh` without errors

## End of Session

Before ending a session:

1. Update `.harness/state/progress.md` with current state and blockers
2. Update task `status` in `feature_list.json`
3. Record unresolved risks in `notes` field of the task
4. Commit with a descriptive message
5. Leave repo clean so `dev harness run` can restart immediately

## CortexHarness CLI Commands

```bash
# Data pipeline — keep Neo4j + Qdrant fresh
dev init .                  # (re-)configure project in current directory
dev sync code               # incremental code sync (git diff → mtime)
dev sync doc                # incremental doc sync (hash → git diff)
dev mcp start               # start code-tiny :8788 + doc-tiny :8789
dev mcp add                 # register servers in .mcp.json

# Agent harness — task management
dev harness init            # bootstrap .harness/ in current project
dev harness task list       # view backlog
dev harness task add        # add task interactively
dev harness task show <id>  # inspect full task JSON
dev harness status          # backlog summary + MCP health
dev harness context <id>    # build context for a task (queries graph + vector)
dev harness verify          # run verify gate (reads from .harness/config.yaml)
dev harness run             # run orchestrator on next todo task
dev harness run --task-id task-002  # run a specific task
```

## Escalation

| Situation | Action |
|-----------|--------|
| MCP unreachable | Run `dev mcp start`, then re-run `init.sh` |
| Architecture decision | Consult project docs or ask user |
| Unclear requirements | Re-read task `notes` in `feature_list.json` or ask user |
| Verify fails after max_rounds | Set task to `blocked`, update `notes`, stop session |
| Scope ambiguity | Use `graph_entry_node` + `related_modules` as the boundary |
| `dev sync` fails | Check Neo4j and Qdrant are running: `dev harness status` |
