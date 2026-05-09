from __future__ import annotations

import os
import re
from typing import Dict, Optional

_VB6_PROJECT_FILES = {".vbp", ".vbw"}
_VB6_SOURCE_EXTS = {".bas", ".cls", ".frm", ".frx", ".ctl", ".pag"}
_VBA_SOURCE_EXTS = {".bas", ".cls", ".frm"}
_VBNET_SOURCE_EXTS = {".vb"}
_VBSCRIPT_SOURCE_EXTS = {".vbs", ".wsf", ".asp", ".hta"}

_VB6_STRONG_PATTERNS = [
    re.compile(r"^\s*VERSION\s+5\.00", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bBegin\s+VB\.", re.IGNORECASE),
    re.compile(r"\bAttribute\s+VB_GlobalNameSpace\b", re.IGNORECASE),
]

_VBA_STRONG_PATTERNS = [
    re.compile(r"\bThisWorkbook\b", re.IGNORECASE),
    re.compile(r"\bWorksheets?\s*\(", re.IGNORECASE),
    re.compile(r"\bWorkbook\b", re.IGNORECASE),
    re.compile(r"\bApplication\.WorksheetFunction\b", re.IGNORECASE),
    re.compile(r"\bRange\s*\(", re.IGNORECASE),
    re.compile(r"\bActiveWorkbook\b", re.IGNORECASE),
]

_VBA_OFFICE_PATTERNS = [
    re.compile(r"\b(ActiveSheet|ActiveCell|Selection)\b", re.IGNORECASE),
    re.compile(r"\bApplication\.(Excel|Word|Access|PowerPoint|Outlook)\b", re.IGNORECASE),
    re.compile(r"\bCells\s*\(", re.IGNORECASE),
    re.compile(r"\bRange\s*\(", re.IGNORECASE),
]

_VB6_CONTROL_PATTERNS = [
    re.compile(r"\bBegin\s+(VB\.|Form\.)", re.IGNORECASE),
    re.compile(r"\bAttribute\s+VB_\w+\b", re.IGNORECASE),
    re.compile(r"\b(Click|DoubleClick|MouseDown|MouseUp|KeyDown|KeyUp|KeyPress)\b", re.IGNORECASE),
]

_VBSCRIPT_HINTS = [
    re.compile(r"\bLanguage\s*=\s*\"?VBScript\"?", re.IGNORECASE),
    re.compile(r"<%", re.IGNORECASE),
]

_ASP_NON_VBSCRIPT_PATTERNS = [
    re.compile(r"<%@", re.IGNORECASE),  # ASP.NET
    re.compile(r"<\s*%@\s*Page\s+Language=\"?C#\"", re.IGNORECASE),  # ASP.NET C#
    re.compile(r"<\s*%@\s*Language=\"?JScript\"", re.IGNORECASE),  # JScript
    re.compile(r"<\?php", re.IGNORECASE),  # PHP
    re.compile(r"<jsp:", re.IGNORECASE),  # JSP
]

_CLASSIC_VBSCRIPT_PATTERNS = [
    re.compile(r"\bResponse\.(Write|End|Redirect)\b", re.IGNORECASE),
    re.compile(r"\bRequest\.(QueryString|Form|ServerVariables)\b", re.IGNORECASE),
    re.compile(r"\bServer\.(CreateObject|MapPath)\b", re.IGNORECASE),
    re.compile(r"\bSession\s*\(", re.IGNORECASE),
    re.compile(r"\bApplication\s*\(", re.IGNORECASE),
]


def _safe_rel(path: str) -> str:
    return (path or "").replace("\\", "/")


class VBPathClassifier:
    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self._vb6_ancestor_cache: Dict[str, bool] = {}
        self._vbnet_ancestor_cache: Dict[str, bool] = {}
        self._content_cache: Dict[str, str] = {}

    def select_parser_for_path(self, rel_path: str, owner_mode: str = "heuristic") -> Optional[str]:
        rel = _safe_rel(rel_path).strip("./")
        if not rel:
            return None
        lower = rel.lower()
        ext = os.path.splitext(lower)[1]

        if ext in _VB6_PROJECT_FILES or ext == ".frx":
            return "vb6"
        if ext in {".vbproj", ".vbproj.user", ".sln"}:
            return "vbnet"
        if ext in {".vbs", ".wsf", ".hta"}:
            return "vbscript"
        if ext == ".asp":
            return "vbscript" if self._looks_vbscript(rel) else None
        if ext in _VBNET_SOURCE_EXTS:
            # By contract, .vb belongs to VB.NET context. If no marker is found,
            # keep routing to vbnet to avoid dropping standalone VB.NET files.
            return "vbnet"
        if ext in _VBA_SOURCE_EXTS:
            return self._classify_vb6_vba_overlap(rel, owner_mode)
        return None

    def _classify_vb6_vba_overlap(self, rel_path: str, owner_mode: str = "heuristic") -> str:
        # Apply owner mode preference first
        mode = (owner_mode or "heuristic").strip().lower()
        if mode == "prefer-vb6":
            return "vb6"
        if mode == "prefer-vba":
            return "vba"

        # Heuristic mode: use scoring logic
        vb6_score = 0
        vba_score = 0

        if self._has_vb6_project_ancestor(rel_path):
            vb6_score += 3
        if self._has_vbnet_project_ancestor(rel_path):
            vb6_score += 1

        text = self._read_limited(rel_path)
        if text:
            for pat in _VB6_STRONG_PATTERNS:
                if pat.search(text):
                    vb6_score += 2
            for pat in _VBA_STRONG_PATTERNS:
                if pat.search(text):
                    vba_score += 2

            # Add VBA-specific Office patterns
            for pat in _VBA_OFFICE_PATTERNS:
                if pat.search(text):
                    vba_score += 1

            # Add VB6-specific control patterns
            for pat in _VB6_CONTROL_PATTERNS:
                if pat.search(text):
                    vb6_score += 1

        # Requested default for ambiguous overlap is vb6.
        return "vba" if vba_score > vb6_score else "vb6"

    def _looks_vbscript(self, rel_path: str) -> bool:
        text = self._read_limited(rel_path)
        if not text:
            return True

        # Check for non-VBScript patterns first
        if any(p.search(text) for p in _ASP_NON_VBSCRIPT_PATTERNS):
            return False

        # Existing VBScript hints
        if any(p.search(text) for p in _VBSCRIPT_HINTS):
            return True

        # Check for classic VBScript patterns
        if any(p.search(text) for p in _CLASSIC_VBSCRIPT_PATTERNS):
            return True

        # Default to True for .asp files if no conflicting patterns found
        return True

    def _has_vb6_project_ancestor(self, rel_path: str) -> bool:
        probe = os.path.dirname(_safe_rel(rel_path)).strip("/")
        while True:
            key = probe or "."
            cached = self._vb6_ancestor_cache.get(key)
            if cached is None:
                base = os.path.join(self.root, probe) if probe else self.root
                found = False
                try:
                    for name in os.listdir(base):
                        if os.path.splitext(name.lower())[1] in _VB6_PROJECT_FILES:
                            found = True
                            break
                except OSError:
                    found = False
                self._vb6_ancestor_cache[key] = found
                cached = found
            if cached:
                return True
            if not probe:
                return False
            probe = probe.rsplit("/", 1)[0] if "/" in probe else ""

    def _has_vbnet_project_ancestor(self, rel_path: str) -> bool:
        probe = os.path.dirname(_safe_rel(rel_path)).strip("/")
        while True:
            key = probe or "."
            cached = self._vbnet_ancestor_cache.get(key)
            if cached is None:
                base = os.path.join(self.root, probe) if probe else self.root
                found = False
                try:
                    for name in os.listdir(base):
                        lower = name.lower()
                        if lower.endswith(".vbproj") or lower.endswith(".vbproj.user") or lower == ".sln":
                            found = True
                            break
                except OSError:
                    found = False
                self._vbnet_ancestor_cache[key] = found
                cached = found
            if cached:
                return True
            if not probe:
                return False
            probe = probe.rsplit("/", 1)[0] if "/" in probe else ""

    def _read_limited(self, rel_path: str, limit: int = 128 * 1024) -> str:
        rel = _safe_rel(rel_path)
        cached = self._content_cache.get(rel)
        if cached is not None:
            return cached
        abs_path = os.path.join(self.root, rel)
        try:
            # Use binary mode first to handle encoding issues
            with open(abs_path, "rb") as handle:
                raw_bytes = handle.read(limit)

            # Try UTF-8 first, fallback to latin-1 (which accepts all byte values)
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = raw_bytes.decode("latin-1")
        except OSError:
            text = ""
        self._content_cache[rel] = text
        return text
