### Objective

* Provide two CLI commands to manage and integrate MCP for `doc-tiny` and `code-tiny`.

### Commands

#### `dev mcp start`

* **Description**: Launch MCP for both `code-tiny` and `doc-tiny` components by invoking existing startup scripts.
* **Scripts**:
* `code-tiny/mcp.sh`
* `doc-tiny/mcp.sh`


* **Behavior**:
* Verify that scripts exist and are executable.
* If MCP is already running, display the status (PID, port, uptime) for each component.
* If not running, call the respective scripts to start; support the `--detached` option for background execution.
* Report detailed errors if startup fails (exit code, stdout/stderr).


* **Proposed Options**:
* `--detached`: Run in the background (adjusted based on existing scripts).
* `--force-restart`: Stop and then restart the services if they are already running.



#### `dev mcp integrate [--scope <global|workspace>] [--agent <name|all>]`

* **Description**: Register or link MCP endpoints into agent configurations (e.g., Claude, VS Code, Cursor).
* **Scope**:
* `global`: Update system-wide configuration (e.g., global config files or shared environment variables).
* `workspace`: Update configuration only for the current workspace (repo-specific config files).


* **Agent Targets**: `claude`, `vscode`, `cursor`, `all`.
* **Behavior**:
1. Check for default MCP endpoints (e.g., `localhost:8788/8789` or via environment variables).
2. Generate the corresponding configuration/patch for the specified agent:
* Example: Add the MCP endpoint to the agent's config or write a `.mcp-config.json` file in the workspace.


3. If `--scope global` is used, print instructions or automatically update system config files if permissions allow.
4. Display a summary of changes and rollback steps (if applicable).



---

### Technical Implementation Details

#### `dev mcp start` implementation sketch:

1. Resolve full paths for `code-tiny/mcp.sh` and `doc-tiny/mcp.sh`.
2. For each script:
* If not executable: Fail with the instruction `chmod +x`.
* If `--force-restart`: Find the PID (`ps`/`pgrep`) and stop it cleanly.
* Start: Run the script; capture the PID and basic status (port, logs path if any).


3. (Optional) Output structured status JSON for consumption by other tools.

#### `dev mcp integrate` implementation sketch:

1. Discover MCP endpoints (either from running services or configuration variables).
2. For each target agent:
* Locate agent configuration file(s) to modify (e.g., global agent registry, workspace `.dev/agent-config.json`).
* Create a backup before modification.
* Insert/update MCP endpoint entries:
```json
{
    "servers": {
        "code-tiny": {
            "type": "http",
            "url": "http://127.0.0.1:8788/mcp"
        },
        "doc-tiny": {
            "type": "http",
            "url": "http://127.0.0.1:8789/mcp"
        }
    }
}

```


* Validate the configuration (basic JSON/YAML lint) after modification.


3. Print performed actions and next steps (e.g., restart agent if necessary).

---

### UX & Safety

* Always backup configuration files before editing.
* When running `dev mcp integrate --scope global`, warn the user about sudo requirements or system-wide impacts.
* If automatic updates are impossible (e.g., proprietary agent configs), print precise instructions for the user to paste manually.

### Examples

* **Start MCP (Interactive):**
`dev mcp start`
* **Start MCP detached and force restart:**
`dev mcp start --detached --force-restart`
* **Integrate MCP globally for all agents:**
`dev mcp integrate --scope global --agent all`
* **Integrate MCP for workspace-only for VS Code:**
`dev mcp integrate --scope workspace --agent vscode`

### Implementation Notes

* Before deployment, ensure `code-tiny/mcp.sh` and `doc-tiny/mcp.sh` function correctly when run independently from the shell.
* Integration rules may vary by agent; it is better to implement a small plugin mechanism to support agent-specific adapters.
* Log detailed activity to `./.cache/dev-mcp.log` for debugging.

---