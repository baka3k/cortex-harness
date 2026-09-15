#!/usr/bin/env python3
"""Phase 08 parity gate — web framework overlays (python vs rust).

Dùng chung cho 5 overlay: fastapi_django, express_js, laravel,
aspnet_framework, aspnet_core (xem analyzer_parity_<key>.py).

Mỗi overlay:
1. Seed CẢ 2 graph FalkorDB (`p08_<key>_<tag>_py`/`_rs`) bằng base python
   analyzer (prerequisite parser theo FRAMEWORK_ANALYZERS:
   python/js/php/csharp) với journal-shadow env như orchestrator.
2. Dual-run overlay python vs rust trên cùng fixture.
3. So stdout byte-identical (ngoài warning line của graph-cli), dump graph
   diff rỗng ngoài mask chuẩn (dual_write_diff + _start_id/_end_id), và với
   2 overlay ASP.NET: preview/diagnostics output byte-identical.
4. FULL + incremental (changed/deleted manifest + mutation).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_fastapi_django.py
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.journal.config import configure_journal_env  # noqa: E402
import dual_write_diff  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

ENGINE_INTERNAL_PROPS = MASKED_PROPS | {"_start_id", "_end_id"}

PY_BIN = REPO / ".venv" / "bin" / "python"
REPORT_DIR = REPO / "plans" / "260913-2130-rust-full-migration" / "reports"

# Registry mirrors FRAMEWORK_ANALYZERS (code-tiny/tools/sync/incremental_sync.py).
KEYS = {
    "fastapi_django": {
        "overlay": ("py", REPO / "code-tiny" / "tools" / "web_framework" / "web_framework_analyzer.py"),
        "rust_bin": "analyzer-fastapi-django",
        "base_parser": "python",
        "extra_args": ["--framework", "fastapi_django"],
    },
    "express_js": {
        "overlay": ("py", REPO / "code-tiny" / "tools" / "web_framework" / "web_framework_analyzer.py"),
        "rust_bin": "analyzer-express-js",
        "base_parser": "js",
        "extra_args": ["--framework", "express_js"],
    },
    "laravel": {
        "overlay": ("py", REPO / "code-tiny" / "tools" / "web_framework" / "web_framework_analyzer.py"),
        "rust_bin": "analyzer-laravel",
        "base_parser": "php",
        "extra_args": ["--framework", "laravel"],
    },
    "aspnet_framework": {
        "overlay": ("py", REPO / "code-tiny" / "tools" / "aspnet_framework" / "aspnet_framework_analyzer.py"),
        "rust_bin": "analyzer-aspnet-framework",
        "base_parser": "csharp",
        "extra_args": [],
    },
    "aspnet_core": {
        "overlay": ("py", REPO / "code-tiny" / "tools" / "aspnet_core" / "aspnet_core_analyzer.py"),
        "rust_bin": "analyzer-aspnet-core",
        "base_parser": "csharp",
        "extra_args": [],
    },
}

BASE_ANALYZER_SCRIPTS = {
    "python": REPO / "code-tiny" / "tools" / "python" / "python_analyzer.py",
    "js": REPO / "code-tiny" / "tools" / "js" / "js_analyzer.py",
    "php": REPO / "code-tiny" / "tools" / "php" / "php_analyzer.py",
    "csharp": REPO / "code-tiny" / "tools" / "csharp" / "csharp_analyzer.py",
}

ROSLYN_WORKER_PROJECT = (
    REPO / "code-tiny" / "tools" / "common" / "aspnet" / "roslyn_worker" / "AspNetRoslynWorker.csproj"
)

STDOUT_IGNORE_PATTERNS = [
    # warning của ProjectRegistry lookup — chỉ phía python, không thuộc contract.
    re.compile(r"\[graph-cli\] project_id"),
]

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH", "QDRANT_COLLECTION", "FALKORDB_URI", "FALKORDB_GRAPH",
        "PROJECT_ID", "PROJECT_NAME", "PROJECT_LANGUAGE", "PROJECT_REPO",
        "PROJECT_BUILD_SYSTEM", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASS", "NEO4J_DB",
        "LADYBUG_PATH", "LADYBUG_GRAPH", "CORTEX_DISABLE_GRAPH", "QDRANT_CACHE_DIR",
        "ASPNET_ROSLYN_WORKER_PROJECT", "REQUIRE_NEO4J", "FALKORDB_PATH",
        "FALKORDB_PASSWORD", "CORTEX_GRAPH_PROVIDER",
    ]:
        env.pop(key, None)
    return env


def run_base_analyzer(key: str, root: Path, project_id: str, graph: str, host: str, port: int) -> str:
    """Seed 1 graph bằng base python analyzer (journal-shadow như orchestrator)."""
    base_parser = KEYS[key]["base_parser"]
    cmd = [
        str(PY_BIN), str(BASE_ANALYZER_SCRIPTS[base_parser]),
        "--root", str(root),
        "--config", "/dev/null",
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
    ]
    if base_parser == "csharp":
        # Roslyn-first của csharp analyzer sinh Function id dạng dotted scope
        # (không khớp canonical id của overlay); tree-sitter path sinh đúng
        # `namespace::Type::member/N@rel` như overlay anchors kỳ vọng.
        cmd.append("--disable-roslyn")
    env = analyzer_env()
    scratch = REPO / ".cache" / f"p08_{key}_parity_journal"
    scratch.mkdir(parents=True, exist_ok=True)
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser=base_parser,
        source_revision=f"parity-{graph}",
        source_snapshot=f"parity-{graph}",
        physical_target=f"falkordb:{host}:{port}/{graph}",
        cache_dir=str(scratch),
        mode=env.get("CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"),
        generation=f"parity-{graph}",
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"base {base_parser} analyzer failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def overlay_cmd(key: str, side: str, root: Path, project_id: str, graph: str,
                host: str, port: int, incremental: tuple[Path, Path] | None,
                tmp_dir: Path) -> list[str]:
    spec = KEYS[key]
    if side == "py":
        _, script = spec["overlay"]
        cmd = [str(PY_BIN), str(script)]
    else:
        cmd = [str(REPO / "rust" / "target" / "release" / spec["rust_bin"])]
    cmd.extend([
        "--root", str(root),
        "--project-id", project_id,
        "--project-name", project_id,
        "--commit-sha-before", "aaa",
        "--commit-sha-after", "bbb",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--ignore-cache",
        "--verbose",
    ])
    cmd.extend(spec["extra_args"])
    if key.startswith("aspnet"):
        # add_shared_arguments định nghĩa --qdrant-collection/--device — truyền
        # để chứng minh 2 backend cùng accept-and-ignore.
        flag = "--aspnet-core-preview-output" if key == "aspnet_core" else "--aspnet-framework-preview-output"
        cmd.extend([
            "--qdrant-collection", "unused-collection",
            "--device", "auto",
            "--roslyn-worker-project", str(ROSLYN_WORKER_PROJECT),
            flag, str(tmp_dir / f"preview_{side}.json"),
            "--diagnostics-output", str(tmp_dir / f"diagnostics_{side}.json"),
        ])
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    return cmd


def run_overlay(key: str, side: str, root: Path, project_id: str, graph: str,
                host: str, port: int, incremental: tuple[Path, Path] | None,
                tmp_dir: Path) -> str:
    cmd = overlay_cmd(key, side, root, project_id, graph, host, port, incremental, tmp_dir)
    env = analyzer_env()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{side} overlay failed ({proc.returncode}):\n"
            f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
        )
    return proc.stdout


def normalize_stdout(log: str) -> str:
    lines = [line for line in log.splitlines() if not any(p.search(line) for p in STDOUT_IGNORE_PATTERNS)]
    return "\n".join(lines)


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def dump_graph_masked(driver: FalkorDBDriver, graph: str) -> dict:
    dump = dump_graph(driver, graph)

    def strip(props: dict) -> dict:
        return {k: v for k, v in props.items() if k not in ENGINE_INTERNAL_PROPS}

    return {
        "nodes": {k: strip(v) for k, v in dump["nodes"].items()},
        "edges": {k: strip(v) for k, v in dump["edges"].items()},
    }


def summarize_stdout(log: str, key: str) -> str:
    marker = "[overlay]" if key in {"fastapi_django", "express_js", "laravel"} else f"[{key}]"
    lines = [line for line in log.splitlines() if line.startswith(marker)]
    return " | ".join(lines)


def dual(key: str, driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], incremental: tuple[Path, Path] | None = None) -> None:
    project_id = f"p08_{key.replace('_', '')}"
    graph_py = f"p08_{key}_{tag}_py"
    graph_rs = f"p08_{key}_{tag}_rs"
    clean_graph(driver, graph_py)
    clean_graph(driver, graph_rs)
    run_base_analyzer(key, root, project_id, graph_py, host, port)
    run_base_analyzer(key, root, project_id, graph_rs, host, port)
    with tempfile.TemporaryDirectory(prefix=f"p08_{key}_{tag}_") as tmp:
        tmp_dir = Path(tmp)
        py_log = run_overlay(key, "py", root, project_id, graph_py, host, port, incremental, tmp_dir)
        rs_log = run_overlay(key, "rs", root, project_id, graph_rs, host, port, incremental, tmp_dir)

    py_norm, rs_norm = normalize_stdout(py_log), normalize_stdout(rs_log)
    byte_identical = py_norm == rs_norm
    check(f"{tag}: stdout byte-identical", byte_identical,
          f"py={summarize_stdout(py_log, key)!r} rust={summarize_stdout(rs_log, key)!r}")
    report.append(f"\n### {tag}\n\n- stdout py: `{summarize_stdout(py_log, key)}`\n"
                  f"- stdout rust: `{summarize_stdout(rs_log, key)}`\n"
                  f"- byte-identical: **{byte_identical}**\n")

    py_dump = dump_graph_masked(driver, graph_py)
    rs_dump = dump_graph_masked(driver, graph_rs)
    diff = diff_dump(py_dump, rs_dump)
    diff_total = sum(len(v) for k, v in diff.items() if not k.startswith("_"))
    check(f"{tag}: graph diff rỗng ngoài mask", diff_total == 0,
          f"nodes py={len(py_dump['nodes'])} rust={len(rs_dump['nodes'])} diff={diff_total}")
    report.append(
        f"- nodes: py={len(py_dump['nodes'])} rust={len(rs_dump['nodes'])}\n"
        f"- edges: py={len(py_dump['edges'])} rust={len(rs_dump['edges'])}\n"
        f"- diff_total: **{diff_total}**\n"
    )
    if diff_total:
        report.append("```json\n")
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")


def preview_compare(key: str, driver: FalkorDBDriver, root: Path, tag: str,
                    host: str, port: int, report: list[str]) -> None:
    """Gate bổ sung cho 2 overlay ASP.NET: preview/diagnostics output.

    Preview/diagnostics PHẢI byte-identical; ngoại lệ duy nhất: thứ tự MẢNG
    `diagnostics` được chuẩn hoá (sort theo canonical dump từng phần tử) vì
    roslyn worker trả compilation diagnostics KHÔNG ổn định thứ tự giữa các
    process — chính phía python cũng không tự ảnh (đã verify py-vs-py).
    """
    project_id = f"p08_{key.replace('_', '')}"
    graph_py = f"p08_{key}_{tag}_py"
    graph_rs = f"p08_{key}_{tag}_rs"

    def canonical(text: str) -> str:
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("diagnostics"), list):
            entries = [json.dumps(item, sort_keys=True) for item in payload["diagnostics"]]
            payload["diagnostics"] = sorted(entries)
        elif isinstance(payload, list):
            payload = sorted(json.dumps(item, sort_keys=True) for item in payload)
        return json.dumps(payload, sort_keys=True, indent=2)

    with tempfile.TemporaryDirectory(prefix=f"p08_{key}_pv_{tag}_") as tmp:
        tmp_dir = Path(tmp)
        run_overlay(key, "py", root, project_id, graph_py, host, port, None, tmp_dir)
        run_overlay(key, "rs", root, project_id, graph_rs, host, port, None, tmp_dir)
        for name in ("preview_{}.json", "diagnostics_{}.json"):
            py_text = canonical((tmp_dir / name.format("py")).read_text(encoding="utf-8"))
            rs_text = canonical((tmp_dir / name.format("rs")).read_text(encoding="utf-8"))
            label = "preview" if name.startswith("preview") else "diagnostics"
            ok = py_text == rs_text
            check(f"{tag}: {label} output identical (diagnostics order normalized)", ok,
                  f"py {len(py_text)}B vs rs {len(rs_text)}B")
            report.append(f"- {label} identical (diagnostics order normalized): **{ok}**\n")


def scenario_incremental_web(key: str, driver: FalkorDBDriver, workdir: Path,
                             host: str, port: int, report: list[str]) -> None:
    """Mutation cho 3 web overlay: thêm route mới + xoá file có endpoint."""
    if key == "fastapi_django":
        target = workdir / "main.py"
        target.write_text(target.read_text(encoding="utf-8") + """

