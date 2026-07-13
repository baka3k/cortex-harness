from __future__ import annotations

import os
import re
from typing import Iterable, List, Protocol, Sequence

from tools.spring.detector import read_limited
from tools.spring.models import Diagnostic, LanguageSourceFact


_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][\w.]*)(?:\s*;)?", re.MULTILINE)
_ANNOTATION_RE = re.compile(r"@([A-Za-z_][\w.]*)")
_JAVA_DECL_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")
_KOTLIN_DECL_RE = re.compile(r"\b(?:class|interface|object|data\s+class|enum\s+class)\s+([A-Za-z_]\w*)")


class LanguageAdapter(Protocol):
    language: str
    extensions: Sequence[str]

    def collect(self, root: str, rel_paths: Iterable[str]) -> List[LanguageSourceFact]:
        ...


class _BaseAdapter:
    language = ""
    extensions: Sequence[str] = ()
    declaration_pattern: re.Pattern[str]

    def collect(self, root: str, rel_paths: Iterable[str]) -> List[LanguageSourceFact]:
        facts: List[LanguageSourceFact] = []
        for rel_path in sorted(set(rel_paths)):
            lower = rel_path.lower()
            if not lower.endswith(tuple(self.extensions)):
                continue
            text = read_limited(os.path.join(root, rel_path), limit=512 * 1024)
            package = _extract_package(text)
            declarations = tuple(self.declaration_pattern.findall(text))
            annotations = tuple(sorted(set(_ANNOTATION_RE.findall(text))))
            facts.append(
                LanguageSourceFact(
                    language=self.language,
                    file_path=rel_path,
                    source_symbol_id=f"{self.language}::file::{rel_path}",
                    package_name=package,
                    declarations=declarations,
                    annotations=annotations,
                    parser_status="base_adapter_pending",
                )
            )
        return facts


class JavaLanguageAdapter(_BaseAdapter):
    language = "java"
    extensions = (".java",)
    declaration_pattern = _JAVA_DECL_RE


class KotlinLanguageAdapter(_BaseAdapter):
    language = "kotlin"
    extensions = (".kt", ".kts")
    declaration_pattern = _KOTLIN_DECL_RE


def _extract_package(text: str) -> str:
    match = _PACKAGE_RE.search(text)
    return match.group(1) if match else ""


def adapters_for_languages(languages: Sequence[str]) -> List[LanguageAdapter]:
    requested = set(languages)
    adapters: List[LanguageAdapter] = []
    if "java" in requested:
        adapters.append(JavaLanguageAdapter())
    if "kotlin" in requested:
        adapters.append(KotlinLanguageAdapter())
    return adapters
