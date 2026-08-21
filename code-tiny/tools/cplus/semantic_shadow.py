"""Shadow-mode semantic-evidence comparison report (Phase 02).

Runs the isolated semantic worker over a corpus in shadow mode and produces
a comparison artifact against the reviewed Phase 01 expectations.  Shadow
mode never replaces Tree-sitter structure and never publishes consumer
``CALLS`` edges; the report is the only output.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Optional, Tuple

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


# Reviewed-expectation semantics: each semantic_expectation string maps to the
# exact set of acceptable observed resolution classes.  Specialized meanings
# (macro origin, distinct overload USRs) are explicit predicates so every
# expectation can actually fail — no ad hoc string prefix matching.
_EXPECTATION_ALLOWED_CLASSES = {
    "direct_resolved": ("direct_resolved",),
    "direct_resolved_or_unresolved": ("direct_resolved", "unresolved"),
    "unresolved": ("unresolved",),
    "indirect_callsite": ("indirect_callsite",),
    "declared_virtual_target": ("declared_virtual_target",),
    "dependent_template_call": ("dependent_template_call",),
    "macro_expansion_to_direct_resolved": ("direct_resolved",),
    "direct_resolved_distinct_overloads": ("direct_resolved",),
    "direct_resolved_with_proc_bundle": ("direct_resolved",),
    "precompiler_runtime_or_unmapped": ("direct_resolved", "unresolved"),
}


def _expectation_matches(
    want_class: str,
    observed_sites: List[Mapping[str, Any]],
    observed: Mapping[str, Any],
) -> Tuple[bool, str]:
    """Evaluate one expectation against the observed evidence.

    Returns (ok, reason).  Unknown expectation strings fail closed.
    """

    allowed = _EXPECTATION_ALLOWED_CLASSES.get(want_class)
    if allowed is None:
        return False, "unknown_expectation"
    observed_class = str(observed.get("resolution_class") or "")
    if observed_class not in allowed:
        return False, f"class_not_in_{want_class}"
    if want_class == "macro_expansion_to_direct_resolved" and not observed.get("macro_origin"):
        return False, "missing_macro_origin"
    if want_class == "direct_resolved_distinct_overloads":
        usrs = {
            str(site.get("callee_usr") or "")
            for site in observed_sites
            if site.get("callee_name") == observed.get("callee_name")
        }
        usrs.discard("")
        if len(usrs) < len(
            [s for s in observed_sites if s.get("callee_name") == observed.get("callee_name")]
        ):
            return False, "overload_usrs_not_distinct"
    return True, ""


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
                ok, why = _expectation_matches(want_class, entry["callsites"], observed)
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
                            "reason": why,
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