@app.get("/extra")
def extra_endpoint():
    return {"extra": True}
""", encoding="utf-8")
        changed = ["main.py", "services.py"]
        deleted_file = workdir / "routers" / "users.py"
        deleted_rel = "routers/users.py"
    elif key == "express_js":
        target = workdir / "app.js"
        target.write_text(target.read_text(encoding="utf-8") + """
app.get('/extra', extraEndpoint);

function extraEndpoint(req, res) {
  res.end();
}
""", encoding="utf-8")
        changed = ["app.js"]
        deleted_file = workdir / "routes" / "admin.js"
        deleted_rel = "routes/admin.js"
    else:  # laravel
        target = workdir / "routes" / "web.php"
        target.write_text(target.read_text(encoding="utf-8")
                          + "Route::get('/extra', [HomeController::class, 'extra']);\n",
                          encoding="utf-8")
        changed = ["routes/web.php"]
        deleted_file = workdir / "app" / "Http" / "Controllers" / "ProxyController.php"
        deleted_rel = "app/Http/Controllers/ProxyController.php"
    deleted_file.unlink()
    changed_manifest = workdir.parent / f"changed_{key}.json"
    deleted_manifest = workdir.parent / f"deleted_{key}.json"
    # web overlay đọc key "paths", orchestrator dùng "files" — ghi CẢ HAI để
    # exercise đúng selection/delete của từng overlay.
    payload = {"files": changed, "paths": changed}
    changed_manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    payload = {"files": [deleted_rel], "paths": [deleted_rel]}
    deleted_manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    dual(key, driver, workdir, "inc_run", host, port, report,
         incremental=(changed_manifest, deleted_manifest))


def scenario_incremental_aspnet(key: str, driver: FalkorDBDriver, workdir: Path,
                                host: str, port: int, report: list[str]) -> None:
    """Mutation cho 2 overlay ASP.NET: sửa 1 file .cs + xoá artifact.

    * aspnet_core: thêm MapGet vào WebApp/Program.cs + xoá nguyên module
      SecondApp (cleanup module + empty generation).
    * aspnet_framework: thêm MapRoute vào RouteConfig.cs + xoá 1 resx trong
      live module (deleted_artifact diagnostics).
    """
    if key == "aspnet_core":
        program = workdir / "WebApp" / "Program.cs"
        program.write_text(program.read_text(encoding="utf-8") + 'app.MapGet("/after", () => 1);\n',
                           encoding="utf-8")
        shutil.rmtree(workdir / "SecondApp")
        changed_manifest = workdir.parent / f"changed_{key}.json"
        deleted_manifest = workdir.parent / f"deleted_{key}.json"
        changed_manifest.write_text(json.dumps({"files": ["WebApp/Program.cs"]}) + "\n", encoding="utf-8")
        deleted_manifest.write_text(json.dumps({"files": [
            "SecondApp/SecondApp.csproj", "SecondApp/Program.cs", "SecondApp/appsettings.json",
        ]}) + "\n", encoding="utf-8")
    else:
        routes = workdir / "LegacyWeb" / "App_Start" / "RouteConfig.cs"
        routes.write_text(
            routes.read_text(encoding="utf-8").replace(
                'routes.IgnoreRoute("{resource}.axd/{*pathInfo}");',
                'routes.MapRoute("Extra", "extra/{action}");\n'
                '            routes.IgnoreRoute("{resource}.axd/{*pathInfo}");',
            ),
            encoding="utf-8",
        )
        resx = workdir / "LegacyWeb" / "App_LocalResources" / "Resource.resx"
        resx.unlink()
        changed_manifest = workdir.parent / f"changed_{key}.json"
        deleted_manifest = workdir.parent / f"deleted_{key}.json"
        changed_manifest.write_text(json.dumps({"files": ["LegacyWeb/App_Start/RouteConfig.cs"]}) + "\n", encoding="utf-8")
        deleted_manifest.write_text(json.dumps({"files": ["LegacyWeb/App_LocalResources/Resource.resx"]}) + "\n", encoding="utf-8")
    dual(key, driver, workdir, "inc_run", host, port, report,
         incremental=(changed_manifest, deleted_manifest))


REPORT_NOTES = {
    "fastapi_django": """
