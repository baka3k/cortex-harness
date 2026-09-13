//! Static command tree — a 1:1 transcription of the Click decorators in
//! `cortex_harness/dev.py` (command names, option names/aliases, choices,
//! defaults). Keep in sync with the Python reference; the parity harness
//! enforces it.

use crate::spec::{ArgSpec, Cmd, Opt, OptMeta};

const HELP_OPT: Opt = Opt {
    names: &["--help"],
    meta: OptMeta::Flag,
    required: false,
    multiple: false,
    default: None,
    default_dynamic: false,
    help: "Show this message and exit.",
};

macro_rules! opt {
    ($names:expr, $meta:expr, help=$help:expr) => {
        Opt {
            names: $names,
            meta: $meta,
            required: false,
            multiple: false,
            default: None,
            default_dynamic: false,
            help: $help,
        }
    };
    ($names:expr, $meta:expr, default=$default:expr, help=$help:expr) => {
        Opt {
            names: $names,
            meta: $meta,
            required: false,
            multiple: false,
            default: Some($default),
            default_dynamic: false,
            help: $help,
        }
    };
    ($names:expr, $meta:expr, required=$required:expr, help=$help:expr) => {
        Opt {
            names: $names,
            meta: $meta,
            required: $required,
            multiple: false,
            default: None,
            default_dynamic: false,
            help: $help,
        }
    };
}

// ---------------------------------------------------------------------------
// storage-migrate-layout
// ---------------------------------------------------------------------------

const OPT_LEGACY_ROOT: Opt = Opt {
    names: &["--legacy-root"],
    meta: OptMeta::Value("PATH"),
    required: false,
    multiple: false,
    default: None, // rendered dynamically: the repo root (Click show_default)
    default_dynamic: true,
    help: "",
};

// ---------------------------------------------------------------------------
// installer build
// ---------------------------------------------------------------------------

const OPT_PLATFORM: Opt = Opt {
    names: &["--platform"],
    meta: OptMeta::Choice(&["windows", "macos", "ubuntu", "all"]),
    required: false,
    multiple: true,
    default: Some("all"),
    default_dynamic: false,
    help: "",
};

static INSTALLER_BUILD_ARGS: &[ArgSpec] = &[];

static CMD_INSTALLER_BUILD: Cmd = Cmd {
    name: "build",
    desc: "Build platform-specific installers for context menu integration.\n\n\nBuilds installers for the specified platforms:\n  windows    -> Inno Setup .exe installer\n  macos      -> pkgbuild .pkg installer\n  ubuntu     -> Debian .deb package\n  all        -> All three platforms",
    opts: &[
        OPT_PLATFORM,
        opt!(&["--output-dir"], OptMeta::Value("PATH"), default = "dist", help = ""),
        HELP_OPT,
    ],
    args: INSTALLER_BUILD_ARGS,
    subs: &[],
    runs_without_sub: false,
};

