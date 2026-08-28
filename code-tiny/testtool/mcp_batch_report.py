#!/usr/bin/env python3
"""Run a fixed MCP smoke suite and write a full Markdown evidence report.

The suite is data-driven: each server has a fixed endpoint and ordered tool
cases with explicit inputs and expected outcomes.  Every call records the
live schema, exact input, complete parsed JSON-RPC output, timing, protocol
contract validation, and expectation result.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


TESTTOOL_DIR = Path(__file__).resolve().parent
CODE_TINY_DIR = TESTTOOL_DIR.parent
if str(CODE_TINY_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_TINY_DIR))

from testtool.mcp_client import MCPClient  # noqa: E402


CONTRACT_NAME = "cortex.mcp.tool-result"
CONTRACT_VERSION = "1.0"
DEFAULT_SUITE = TESTTOOL_DIR / "suites" / "procsample-all-tools.json"
DEFAULT_OUTPUT_DIR = TESTTOOL_DIR / "outputs"
EXPECTED_STATUSES = {
    "ANY",
    "SUCCESS",
    "SUCCESS_DATA",
    "SUCCESS_EMPTY",
    "TOOL_ERROR",
}
FORBIDDEN_ERROR_DETAIL_KEYS = {
    "accepted_params",
    "available_labels",
    "available_relationships",
    "capability",
    "capability_diagnostics",
    "context",
    "example",
    "next_step",
    "query_engine",
    "received_params",
    "required_params",
    "supported_aliases",
    "supported_parsers",
    "tool",
}
COLLECTION_KEYS = ("results", "items", "nodes", "passages", "ids", "messages")


@dataclass(frozen=True)
class ToolCase:
    tool: str
    arguments: Mapping[str, Any]
    expected_status: str = "SUCCESS"
    expected_error_code: str | None = None

    def __post_init__(self) -> None:
        if not self.tool.strip():
            raise ValueError("tool case name must not be empty")
        if not isinstance(self.arguments, Mapping):
            raise ValueError(f"input for {self.tool!r} must be a JSON object")
        status = self.expected_status.upper()
        if status not in EXPECTED_STATUSES:
            raise ValueError(
                f"unsupported expected_status {self.expected_status!r} for {self.tool!r}"
            )
        object.__setattr__(self, "expected_status", status)
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True)
class ServerConfig:
    name: str
    endpoint: str
    cases: tuple[ToolCase, ...]
    require_full_inventory: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.endpoint.strip():
            raise ValueError("server name and endpoint must not be empty")
        names = [case.tool for case in self.cases]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"duplicate tool case(s) for server {self.name!r}: {duplicates}"
            )


@dataclass(frozen=True)
class SuiteConfig:
    name: str
    project: str
    parser: str
    servers: tuple[ServerConfig, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("suite name must not be empty")
        if not self.servers:
            raise ValueError("suite must define at least one server")
        names = [server.name for server in self.servers]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate server name(s): {duplicates}")


@dataclass(frozen=True)
class BatchRun:
    server: str
    endpoint: str
    case: ToolCase
    description: str
    input_schema: Any
    output_schema: Any
    response: Any
    status: str
    error_code: str | None
    duration_ms: float
    contract_errors: tuple[str, ...]
    expectation_errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            self.status not in {"PROTOCOL_ERROR", "CLIENT_EXCEPTION"}
            and not self.contract_errors
            and not self.expectation_errors
        )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def suite_from_mapping(value: Mapping[str, Any]) -> SuiteConfig:
    root = _require_mapping(value, "suite")
    raw_servers = root.get("servers")
    if not isinstance(raw_servers, list):
        raise ValueError("suite.servers must be a JSON array")

    servers: list[ServerConfig] = []
    for server_index, raw_server in enumerate(raw_servers):
        server = _require_mapping(raw_server, f"suite.servers[{server_index}]")
        raw_cases = server.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError(f"suite.servers[{server_index}].cases must be an array")
        cases: list[ToolCase] = []
        for case_index, raw_case in enumerate(raw_cases):
            case = _require_mapping(
                raw_case, f"suite.servers[{server_index}].cases[{case_index}]"
            )
            arguments = case.get("input", case.get("arguments", {}))
            cases.append(
                ToolCase(
                    tool=str(case.get("tool", "")),
                    arguments=_require_mapping(arguments, f"input for {case.get('tool')!r}"),
                    expected_status=str(case.get("expected_status", "SUCCESS")),
                    expected_error_code=(
                        str(case["expected_error_code"])
                        if case.get("expected_error_code") is not None
                        else None
                    ),
                )
            )
        servers.append(
            ServerConfig(
                name=str(server.get("name", "")),
                endpoint=str(server.get("endpoint", "")),
                cases=tuple(cases),
                require_full_inventory=bool(server.get("require_full_inventory", True)),
            )
        )

    return SuiteConfig(
        name=str(root.get("name", "")),
        project=str(root.get("project", "")),
        parser=str(root.get("parser", "")),
        servers=tuple(servers),
    )


def load_suite(path: Path) -> SuiteConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"invalid suite JSON at {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    return suite_from_mapping(_require_mapping(raw, "suite"))


def _result(response: Any) -> Mapping[str, Any] | None:
    if not isinstance(response, Mapping):
        return None
    result = response.get("result")
    return result if isinstance(result, Mapping) else None


def _structured(response: Any) -> Mapping[str, Any] | None:
    result = _result(response)
    if result is None:
        return None
    structured = result.get("structuredContent")
    return structured if isinstance(structured, Mapping) else None


def _data_is_empty(data: Any) -> bool:
    if data is None or data == "":
        return True
    if isinstance(data, (list, tuple, set)):
        return not data
    if isinstance(data, Mapping):
        if not data:
            return True
        present_collections = [data[key] for key in COLLECTION_KEYS if key in data]
        if present_collections:
            return all(
                isinstance(value, (list, tuple, set, Mapping)) and not value
                for value in present_collections
            )
    return False


def classify_response(response: Any) -> tuple[str, str | None]:
    result = _result(response)
    structured = _structured(response)
    if result is None or structured is None:
        return "PROTOCOL_ERROR", None
    if result.get("isError") is True or structured.get("ok") is False:
        error = structured.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        return "TOOL_ERROR", str(code) if code is not None else None
    return (
        "SUCCESS_EMPTY" if _data_is_empty(structured.get("data")) else "SUCCESS_DATA",
        None,
    )


def _find_forbidden_keys(value: Any, path: str = "details") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if str(key) in FORBIDDEN_ERROR_DETAIL_KEYS:
                found.append(child)
            found.extend(_find_forbidden_keys(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_forbidden_keys(item, f"{path}[{index}]"))
    return found


def validate_contract(response: Any, tool_name: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(response, Mapping):
        return ["JSON-RPC response must be an object"]
    if response.get("jsonrpc") != "2.0":
        errors.append("jsonrpc must equal '2.0'")
    result = _result(response)
    if result is None:
        return errors + ["JSON-RPC response.result must be an object"]

    meta = result.get("_meta")
    if not isinstance(meta, Mapping):
        errors.append("result._meta must be an object")
    else:
        if meta.get("contract") != CONTRACT_NAME:
            errors.append(f"result._meta.contract must equal {CONTRACT_NAME!r}")
        if meta.get("contractVersion") != CONTRACT_VERSION:
            errors.append(f"result._meta.contractVersion must equal {CONTRACT_VERSION!r}")
        if meta.get("tool") != tool_name:
            errors.append(f"result._meta.tool must equal {tool_name!r}")

    content = result.get("content")
    if not isinstance(content, list):
        errors.append("result.content must be an array")
    else:
        for index, item in enumerate(content):
            if not isinstance(item, Mapping) or item.get("type") != "text":
                errors.append(f"result.content[{index}] must be a text content object")
                continue
            text = item.get("text")
            if not isinstance(text, str):
                errors.append(f"result.content[{index}].text must be a string")
            elif len(text) > 1000:
                errors.append(
                    f"result.content[{index}].text exceeds concise limit (1000 chars)"
                )

    structured = _structured(response)
    if structured is None:
        return errors + ["result.structuredContent must be an object"]
    if not isinstance(structured.get("ok"), bool):
        errors.append("structuredContent.ok must be boolean")
    if "data" not in structured or "error" not in structured:
        errors.append("structuredContent must contain data and error")

    is_error = result.get("isError")
    if not isinstance(is_error, bool):
        errors.append("result.isError must be boolean")
    ok = structured.get("ok")
    if isinstance(is_error, bool) and isinstance(ok, bool) and is_error == ok:
        errors.append("result.isError must be the inverse of structuredContent.ok")

    error = structured.get("error")
    if ok is True:
        if error is not None:
            errors.append("successful structuredContent.error must be null")
    elif ok is False:
        if structured.get("data") is not None:
            errors.append("failed structuredContent.data must be null")
        if not isinstance(error, Mapping):
            errors.append("failed structuredContent.error must be an object")
        else:
            for field, expected_type in (
                ("code", str),
                ("message", str),
                ("retryable", bool),
                ("details", Mapping),
            ):
                if not isinstance(error.get(field), expected_type):
                    errors.append(f"structuredContent.error.{field} has invalid type")
            details = error.get("details")
            if isinstance(details, Mapping):
                for path in _find_forbidden_keys(details):
                    errors.append(f"error response exposes internal field {path}")
    return errors


def _expectation_errors(
    case: ToolCase, status: str, error_code: str | None
) -> tuple[str, ...]:
    expected = case.expected_status
    errors: list[str] = []
    if expected == "SUCCESS" and not status.startswith("SUCCESS_"):
        errors.append(f"expected SUCCESS, received {status}")
    elif expected not in {"ANY", "SUCCESS"} and status != expected:
        errors.append(f"expected {expected}, received {status}")
    if case.expected_error_code is not None and error_code != case.expected_error_code:
        errors.append(
            f"expected error code {case.expected_error_code!r}, received {error_code!r}"
        )
    return tuple(errors)


def execute_suite(
    suite: SuiteConfig,
    *,
    timeout: float = 120.0,
    client_factory: Callable[..., MCPClient] = MCPClient,
    progress: Callable[[str], None] | None = print,
) -> tuple[list[BatchRun], dict[str, list[str]], tuple[str, ...]]:
    runs: list[BatchRun] = []
    inventories: dict[str, list[str]] = {}
    inventory_errors: list[str] = []

    for server in suite.servers:
        client = client_factory(server.endpoint, timeout=timeout)
        try:
            client.initialize()
            live_tools = client.list_tools()
        except Exception as exc:
            message = f"{server.name}: cannot initialize/list tools: {type(exc).__name__}: {exc}"
            inventory_errors.append(message)
            live_tools = []

        catalog = {
            str(item.get("name")): item
            for item in live_tools
            if isinstance(item, Mapping) and item.get("name")
        }
        inventories[server.name] = sorted(catalog)
        configured = {case.tool for case in server.cases}
        live = set(catalog)
        stale = sorted(configured - live)
        missing = sorted(live - configured) if server.require_full_inventory else []
        if stale:
            inventory_errors.append(
                f"{server.name}: configured tools missing from live inventory: {stale}"
            )
        if missing:
            inventory_errors.append(
                f"{server.name}: live tools without fixed test cases: {missing}"
            )

        for index, case in enumerate(server.cases, 1):
            tool = catalog.get(case.tool, {})
            if progress is not None:
                progress(
                    f"[{server.name} {index}/{len(server.cases)}] {case.tool}"
                )
            started = time.perf_counter()
            if case.tool not in catalog:
                response: Any = {
                    "client_exception": f"tool {case.tool!r} is absent from live inventory"
                }
                status, error_code = "CLIENT_EXCEPTION", None
                contract_errors = ("tool was not called because it is not registered",)
            else:
                try:
                    response = client.call_tool_raw(case.tool, dict(case.arguments))
                    status, error_code = classify_response(response)
                    contract_errors = tuple(validate_contract(response, case.tool))
                except Exception as exc:
                    response = {
                        "client_exception": f"{type(exc).__name__}: {exc}"
                    }
                    status, error_code = "CLIENT_EXCEPTION", None
                    contract_errors = ("no valid JSON-RPC response was received",)
            duration_ms = (time.perf_counter() - started) * 1000
            runs.append(
                BatchRun(
                    server=server.name,
                    endpoint=server.endpoint,
                    case=case,
                    description=str(tool.get("description") or ""),
                    input_schema=tool.get("inputSchema"),
                    output_schema=tool.get("outputSchema"),
                    response=response,
                    status=status,
                    error_code=error_code,
                    duration_ms=duration_ms,
                    contract_errors=contract_errors,
                    expectation_errors=_expectation_errors(case, status, error_code),
                )
            )
    return runs, inventories, tuple(inventory_errors)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _duration_seconds(started_at: datetime, finished_at: datetime) -> float:
    return max(0.0, (finished_at - started_at).total_seconds())


def render_markdown(
    suite: SuiteConfig,
    runs: Sequence[BatchRun],
    *,
    inventories: Mapping[str, Sequence[str]],
    inventory_errors: Sequence[str],
    started_at: datetime,
    finished_at: datetime,
) -> str:
    status_counts = Counter(run.status for run in runs)
    error_counts = Counter(run.error_code for run in runs if run.error_code)
    passed = sum(run.passed for run in runs)
    failed = len(runs) - passed
    overall = "PASS" if not failed and not inventory_errors else "FAIL"
    inventory_total = sum(len(items) for items in inventories.values())

    lines = [
        "---",
        'type: "MCP fixed-suite execution report"',
        f'date: "{started_at.date().isoformat()}"',
        f'suite: "{suite.name}"',
        f'project: "{suite.project}"',
        "---",
        "",
        f"# MCP fixed-suite report: {suite.name}",
        "",
        "## Summary",
        "",
        f"- Overall: **{overall}**",
        f"- Started: `{started_at.isoformat()}`",
        f"- Finished: `{finished_at.isoformat()}`",
        f"- Duration: `{_duration_seconds(started_at, finished_at):.2f}s`",
        f"- Project: `{suite.project}`",
        f"- Parser: `{suite.parser}`",
        f"- Live inventory: `{inventory_total}` tools across `{len(inventories)}` servers",
        f"- Cases executed: `{len(runs)}` (`{passed}` passed, `{failed}` failed)",
        "- Evidence: advertised schema, exact input, complete parsed JSON-RPC output.",
        "",
        "| Status | Count |",
        "| --- | ---: |",
    ]
    for status in (
        "SUCCESS_DATA",
        "SUCCESS_EMPTY",
        "TOOL_ERROR",
        "PROTOCOL_ERROR",
        "CLIENT_EXCEPTION",
    ):
        lines.append(f"| {status} | {status_counts.get(status, 0)} |")

    lines.extend(["", "### Error codes", "", "| Code | Count |", "| --- | ---: |"])
    if error_counts:
        for code, count in sorted(error_counts.items()):
            lines.append(f"| `{code}` | {count} |")
    else:
        lines.append("| — | 0 |")

    lines.extend(["", "### Inventory validation", ""])
    if inventory_errors:
        lines.extend(f"- FAIL: {message}" for message in inventory_errors)
    else:
        lines.append("- PASS: every live tool has exactly one fixed case.")

    lines.extend(
        [
            "",
            "## Execution index",
            "",
            "| # | Server | Tool | Result | Status | Error code | Contract | Time (ms) |",
            "| ---: | --- | --- | --- | --- | --- | --- | ---: |",
        ]
    )
    for index, run in enumerate(runs, 1):
        result = "PASS" if run.passed else "FAIL"
        contract = "PASS" if not run.contract_errors else "FAIL"
        code = f"`{run.error_code}`" if run.error_code else ""
        lines.append(
            f"| {index} | `{run.server}` | `{run.case.tool}` | {result} | "
            f"{run.status} | {code} | {contract} | {run.duration_ms:.2f} |"
        )

    for index, run in enumerate(runs, 1):
        lines.extend(
            [
                "",
                f"## {index:02d}. `{run.server}.{run.case.tool}`",
                "",
                f"- Result: **{'PASS' if run.passed else 'FAIL'}**",
                f"- Status: **{run.status}**",
                f"- Contract: **{'PASS' if not run.contract_errors else 'FAIL'}**",
                f"- Duration: `{run.duration_ms:.2f} ms`",
                f"- Endpoint: `{run.endpoint}`",
                f"- Expected status: `{run.case.expected_status}`",
            ]
        )
        if run.case.expected_error_code:
            lines.append(f"- Expected error code: `{run.case.expected_error_code}`")
        if run.error_code:
            lines.append(f"- Actual error code: `{run.error_code}`")
        if run.contract_errors:
            lines.extend(["", "### Contract violations", ""])
            lines.extend(f"- {message}" for message in run.contract_errors)
        if run.expectation_errors:
            lines.extend(["", "### Expectation mismatches", ""])
            lines.extend(f"- {message}" for message in run.expectation_errors)
        lines.extend(
            [
                "",
                "### Description",
                "",
                run.description or "—",
                "",
                "### Input schema",
                "",
                "```json",
                _json(run.input_schema),
                "```",
                "",
                "### Output schema advertised by `tools/list`",
                "",
                "```json",
                _json(run.output_schema),
                "```",
                "",
                "### Input executed",
                "",
                "```json",
                _json(run.case.arguments),
                "```",
                "",
                "### Raw parsed JSON-RPC output",
                "",
                "```json",
                _json(run.response),
                "```",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _output_path(value: str | None, suite: SuiteConfig, now: datetime) -> Path:
    filename = f"{now.strftime('%y%m%d-%H%M%S')}-{suite.name}.md"
    if value is None:
        return DEFAULT_OUTPUT_DIR / filename
    path = Path(value).expanduser()
    if path.suffix.lower() == ".md":
        return path
    return path / filename


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fixed MCP inputs and write a complete Markdown report."
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=DEFAULT_SUITE,
        help=f"suite JSON path (default: {DEFAULT_SUITE})",
    )
    parser.add_argument(
        "--output",
        help="output .md path or directory (default: testtool/outputs)",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--quiet", action="store_true", help="hide per-tool progress"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        suite = load_suite(args.suite.expanduser().resolve())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now().astimezone()
    try:
        runs, inventories, inventory_errors = execute_suite(
            suite,
            timeout=args.timeout,
            progress=None if args.quiet else print,
        )
    except KeyboardInterrupt:
        print("ERROR: interrupted; no partial report written", file=sys.stderr)
        return 130
    finished_at = datetime.now().astimezone()
    output = _output_path(args.output, suite, finished_at).resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            render_markdown(
                suite,
                runs,
                inventories=inventories,
                inventory_errors=inventory_errors,
                started_at=started_at,
                finished_at=finished_at,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"ERROR: cannot write report {output}: {exc}", file=sys.stderr)
        return 2

    failed = [run for run in runs if not run.passed]
    print(f"Report: {output}")
    print(
        f"Cases: {len(runs)} | passed: {len(runs) - len(failed)} | "
        f"failed: {len(failed)} | inventory errors: {len(inventory_errors)}"
    )
    return 1 if failed or inventory_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