## Ghi chú parity (phase 08 — fastapi_django)

- **Port**: `tools/web_framework/` (109 + 198 + models 77 LOC) → `web::pipeline`
  + `web::models` + `web::writer`; regex `_FASTAPI_RE`/`_DJANGO_RE` port với
  `(?is)`/`(?m)` đúng flags Python. `stable_id` = `web::` + sha256[:32] của
  `"\\x1f".join(str(p).strip())`.
- **Semantic engine**: KHÔNG dùng `SemanticInferenceEngine` (overlay thuần regex
  + symbol index); handler resolution từ symbol index tự scan (def/class/function).
- **Writer**: `WebFrameworkWriter` qua `GraphStore.execute_query` — MERGE
  `ApiEndpoint {id}` + `SET node += row` + `HANDLES`/`SEMANTIC_OF` có điều kiện
  match handler (name/file_path/scope) như Python; `delete_paths` per framework.
- **Incremental**: manifest đọc key `paths` (dict) hoặc list — ĐÚNG chữ ký
  `_manifest()` của overlay (khác `load_manifest_paths` của orchestrator).
- **project_id**: node/relationship rows đều mang `project_id` tường minh
  (writer contract); Python overlay cũng tự điền nên không cần journal env.
""",
    "express_js": """
## Ghi chú parity (phase 08 — express_js)