static CMD_INSTALLER_INSTALL: Cmd = Cmd {
    name: "install",
    desc: "Install context menu integration for the current platform.\n\n\nDevelopment mode (--local):\n  Windows   -> Registry entries for current user\n  macOS     -> Services in ~/Library\n  Ubuntu    -> Nautilus scripts in home directory\n\nSystem-wide mode (default, requires admin/sudo):\n  Windows   -> Registry entries + Program Files installation\n  macOS     -> Services in /Library\n  Ubuntu    -> Nautilus scripts in /usr/share",
    opts: &[
        opt!(&["--local"], OptMeta::Flag, help = "Install context menu for current user only (development mode)"),
        opt!(&["--project-dir"], OptMeta::Value("PATH"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_INSTALLER_UNINSTALL: Cmd = Cmd {
    name: "uninstall",
    desc: "Remove context menu integration for the current platform.",
    opts: &[
        opt!(&["--local"], OptMeta::Flag, help = "Uninstall local user installation only"),
        opt!(&["--project-dir"], OptMeta::Value("PATH"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_INSTALLER: Cmd = Cmd {
    name: "installer",
    desc: "Build and manage context menu installers for Windows, macOS, and Ubuntu.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[
        &CMD_INSTALLER_BUILD,
        &CMD_INSTALLER_INSTALL,
        &CMD_INSTALLER_UNINSTALL,
    ],
    runs_without_sub: false,
};

// ---------------------------------------------------------------------------
// harness
// ---------------------------------------------------------------------------

static CMD_HARNESS_TASK_LIST: Cmd = Cmd {
    name: "list",
    desc: "List all tasks in the backlog.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_TASK_ADD: Cmd = Cmd {
    name: "add",
    desc: "Add a new task to the backlog interactively.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_TASK_SHOW: Cmd = Cmd {
    name: "show",
    desc: "Show full details for a task.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[ArgSpec { metavar: "TASK_ID", required: true, variadic: false }],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_TASK: Cmd = Cmd {
    name: "task",
    desc: "Manage tasks in the harness backlog.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[&CMD_HARNESS_TASK_LIST, &CMD_HARNESS_TASK_ADD, &CMD_HARNESS_TASK_SHOW],
    runs_without_sub: false,
};

static CMD_HARNESS_INIT: Cmd = Cmd {
    name: "init",
    desc: "Bootstrap .harness/ structure in a target project.\n\n\nCopies scripts and templates from the cortex-harness repo,\ncreates state directories, and writes config.yaml.\nExisting files are NOT overwritten.",
    opts: &[
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = "Target project root to bootstrap .harness/ in."),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_STATUS: Cmd = Cmd {
    name: "status",
    desc: "Show task backlog summary and MCP endpoint health.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_RUN: Cmd = Cmd {
    name: "run",
    desc: "Run an orchestrator session for the next todo task (or --task-id).\n\n\nDelegates entirely to .harness/scripts/orchestrator.py.\nSession log is written to .harness/state/session_log/<session-id>.json.",
    opts: &[
        opt!(&["--task-id"], OptMeta::Value("TEXT"), help = "Run a specific task ID (default: next todo)."),
        opt!(&["--max-rounds"], OptMeta::Value("INTEGER"), help = "Override max_rounds from config."),
        opt!(&["--agent-command"], OptMeta::Value("TEXT"), default = "", help = "Shell command that invokes the agent (if used as sub-process)."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_CONTEXT: Cmd = Cmd {
    name: "context",
    desc: "Run context_selector.py for a task and print the result.\n\n\nQueries graph_mcp + mind_mcp using settings from .harness/config.yaml.",
    opts: &[
        opt!(&["--output"], OptMeta::Value("TEXT"), default = "-", help = "Output file path, or '-' for stdout."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[ArgSpec { metavar: "TASK_ID", required: true, variadic: false }],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS_VERIFY: Cmd = Cmd {
    name: "verify",
    desc: "Run the verify gate (.harness/scripts/verify.sh).\n\n\nReads CRITICAL_TEST_CMD / CRITICAL_LINT_CMD / CRITICAL_TYPE_CMD\nfrom .harness/config.yaml and exports them before invoking verify.sh.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HARNESS: Cmd = Cmd {
    name: "harness",
    desc: "Manage AI agent task sessions (harness orchestration layer).\n\n\nBootstrap:\n  dev harness init          Set up .harness/ in a project\nTask management:\n  dev harness task list     List all tasks\n  dev harness task add      Add a new task\n  dev harness task show     Show full task details\nExecution:\n  dev harness run           Run orchestrator session\n  dev harness context       Select context for a task\n  dev harness verify        Run verify gate\n  dev harness status        Show backlog summary + MCP health",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[
        &CMD_HARNESS_CONTEXT,
        &CMD_HARNESS_INIT,
        &CMD_HARNESS_RUN,
        &CMD_HARNESS_STATUS,
        &CMD_HARNESS_TASK,
        &CMD_HARNESS_VERIFY,
    ],
    runs_without_sub: false,
};

// ---------------------------------------------------------------------------
// mcp
// ---------------------------------------------------------------------------

static CMD_MCP_START: Cmd = Cmd {
    name: "start",
    desc: "Start MCP servers for code-tiny (port 8788) and doc-tiny (port 8789).\n\n\nBoth servers run in the background (non-blocking).\nIf already running, displays PID and uptime — use --force-restart to reload.\nLogs: .cache/dev-mcp-<name>.log",
    opts: &[
        opt!(&["--force-restart"], OptMeta::Flag, help = "Kill existing instances before starting."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = "Project root containing .cortext-harness/config."),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_MCP_ADD: Cmd = Cmd {
    name: "add",
    desc: "Register MCP endpoints into agent configurations.\n\n\nScope workspace  → writes .mcp.json in the project root (Claude Code picks this up).\nScope global     → patches system-wide agent config files (Claude Desktop, VS Code, Cursor…).\nConfig files are backed up before modification.",
    opts: &[
        opt!(&["--scope"], OptMeta::Choice(&["global", "workspace"]), default = "workspace", help = "'workspace' writes .mcp.json; 'global' updates agent config files."),
        opt!(&["--agent"], OptMeta::Value("TEXT"), default = "all", help = "Target: claude / claude-code / vscode / cursor / all"),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_MCP: Cmd = Cmd {
    name: "mcp",
    desc: "Start and integrate MCP servers (code-tiny + doc-tiny).",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[&CMD_MCP_START, &CMD_MCP_ADD],
    runs_without_sub: false,
};

// ---------------------------------------------------------------------------
// ignore
// ---------------------------------------------------------------------------

static IGNORE_ARGS: &[ArgSpec] = &[ArgSpec {
    metavar: "<FOLDER>...",
    required: true,
    variadic: true,
}];

static CMD_IGNORE_ADD: Cmd = Cmd {
    name: "add",
    desc: "Add ignore folders to the active environment config.\n\n\nExample:\n  dev ignore add legacy generated-*",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: IGNORE_ARGS,
    subs: &[],
    runs_without_sub: false,
};

static CMD_IGNORE_REMOVE: Cmd = Cmd {
    name: "remove",
    desc: "Remove exact-match entries from the ignore list.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: IGNORE_ARGS,
    subs: &[],
    runs_without_sub: false,
};

static CMD_IGNORE_LIST: Cmd = Cmd {
    name: "list",
    desc: "Print the configured ignore folders.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_IGNORE: Cmd = Cmd {
    name: "ignore",
    desc: "Manage user-configured ignore folders (scan-time excludes).\n\n\nEntries are folder names or fnmatch globs (\"generated-*\") matched at any\ndepth below the scan roots. They ADD to the built-in default excludes\n(.venv, node_modules, build, ...) — defaults can never be un-ignored.\nApplies to both 'dev sync code' and 'dev sync doc'.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[&CMD_IGNORE_ADD, &CMD_IGNORE_LIST, &CMD_IGNORE_REMOVE],
    runs_without_sub: false,
};

// ---------------------------------------------------------------------------
// sync code
// ---------------------------------------------------------------------------

static SYNC_ADD_ARGS: &[ArgSpec] = &[ArgSpec {
    metavar: "[FOLDER...]",
    required: false,
    variadic: true,
}];

static CMD_SYNC_CODE_ADD: Cmd = Cmd {
    name: "add",
    desc: "Add a new source project to code.source.projects.\n\n\nFolders can be passed as positional arguments or entered interactively.\nExamples:\n  dev sync code add /path/to/src\n  dev sync code add /path/a /path/b --git https://github.com/org/repo.git",
    opts: &[
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        opt!(&["--git"], OptMeta::Value("TEXT"), help = "Git remote URL (blank = local)."),
        HELP_OPT,
    ],
    args: SYNC_ADD_ARGS,
    subs: &[],
    runs_without_sub: false,
};

static CMD_SYNC_CODE_ALL: Cmd = Cmd {
    name: "all",
    desc: "Run ALL available analyzers on every configured folder.\n\n\n- Every primary language analyzer and detected framework overlay is considered.\n- Framework overlays are detector-gated by incremental_sync.\n- Incremental if a sync baseline exists, full sync on first run.\n- Changed/deleted files are passed via --changed-files-manifest\n  to the analyzer's built-in incremental engine.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_SYNC_CODE_STOP: Cmd = Cmd {
    name: "stop",
    desc: "Stop all running code sync workers and their embedded store process.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static SYNC_CODE_OPTS: &[Opt] = &[
    opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
    opt!(&["--preview"], OptMeta::Flag, help = "Preview changed files before syncing."),
    Opt {
        names: &["--verbose", "--no-verbose"],
        meta: OptMeta::FlagPair(true),
        required: false,
        multiple: false,
        default: Some("verbose"),
        default_dynamic: false,
        help: "",
    },
    opt!(&["--dry-run"], OptMeta::Flag, help = ""),
    opt!(&["--full-scan"], OptMeta::Flag, help = "Force full rescan (ignore git state / baseline)."),
    opt!(&["--sync-mode"], OptMeta::Choice(&["both", "graph", "embedding"]), default = "both", help = "Run graph+topology then embeddings, graph only, or embeddings only."),
    opt!(&["--change-detection"], OptMeta::Choice(&["hybrid", "committed", "hash"]), default = "hybrid", help = "Git+SHA hybrid, committed-only, or full SHA comparison."),
    opt!(&["--parsers"], OptMeta::Value("TEXT"), default = "auto", help = "auto or a comma-separated list of parser names to run."),
    opt!(&["--lock-timeout-seconds"], OptMeta::Value("FLOAT"), default = "10.0", help = ""),
    opt!(&["--reconcile"], OptMeta::Flag, help = "Force full SHA-256 reconciliation."),
    opt!(&["--submodules"], OptMeta::Choice(&["recursive", "ignore", "off"]), default = "recursive", help = ""),
    opt!(&["--parse-quality"], OptMeta::Choice(&["off", "report", "repair"]), default = "report", help = "C/C++ Tree-sitter quality policy: off disables artifacts; report records diagnostics; repair permits same-backend grammar retry."),
    opt!(&["--parse-quality-max-files"], OptMeta::IntMin(1), default = "500", help = ""),
    opt!(&["--parse-quality-wall-seconds"], OptMeta::IntMin(1), default = "900", help = ""),
    Opt {
        names: &["--parse-quality-workers"],
        meta: OptMeta::IntMin(1),
        required: false,
        multiple: false,
        default: None, // computed: max(1, min(4, cpu/2)) — filled dynamically in help
        default_dynamic: true,
        help: "",
    },
    HELP_OPT,
];

static CMD_SYNC_CODE: Cmd = Cmd {
    name: "code",
    desc: "Interactive: pick folders, auto-detect language, incremental if baseline exists.\n\n\nFirst run   -> full sync (no baseline)\nNext runs   -> incremental via analyzer --changed-files-manifest (built-in)\n--full-scan -> force full rescan from scratch on every folder.\n--sync-mode -> both (default), graph, or embedding; specialized modes require --full-scan.\nSub-command:\n  all       Run ALL analyzers on all folders (incremental if baseline exists).",
    opts: SYNC_CODE_OPTS,
    args: &[],
    subs: &[&CMD_SYNC_CODE_ADD, &CMD_SYNC_CODE_ALL, &CMD_SYNC_CODE_STOP],
    runs_without_sub: true,
};

// ---------------------------------------------------------------------------
// sync doc
// ---------------------------------------------------------------------------

static CMD_SYNC_DOC_ADD: Cmd = Cmd {
    name: "add",
    desc: "Add a new doc project to doc.source.projects.\n\n\nFolders can be passed as positional arguments or entered interactively.\nExamples:\n  dev sync doc add /path/to/docs\n  dev sync doc add /path/a /path/b --git https://github.com/org/repo.git",
    opts: &[
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        opt!(&["--git"], OptMeta::Value("TEXT"), help = "Git remote URL (blank = local)."),
        HELP_OPT,
    ],
    args: SYNC_ADD_ARGS,
    subs: &[],
    runs_without_sub: false,
};

static CMD_SYNC_DOC_ALL: Cmd = Cmd {
    name: "all",
    desc: "Full sync for all configured doc folders.\n\n\nPushes every doc file through the doc-tiny pipeline.\nUses incremental if a sync baseline exists, full sync on first run.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_SYNC_DOC_STOP: Cmd = Cmd {
    name: "stop",
    desc: "Stop all running document sync workers and their embedded store process.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_SYNC_DOC: Cmd = Cmd {
    name: "doc",
    desc: "Interactive: pick doc folders, incremental if baseline exists.\n\n\nFirst run   -> full sync (no baseline)\nNext runs   -> incremental (git diff > hash comparison > mtime)\nSub-command:\n  all       Full sync for all configured doc folders.",
    opts: &[
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        opt!(&["--preview"], OptMeta::Flag, help = "Preview changed files before syncing."),
        opt!(&["--entity-provider"], OptMeta::Value("TEXT"), default = "gliner", help = "Entity extraction provider: gliner / langextract / spacy"),
        opt!(&["--dry-run"], OptMeta::Flag, help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[&CMD_SYNC_DOC_ADD, &CMD_SYNC_DOC_ALL, &CMD_SYNC_DOC_STOP],
    runs_without_sub: true,
};

static CMD_SYNC: Cmd = Cmd {
    name: "sync",
    desc: "Sync code or documents into Neo4j + Qdrant.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[&CMD_SYNC_CODE, &CMD_SYNC_DOC],
    runs_without_sub: false,
};

// ---------------------------------------------------------------------------
// journal
// ---------------------------------------------------------------------------

static CMD_JOURNAL_STATUS: Cmd = Cmd {
    name: "status",
    desc: "Show payload-free queue state without contacting the graph.",
    opts: &[
        Opt {
            names: &["--journal-path"],
            meta: OptMeta::Value("FILE"),
            required: true,
            multiple: false,
            default: None,
            default_dynamic: false,
            help: "Exact journal SQLite path printed by a sync summary.",
        },
        opt!(&["--json-output"], OptMeta::Flag, help = "Emit machine-readable JSON."),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_JOURNAL_PURGE: Cmd = Cmd {
    name: "purge",
    desc: "Purge one retained terminal run while holding its scan-scope lock.",
    opts: &[
        Opt {
            names: &["--journal-path"],
            meta: OptMeta::Value("FILE"),
            required: true,
            multiple: false,
            default: None,
            default_dynamic: false,
            help: "Exact journal SQLite path printed by a sync summary.",
        },
        opt!(&["--run-id"], OptMeta::Value("TEXT"), required = true, help = "Exact terminal run identifier."),
        opt!(&["--project-id"], OptMeta::Value("TEXT"), required = true, help = "Project identifier bound to the run."),
        Opt {
            names: &["--root"],
            meta: OptMeta::Value("DIRECTORY"),
            required: true,
            multiple: false,
            default: None,
            default_dynamic: false,
            help: "Exact source root bound to the run scope.",
        },
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_JOURNAL: Cmd = Cmd {
    name: "journal",
    desc: "Inspect or safely purge durable graph-write journals.",
    opts: &[HELP_OPT],
    args: &[],
    subs: &[&CMD_JOURNAL_PURGE, &CMD_JOURNAL_STATUS],
    runs_without_sub: false,
};

// ---------------------------------------------------------------------------
// top-level commands
// ---------------------------------------------------------------------------

macro_rules! simple_cmd {
    ($name:literal, $desc:literal) => {
        Cmd {
            name: $name,
            desc: $desc,
            opts: &[HELP_OPT],
            args: &[],
            subs: &[],
            runs_without_sub: false,
        }
    };
}

static CMD_BUILD: Cmd = simple_cmd!("build", "Create or synchronize the repository virtual environment.");
static CMD_INSTALL: Cmd = simple_cmd!("install", "Build dependencies and install the global dev command.");
static CMD_UNINSTALL: Cmd = simple_cmd!("uninstall", "Remove the global dev command installed by the lifecycle script.");

static CMD_INFRA_UP: Cmd = Cmd {
    name: "infra-up",
    desc: "Initialize local storage and probe remote projects.\n\nLocal projects get their instance tree created (same as ``storage-init``).\nRemote projects (configured via ``storage_backend: remote``) are probed for\nconnectivity; pass ``--provision`` to additionally create the required\nQdrant collections and FalkorDB graphs.",
    opts: &[
        opt!(&["--provision"], OptMeta::Flag, help = "Create collections and graphs on remote Qdrant/FalkorDB servers."),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_INFRA_DOWN: Cmd = simple_cmd!("infra-down", "Close cached remote clients; local storage is left untouched.");
static CMD_STORAGE_INIT: Cmd = simple_cmd!("storage-init", "Initialize local Qdrant and FalkorDBLite storage.\n\nCreates the centralized instance tree and versioned manifest.");
static CMD_STORAGE_LAYOUT: Cmd = simple_cmd!("storage-layout", "Show resolved local paths, manifest, and active owner leases.");

static CMD_STORAGE_MIGRATE_LAYOUT: Cmd = Cmd {
    name: "storage-migrate-layout",
    desc: "Copy the repository-local legacy layout without deleting its source.",
    opts: &[OPT_LEGACY_ROOT, opt!(&["--apply"], OptMeta::Flag, help = "Copy and verify; the default is a dry-run."), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_STORAGE_BACKUP: Cmd = Cmd {
    name: "storage-backup",
    desc: "Create a stopped-owner backup and verify copied content hashes.",
    opts: &[
        opt!(&["--owner"], OptMeta::Choice(&["code", "doc"]), default = "code", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_STORAGE_STOP: Cmd = simple_cmd!("storage-stop", "No-op for parity with the legacy ``infra-down`` command.");

static CMD_START: Cmd = Cmd {
    name: "start",
    desc: "Open default MCPs, or launch a named project-specific instance.",
    opts: &[
        opt!(&["--server"], OptMeta::Choice(&["all", "code", "doc"]), default = "all", help = ""),
        opt!(&["--name"], OptMeta::Value("TEXT"), help = "Named instance used for concurrent start/stop operations."),
        opt!(&["--project"], OptMeta::Value("TEXT"), help = "Project ID; also defaults database and vector collection names."),
        opt!(&["--database", "--db"], OptMeta::Value("TEXT"), help = "Graph database used by every selected MCP server."),
        opt!(&["--code-database"], OptMeta::Value("TEXT"), help = "Graph database override for code-tiny."),
        opt!(&["--doc-database"], OptMeta::Value("TEXT"), help = "Graph database override for doc-tiny."),
        opt!(&["--port"], OptMeta::IntRange(1, 65535), help = "Port for a single selected server."),
        opt!(&["--code-port"], OptMeta::IntRange(1, 65535), help = "code-tiny port."),
        opt!(&["--doc-port"], OptMeta::IntRange(1, 65535), help = "doc-tiny port."),
        opt!(&["--host"], OptMeta::Value("TEXT"), help = "Bind host for selected MCP servers."),
        opt!(&["--path"], OptMeta::Value("TEXT"), help = "Streamable HTTP route."),
        opt!(&["--provider"], OptMeta::Choice(&["falkordb", "neo4j"]), help = "Graph provider override."),
        opt!(&["--collection"], OptMeta::Value("TEXT"), help = "Vector collection used by every selected server."),
        opt!(&["--code-collection"], OptMeta::Value("TEXT"), help = "Vector collection override for code-tiny."),
        opt!(&["--doc-collection"], OptMeta::Value("TEXT"), help = "Vector collection override for doc-tiny."),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_STOP: Cmd = Cmd {
    name: "stop",
    desc: "Stop all MCPs, or stop one named instance.",
    opts: &[opt!(&["--name"], OptMeta::Value("TEXT"), help = "Stop only this named MCP instance."), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_DOCTOR: Cmd = simple_cmd!("doctor", "Check Python 3.12, local storage backends, paths, and MCP ports.");
static CMD_MCP_GATES: Cmd = simple_cmd!("mcp-gates", "Print the active per-instance MCP isolation gate values.\n\nUseful for confirming whether a process is running with the new per-instance\nisolation in effect or the legacy global behavior. See\nplans/260828-1428-instance-isolated-mcp-locks for context.");

static CMD_INIT: Cmd = Cmd {
    name: "init",
    desc: "Create/update config and scaffold project folder structure.\n\nPATH can be passed positionally, e.g. 'dev init .' to use the current directory.",
    opts: &[
        opt!(&["--env"], OptMeta::Choice(&["dev", "prod"]), default = "dev", help = "Environment to configure."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), help = "Target project root directory."),
        HELP_OPT,
    ],
    args: &[ArgSpec { metavar: "[PATH]", required: false, variadic: false }],
    subs: &[],
    runs_without_sub: false,
};

static CMD_STATUS: Cmd = Cmd {
    name: "status",
    desc: "Show active config and all available environments.",
    opts: &[opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""), HELP_OPT],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_HELP: Cmd = simple_cmd!("help", "Show the repository lifecycle command reference.");

static CMD_EXPORT_DB: Cmd = Cmd {
    name: "export-db",
    desc: "Bundle the active project's local Qdrant + FalkorDB files into a .cortexdb archive.",
    opts: &[
        opt!(&["--output"], OptMeta::Value("PATH"), help = "Destination .cortexdb archive path. Defaults to outputs/db/<project_id>-<ts>.cortexdb."),
        opt!(&["--project-id"], OptMeta::Value("TEXT"), help = "Override the project_id embedded in the bundle manifest."),
        opt!(&["--role"], OptMeta::Choice(&["code", "doc", "both"]), default = "both", help = "Which storage lanes to bundle."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_IMPORT_DB: Cmd = Cmd {
    name: "import-db",
    desc: "Restore a .cortexdb archive into the local CORTEX_DATA_HOME tree (local mode only).",
    opts: &[
        Opt {
            names: &["--archive"],
            meta: OptMeta::Value("FILE"),
            required: true,
            multiple: false,
            default: None,
            default_dynamic: false,
            help: "Path to a .cortexdb archive produced by 'dev export-db'.",
        },
        opt!(&["--overwrite"], OptMeta::Flag, help = "Back up and replace any existing destination data instead of refusing."),
        opt!(&["--role"], OptMeta::Choice(&["code", "doc", "both"]), default = "both", help = "Which storage lanes to restore from the archive."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[],
    subs: &[],
    runs_without_sub: false,
};

static CMD_EXPORT: Cmd = Cmd {
    name: "export",
    desc: "Export the active project's local DB (.cortexdb). Reads .cortext-harness/config/dev.json.",
    opts: &[
        opt!(&["--output"], OptMeta::Value("PATH"), help = "Destination .cortexdb archive path. Defaults to outputs/db/<project_id>-<ts>.cortexdb."),
        opt!(&["--project-id"], OptMeta::Value("TEXT"), help = "Override the project_id embedded in the bundle manifest (alternative to the positional arg)."),
        opt!(&["--role"], OptMeta::Choice(&["code", "doc", "both"]), default = "both", help = "Which storage lanes to bundle."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[ArgSpec { metavar: "PROJECT_ID", required: false, variadic: false }],
    subs: &[],
    runs_without_sub: false,
};

static CMD_IMPORT: Cmd = Cmd {
    name: "import",
    desc: "Import a .cortexdb archive into the local CORTEX_DATA_HOME. Reads dev.json for destination targets.",
    opts: &[
        opt!(&["--overwrite"], OptMeta::Flag, help = "Back up and replace any existing destination data instead of refusing."),
        opt!(&["--role"], OptMeta::Choice(&["code", "doc", "both"]), default = "both", help = "Which storage lanes to restore from the archive."),
        opt!(&["--project-dir"], OptMeta::Value("TEXT"), default = ".", help = ""),
        HELP_OPT,
    ],
    args: &[ArgSpec { metavar: "ARCHIVE", required: true, variadic: false }],
    subs: &[],
    runs_without_sub: false,
};

static ROOT_DESC: &str = "dev - CortexHarness ingestion CLI.\n\nQuick start:\n  dev init              # configure project + scaffold folder structure\n  dev status            # show active config\n  dev sync code         # interactive: pick folders, auto incremental/full\n  dev sync code all     # ALL analyzers on all folders (incremental if baseline)\n  dev sync doc          # ingest documents -> Neo4j + Qdrant\n  dev build             # create/sync the repository virtualenv\n  dev storage-init      # initialize centralized local Qdrant/FalkorDBLite storage\n  dev storage-layout    # show instance paths, manifest, and leases\n  dev start             # open code-tiny + doc-tiny from any directory\n  dev stop              # stop MCP processes started by dev/make start\n  dev doctor            # check local storage from any directory";

pub static ROOT: Cmd = Cmd {
    name: "dev",
    desc: ROOT_DESC,
    opts: &[HELP_OPT],
    args: &[],
    subs: &[
        &CMD_BUILD,
        &CMD_DOCTOR,
        &CMD_EXPORT,
        &CMD_EXPORT_DB,
        &CMD_HARNESS,
        &CMD_HELP,
        &CMD_IGNORE,
        &CMD_IMPORT,
        &CMD_IMPORT_DB,
        &CMD_INFRA_DOWN,
        &CMD_INFRA_UP,
        &CMD_INIT,
        &CMD_INSTALL,
        &CMD_INSTALLER,
        &CMD_JOURNAL,
        &CMD_MCP,
        &CMD_MCP_GATES,
        &CMD_START,
        &CMD_STATUS,
        &CMD_STOP,
        &CMD_STORAGE_BACKUP,
        &CMD_STORAGE_INIT,
        &CMD_STORAGE_LAYOUT,
        &CMD_STORAGE_MIGRATE_LAYOUT,
        &CMD_STORAGE_STOP,
        &CMD_SYNC,
        &CMD_UNINSTALL,
    ],
    runs_without_sub: false,
};

/// Every command path in the tree, depth-first (used by tests).
#[cfg(test)]
pub fn all_paths() -> Vec<Vec<&'static str>> {
    fn walk(cmd: &'static Cmd, prefix: &mut Vec<&'static str>, out: &mut Vec<Vec<&'static str>>) {
        out.push(prefix.clone());
        for sub in cmd.subs {
            prefix.push(sub.name);
            walk(sub, prefix, out);
            prefix.pop();
        }
    }
    let mut out = Vec::new();
    walk(&ROOT, &mut Vec::new(), &mut out);
    out
}
