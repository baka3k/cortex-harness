from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import EndpointFact, WebAnalysisResult, stable_id


_SKIP_DIRS = frozenset({
    ".git", ".cache", ".venv", "venv", "node_modules", "vendor", "dist", "build",
    "__pycache__", "bin", "obj",
})
_FASTAPI_RE = re.compile(
    r"@(?:\w+\.)?(?:app|router|api_router|blueprint)\.(?P<method>get|post|put|delete|patch|head|options|route|websocket)"
    r"\s*\(\s*[rRuUfF]*['\"](?P<path>[^'\"]+)['\"][^)]*\)\s*"
    r"(?:async\s+)?def\s+(?P<handler>[A-Za-z_]\w*)\s*\(",
    re.IGNORECASE | re.DOTALL,
)
_DJANGO_RE = re.compile(
    r"\b(?:path|re_path)\s*\(\s*[rRuU]*['\"](?P<path>[^'\"]+)['\"]\s*,\s*"
    r"(?P<handler>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)(?:\.as_view\s*\(\s*\))?",
    re.MULTILINE,
)
_EXPRESS_RE = re.compile(
    r"\b(?:app|router|server|api)\.(?P<method>get|post|put|delete|patch|head|options|all|use)"
    r"\s*\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<handler>[A-Za-z_$][\w$]*)",
    re.IGNORECASE,
)
_LARAVEL_RE = re.compile(
    r"\bRoute::(?P<method>get|post|put|delete|patch|options|any|match)\s*\(\s*"
    r"['\"](?P<path>[^'\"]+)['\"]\s*,\s*\[\s*(?P<scope>[A-Za-z_\\][\w\\]*)::class"
    r"\s*,\s*['\"](?P<handler>[A-Za-z_]\w*)['\"]\s*\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Symbol:
    name: str
    scope: str
    file_path: str
    label: str = "Function"


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _source_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in {".py", ".js", ".jsx", ".php"}:
            yield path


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _normalize_path(value: str) -> str:
    path = value.strip()
    if not path.startswith("/"):
        path = "/" + path
    return re.sub(r"/{2,}", "/", path)


def _symbol_index(root: Path, files: Sequence[Path]) -> Dict[str, List[_Symbol]]:
    index: Dict[str, List[_Symbol]] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = _relative(root, path)
        suffix = path.suffix.lower()
        if suffix == ".py":
            for match in re.finditer(r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", text):
                symbol = _Symbol(match.group(1), "", rel)
                index.setdefault(symbol.name, []).append(symbol)
        elif suffix in {".js", ".jsx"}:
            patterns = (
                r"(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
            )
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    symbol = _Symbol(match.group(1), "", rel)
                    index.setdefault(symbol.name, []).append(symbol)
        elif suffix == ".php":
            class_match = re.search(r"\bclass\s+([A-Za-z_]\w*)", text)
            scope = class_match.group(1) if class_match else ""
            for match in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", text):
                symbol = _Symbol(match.group(1), scope, rel)
                index.setdefault(symbol.name, []).append(symbol)
    return index


def _resolve_handler(
    symbols: Dict[str, List[_Symbol]], name: str, scope: str = "",
) -> Tuple[str, str, str, str]:
    candidates = list(symbols.get(name, ()))
    if scope:
        scoped = [item for item in candidates if item.scope == scope.split("\\")[-1]]
        if scoped:
            candidates = scoped
    if len(candidates) == 1:
        item = candidates[0]
        return item.file_path, item.scope, item.label, "resolved"
    return "", scope.split("\\")[-1] if scope else "", "Function", (
        "ambiguous" if candidates else "unresolved"
    )


def _endpoint(
    *, project_id: str, framework: str, method: str, route: str, rel: str,
    line: int, handler: str, symbols: Dict[str, List[_Symbol]], scope: str = "",
) -> EndpointFact:
    handler_file, resolved_scope, label, status = _resolve_handler(symbols, handler, scope)
    normalized_route = _normalize_path(route)
    normalized_method = "ALL" if method.lower() in {"route", "use", "all", "any", "match"} else method.upper()
    return EndpointFact(
        endpoint_id=stable_id(project_id, framework, normalized_method, normalized_route, rel, line),
        project_id=project_id,
        framework=framework,
        http_method=normalized_method,
        path=normalized_route,
        file_path=rel,
        start_line=line,
        handler_name=handler,
        handler_scope=resolved_scope,
        handler_file=handler_file,
        handler_label=label,
        resolution_status=status,
        confidence=1.0 if status == "resolved" else 0.6,
    )


def analyze_project(
    root: Path | str,
    project_id: str,
    frameworks: Sequence[str],
    selected_paths: Optional[Sequence[str]] = None,
) -> WebAnalysisResult:
    root_path = Path(root).resolve()
    files = tuple(_source_files(root_path))
    symbols = _symbol_index(root_path, files)
    selected = {str(path).replace("\\", "/") for path in selected_paths or ()}
    framework_set = {str(value).strip().lower() for value in frameworks}
    endpoints: List[EndpointFact] = []
    for path in files:
        rel = _relative(root_path, path)
        if selected and rel not in selected:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        suffix = path.suffix.lower()
        if suffix == ".py" and {"fastapi", "django"} & framework_set:
            if "fastapi" in framework_set:
                for match in _FASTAPI_RE.finditer(text):
                    endpoints.append(_endpoint(
                        project_id=project_id, framework="fastapi", method=match.group("method"),
                        route=match.group("path"), rel=rel, line=_line(text, match.start()),
                        handler=match.group("handler"), symbols=symbols,
                    ))
            if "django" in framework_set:
                for match in _DJANGO_RE.finditer(text):
                    raw_handler = match.group("handler")
                    endpoints.append(_endpoint(
                        project_id=project_id, framework="django", method="ALL",
                        route=match.group("path"), rel=rel, line=_line(text, match.start()),
                        handler=raw_handler.split(".")[-1], symbols=symbols,
                    ))
        elif suffix in {".js", ".jsx"} and "express_js" in framework_set:
            for match in _EXPRESS_RE.finditer(text):
                endpoints.append(_endpoint(
                    project_id=project_id, framework="express_js", method=match.group("method"),
                    route=match.group("path"), rel=rel, line=_line(text, match.start()),
                    handler=match.group("handler"), symbols=symbols,
                ))
        elif suffix == ".php" and "laravel" in framework_set:
            for match in _LARAVEL_RE.finditer(text):
                endpoints.append(_endpoint(
                    project_id=project_id, framework="laravel", method=match.group("method"),
                    route=match.group("path"), rel=rel, line=_line(text, match.start()),
                    handler=match.group("handler"), scope=match.group("scope"), symbols=symbols,
                ))
    unique = {item.endpoint_id: item for item in endpoints}
    return WebAnalysisResult(
        project_id=project_id,
        endpoints=tuple(sorted(unique.values(), key=lambda item: (item.framework, item.file_path, item.start_line))),
    )


__all__ = ["WebAnalysisResult", "analyze_project"]