- **Port**: giống fastapi_django nhưng `_EXPRESS_RE` (app|router|server|api +
  method set có `all`/`use` → normalized `ALL`) + symbol index JS (function decl
  + arrow `const x = (...) =>`).
- Base seeding: `js_analyzer.py` (prerequisite parser theo FRAMEWORK_ANALYZERS),
  journal-shadow env như orchestrator.
""",
    "laravel": """
## Ghi chú parity (phase 08 — laravel)

- **Port**: `_LARAVEL_RE` (`Route::(get|...|match)(path, [Scope::class, 'method'])`)
  + scope resolution `scope.split("\\\\")[-1]` map vào class PHP của base analyzer.
- Base seeding: `php_analyzer.py` (prerequisite parser), journal-shadow env.
""",
    "aspnet_framework": """
## Ghi chú parity (phase 08 — aspnet_framework)

- **Semantic engine**: overlay GỌI chung ASP.NET Roslyn worker (dotnet,
  `AspNetRoslynWorker.csproj`) qua `aspnet::roslyn` — port 1:1
  `roslyn_adapter.py` (build worker, chọn dll theo mtime + runtime major,
  request manifest JSON). Evidence byte-identical vì cùng worker.
- **Base seeding (csharp)**: chạy `csharp_analyzer.py --disable-roslyn`.
  Lý do: roslyn-first của base sinh Function id dotted-scope
  (`Ns.Type::method/N@rel`) trong khi overlay anchors dùng canonical
  `Ns::Type::method/N@rel`; tree-sitter path sinh đúng định dạng anchors.
