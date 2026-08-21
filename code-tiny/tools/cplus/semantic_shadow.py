"""Shadow-mode semantic-evidence comparison report (Phase 02).

Runs the isolated semantic worker over a corpus in shadow mode and produces
a comparison artifact against the reviewed Phase 01 expectations.  Shadow
mode never replaces Tree-sitter structure and never publishes consumer
``CALLS`` edges; the report is the only output.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional

from tools.common.call_evidence import CALL_EVIDENCE_SCHEMA_VERSION
from tools.cplus.parse_recovery import run_semantic_worker
from tools.cplus.semantic_worker import (
    SEMANTIC_BACKEND_ID,
    SEMANTIC_REQUEST_SCHEMA,
    SEMANTIC_WORKER_PROTOCOL_VERSION,
)

SHADOW_REPORT_SCHEMA_VERSION = "1"

_CPP_FILES = (".cpp", ".cc", ".cxx", ".hpp", ".hh")
_C_FILES = (".c", ".h")


def _default_arguments(path: str) -> List[str]:
    if path.lower().endswith(_CPP_FILES):
        return ["-std=c++17"]
    if path.lower().endswith(_C_FILES):
        return ["-std=c11"]
    return []


def run_shadow_comparison(
    *,
    root: str,
    files: List[str],
    output_path: str,
    worker_path: str,
    expected: Optional[Mapping[str, Any]] = None,
    timeout_seconds: float = 30.0,
) -> Dict[str, Any]:
    """Run the semantic worker in shadow mode over ``files`` and report.

    ``expected`` is the reviewed expectations document
    (``tests/fixtures/cplus_semantic_calls/expected.json``).  The report
    records per-file observed classes and expectation matches; it is a
    comparison artifact only.
    """

    expectations = dict((expected or {}).get("files") or {})
    per_file: Dict[str, Any] = {}
    matched = mismatched = missing = 0

    for rel_file in files:
        abs_path = os.path.join(root, rel_file)
        request = {
            "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
            "request_schema": SEMANTIC_REQUEST_SCHEMA,
            "root": root,
            "path": abs_path,
            "compile_arguments": _default_arguments(rel_file),
            "compile_context_fingerprint": "shadow",
            "memory_mb": 1024,
            "cpu_seconds": 20,
        }
        result = run_semantic_worker(
            worker_path=worker_path,
            request=request,
            timeout_seconds=timeout_seconds,
        )
        entry: Dict[str, Any] = {
            "worker_status": result.get("status"),
            "error": result.get("error") or "",
            "callsites": [],
        }
        if result.get("status") == "ok":
            entry["callsites"] = [
                {
                    "callee_name": site.get("callee_name"),
                    "resolution_class": site.get("resolution_class"),
                    "callee_usr": site.get("callee_usr"),
                    "macro_origin": site.get("macro_origin"),
                }
                for site in result.get("callsites") or ()
            ]
        per_file[rel_file] = entry

        file_expectation = expectations.get(rel_file)
        if file_expectation:
            checks = []
            for expected_site in file_expectation.get("callsites") or ():
                want_name = expected_site.get("callee_name")
                want_class = str(expected_site.get("semantic_expectation") or "")
                observed = next(
                    (s for s in entry["callsites"] if s.get("callee_name") == want_name),
                    None,
                )
                if observed is None:
                    missing += 1
                    checks.append({"callee_name": want_name, "match": "missing"})
                    continue
                observed_class = str(observed.get("resolution_class") or "")
                # Reviewed expectations may encode acceptable alternatives
                # (e.g. "direct_resolved_or_unresolved") or refinements
                # ("direct_resolved_distinct_overloads"); accept a match when
                # the observed class is one of the "_"-joined alternatives'
                # prefixes.
                ok = observed_class in want_class.split("_or_") or (
                    want_class.startswith(observed_class)
                    and want_class != observed_class
                    and "distinct" in want_class
                )
                if want_class == "macro_expansion_to_direct_resolved":
                    ok = observed_class == "direct_resolved" and bool(observed.get("macro_origin"))
                if want_class == "precompiler_runtime_or_unmapped":
                    ok = observed_class in ("unresolved",) or "unmapped" in want_class
                if ok:
                    matched += 1
                    checks.append(
                        {"callee_name": want_name, "match": "match", "observed": observed_class}
                    )
                else:
                    mismatched += 1
                    checks.append(
                        {
                            "callee_name": want_name,
                            "match": "mismatch",
                            "expected": want_class,
                            "observed": observed_class,
                        }
                    )
            entry["expectation_checks"] = checks

    report = {
        "schema_version": SHADOW_REPORT_SCHEMA_VERSION,
        "evidence_schema_version": CALL_EVIDENCE_SCHEMA_VERSION,
        "mode": "shadow",
        "backend": SEMANTIC_BACKEND_ID,
        "published_calls": 0,  # shadow mode never publishes
        "files": per_file,
        "summary": {
            "files": len(files),
            "matched": matched,
            "mismatched": mismatched,
            "missing": missing,
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=True, indent=2, sort_keys=True)
    return report
