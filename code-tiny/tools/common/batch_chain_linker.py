"""Cross-language post-pass linking shell script `CALLS` targets to their
matching Pro*C/C `.pc`/`.c` source files by filename-stem, completing the
JP1 -> shell -> Pro*C -> DB batch chain.

Runs after the shell and cplus analyzers have each produced their own rows
(see `tools/shell/shell_analyzer.py::analyze_root`,
`tools/cplus/cplus_analyzer.py`): shell `CALLS` relations target a generic
`File` node keyed on the raw invocation text (e.g. `./BZZAAB02.sh`,
`${DIR}/BZZAAB02`), which does not necessarily match any real source file.
This module resolves those targets, by filename stem, against the set of
`.pc`/`.c`/`.cpp` files actually discovered by the cplus analyzer, and emits
an additional `CALLS` relation pointing at the matched cplus file.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Iterable, List


_CPLUS_EXTENSIONS = (".pc", ".c", ".cc", ".cpp", ".cxx")


def _stable_id(kind: str, symbol_id: str) -> str:
    return f"{kind}::{uuid.uuid5(uuid.NAMESPACE_URL, symbol_id)}"


def _stem(path: str) -> str:
    base = os.path.basename(path)
    for ext in _CPLUS_EXTENSIONS + (".sh",):
        if base.lower().endswith(ext):
            return base[: -len(ext)]
    return os.path.splitext(base)[0]


def _extract_ref_stem(callee_ref: str) -> str:
    """Strip path/shell-var prefixes and the `.sh` suffix from a shell
    invocation reference, e.g. "${CONFIG_DIR}/BZZAAB02" or "./BBSEAB02.sh"
    -> "BZZAAB02" / "BBSEAB02".
    """
    ref = callee_ref.strip()
    base = ref.rsplit("/", 1)[-1]
    for ext in (".sh",):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
    return base


def build_cplus_stem_index(cplus_file_paths: Iterable[str]) -> Dict[str, str]:
    """Map filename stem (no extension) -> relative file path, for `.pc`/`.c`
    family files discovered by the cplus analyzer. If multiple files share a
    stem, the first one wins (best-effort; ambiguity is not resolved here).
    """
    index: Dict[str, str] = {}
    for path in cplus_file_paths:
        if not path.lower().endswith(_CPLUS_EXTENSIONS):
            continue
        stem = _stem(path)
        index.setdefault(stem, path)
    return index


def link_shell_calls_to_cplus(
    shell_relations: List[Dict[str, Any]],
    cplus_file_paths: Iterable[str],
) -> List[Dict[str, Any]]:
    """Given shell analyzer relation rows and the set of cplus file paths
    discovered in the same root, return additional `CALLS` relation rows
    resolving shell `CALLS` targets that name-match a `.pc`/`.c` file stem.
    """
    stem_index = build_cplus_stem_index(cplus_file_paths)
    if not stem_index:
        return []

    resolved: List[Dict[str, Any]] = []
    for relation in shell_relations:
        if relation.get("rel_type") != "CALLS":
            continue
        callee_ref = (relation.get("properties") or {}).get("callee_ref", "")
        if not callee_ref:
            continue
        stem = _extract_ref_stem(callee_ref)
        matched_path = stem_index.get(stem)
        if not matched_path:
            continue
        resolved.append(
            {
                "source_id": relation["source_id"],
                "source_label": relation["source_label"],
                "target_id": _stable_id("file", matched_path),
                "target_label": "File",
                "rel_type": "CALLS",
                "properties": {
                    "callee_ref": callee_ref,
                    "resolved_via": "batch_chain_linker",
                    "matched_path": matched_path,
                },
            }
        )
    return resolved