- **Arity anchors**: `_count_parameters` phía base TS đếm 0 cho mọi method
  (field name `parameter_list` không khớp grammar c_sharp) — fixture giữ
  các member trở thành overlay-fact (Application_Start…) không tham số để
  anchor khớp; khác số tham số → SEMANTIC_OF rơi ra ngoài MATCH →
  `stage_generation` count mismatch ở CẢ HAI phía (đặc thù dự án, không phải
  divergences của port).
- **Detection**: `detect_modules` port đúng prune `IGNORED_DIRS`, chặn descend
  vào dir chứa .csproj/.vbproj, evidence strong/supporting (system.web,
  legacy-target, system-web-config, legacy-web-artifact, app-start,
  packages-config).
- **`connect_request_pipeline`**: PASSES_THROUGH (endpoint × module position) +
  HANDLED_BY (constant route target / single candidate fallback) — sort keys
  khớp Python (stable_id / (position, file, line)).
- **Staged writer**: `AspNetFactWriter` (stage → checksum sha256 của
  `json.dumps({facts, relationships, coverage}, sort_keys, compact)` →
  generation_id = stable_digest(parser_version, module, checksum) → promote →
  cleanup) + preserve-complete logic của `apply_graph`.
- **Preview/diagnostics**: `AnalysisResult.to_json()` (sort_keys, indent=2,
  ensure_ascii) so byte-identical; RIÊNG mảng `diagnostics` được chuẩn hoá thứ
  tự (worker trả compilation diagnostics không ổn định thứ tự giữa 2 process —
  py-vs-py cũng khác, đã verify trong report).
