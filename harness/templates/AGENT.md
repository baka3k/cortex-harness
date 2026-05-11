# AGENT WORKING CONTRACT

This file defines mandatory behavior for the harness agent.

## Scope
- One session handles one task.
- Use graph-first scope selection before editing.
- Prefer changes inside task subgraph and related files.
- If editing outside whitelist, emit warning and log the violation.

## Required Session Flow
1. Read task from `.harness/state/feature_list.json`.
2. Run `.harness/scripts/init.sh`.
3. Build context with `.harness/scripts/context_selector.py`.
4. Execute work loop (max rounds from config).
5. Run `.harness/scripts/verify.sh`.
6. Mark `done` only when critical checks pass.
7. Produce patch/diff for human approval.

## Quality Gates
- Never mark task `done` if critical verify checks fail.
- If verify fails after allowed rounds, mark task `blocked`.

## Tool Policy
- Allowed tool groups are controlled by `.harness/config.yaml`.
- Soft whitelist mode: out-of-scope file edits are allowed but must be logged.

## Logging
- Every session must write JSON log to `.harness/state/session_log/`.
- Log includes: task id, rounds, verify results, and scope violations.