""",
    "aspnet_core": """
## Ghi chú parity (phase 08 — aspnet_core)

- **Semantic engine**: như aspnet_framework — dùng chung roslyn worker;
  `resolve_roslyn_evidence` port đủ kinds (Controller/RazorPage/Repository/
  Service/Model, Action/PageHandler, Middleware, minimal API endpoints/routes,
  Add* services, GetSection/GetValue/GetConnectionString config, PASSES_THROUGH
  pipeline, attribute routes).
- **Artifact parsers**: `parse_razor` (@page/@model/Layout/partial +
  PartialAsync), `parse_appsettings` (flatten_json với prefix ":", environment
  từ tên file, duplicate-key diagnostics) — đỏm SENSITIVE_KEY_RE redaction
  ("[REDACTED]" cho ConnectionStrings…) và `_CONNECTION_SECRET_RE`
  (`Password=[REDACTED]`). // sensitive-guard:allow (flag name / test sample)
- **Base seeding (csharp)**: `--disable-roslyn` — xem giải thích ở report
  aspnet_framework.
- **Deleted module cleanup**: incremental xoá module SecondApp — detect_path →
  infer_deleted_module_path → module rỗng `evidence=[path:deleted]` → empty
  generation cleanup; `live_module_ids` cập nhật trong loop (tránh trùng
  cleanup module) khớp Python.
- **Redaction**: preview/diagnostics output byte-identical với redact_value
  chạy trên toàn `asdict(result)` như `to_dict()`.
""",
}


def run_key(key: str, host: str, port: int, rust_bin: Path) -> int:
    fixture = REPO / "tests" / "fixtures" / "web-overlays" / key
    report = [
        f"# Phase 08 — {key} overlay parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- fixture: `{fixture.relative_to(REPO)}`",
        f"- rust bin: `{rust_bin}`",
        f"- base parser (prerequisite): `{KEYS[key]['base_parser']}` (python, journal-shadow)",
        f"- mask: `{sorted(MASKED_PROPS)}` + `_start_id`/`_end_id`",
    ]
    driver = FalkorDBDriver(host=host, port=port)

    dual(key, driver, fixture, "testdata_full", host, port, report)
    if key.startswith("aspnet"):
        preview_compare(key, driver, fixture, "testdata_full", host, port, report)

    with tempfile.TemporaryDirectory(prefix=f"p08_{key}_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(fixture, workdir)
        if key in {"fastapi_django", "express_js", "laravel"}:
            scenario_incremental_web(key, driver, workdir, host, port, report)
        else:
            scenario_incremental_aspnet(key, driver, workdir, host, port, report)

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    report.append(REPORT_NOTES.get(key, ""))
    report_path = REPORT_DIR / f"phase08-{key}-parity.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nreport → {report_path.relative_to(REPO)}")
    if FAILURES:
        print(f"FAILED: {FAILURES}")
        return 1
    print("ALL GATES PASS")
    return 0


def main_for(key: str) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin",
                        default=str(REPO / "rust" / "target" / "release" / KEYS[key]["rust_bin"]))
    args = parser.parse_args()
    return run_key(key, args.host, args.port, Path(args.rust_bin))
