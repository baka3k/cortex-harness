"""Conservative, project-local Perl reference and dependency resolution."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import DefaultDict, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from .models import DependencyIndex, Diagnostic, ParsedFile, ReferenceRecord, SymbolRecord


_PERL_BUILTINS = {
    "bless",
    "caller",
    "chdir",
    "close",
    "defined",
    "delete",
    "die",
    "do",
    "each",
    "eval",
    "exists",
    "grep",
    "join",
    "keys",
    "map",
    "open",
    "pop",
    "print",
    "printf",
    "push",
    "ref",
    "require",
    "return",
    "scalar",
    "shift",
    "sort",
    "split",
    "sprintf",
    "substr",
    "undef",
    "unshift",
    "values",
    "warn",
    "__FILE__",
    "__LINE__",
    "__PACKAGE__",
}


@dataclass(frozen=True)
class ResolutionResult:
    parsed_files: Tuple[ParsedFile, ...]
    dependency_index: DependencyIndex
    diagnostics: Tuple[Diagnostic, ...]


def _module_path_candidates(module: str, file_paths: Iterable[str]) -> Tuple[str, ...]:
    suffix = module.replace("::", "/") + ".pm"
    return tuple(sorted(path for path in file_paths if path == suffix or path.endswith("/" + suffix)))


def _current_package(reference: ReferenceRecord, symbols_by_id: Mapping[str, SymbolRecord]) -> str:
    owner = symbols_by_id.get(reference.source_symbol_id)
    if owner is not None:
        return owner.package
    if "::" in reference.source_name:
        return reference.source_name.rsplit("::", 1)[0]
    return reference.source_name or "main"


def _diagnostic(code: str, message: str, reference: ReferenceRecord) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="info",
        message=message,
        file_path=reference.file_path,
        span=reference.span,
        details=(("target", reference.target_name),),
    )


def resolve_project(parsed_files: Sequence[ParsedFile]) -> ResolutionResult:
    """Resolve only exact, unambiguous project-local Perl references."""
    ordered = tuple(sorted(parsed_files, key=lambda item: item.file.file_path))
    file_paths = {item.file.file_path for item in ordered}
    symbols = tuple(symbol for item in ordered for symbol in item.symbols)
    symbols_by_id = {symbol.symbol_id: symbol for symbol in symbols}
    subroutines_by_fq: DefaultDict[str, List[SymbolRecord]] = defaultdict(list)
    package_files: DefaultDict[str, Set[str]] = defaultdict(set)
    symbol_file: Dict[str, str] = {}
    for symbol in symbols:
        symbol_file[symbol.symbol_id] = symbol.file_path
        if symbol.kind == "subroutine":
            subroutines_by_fq[symbol.fq_name].append(symbol)
        elif symbol.kind == "package":
            package_files[symbol.fq_name].add(symbol.file_path)

    forward: DefaultDict[str, Set[str]] = defaultdict(set)
    diagnostics: List[Diagnostic] = []
    updated_files: List[ParsedFile] = []

    for parsed in ordered:
        resolved_imports = []
        imported_modules: List[str] = []
        for item in parsed.imports:
            if item.is_dynamic or not item.module:
                resolved_imports.append(item)
                continue
            candidates = set(package_files.get(item.module, set()))
            candidates.update(_module_path_candidates(item.module, file_paths))
            if len(candidates) == 1:
                target_path = next(iter(candidates))
                forward[item.file_path].add(target_path)
                imported_modules.append(item.module)
                resolved_imports.append(replace(item, resolved_path=target_path))
            else:
                code = "perl.import.ambiguous" if candidates else "perl.import.missing"
                diagnostics.append(
                    Diagnostic(
                        code=code,
                        severity="info",
                        message=(
                            f"Static module {item.module!r} has {len(candidates)} project-local candidates."
                            if candidates
                            else f"Static module {item.module!r} has no project-local file."
                        ),
                        file_path=item.file_path,
                        span=item.span,
                    )
                )
                resolved_imports.append(item)

        updated_references: List[ReferenceRecord] = []
        for reference in parsed.references:
            if reference.kind not in {"direct", "qualified"}:
                updated_references.append(reference)
                continue
            target = reference.target_name.lstrip("&")
            if target in _PERL_BUILTINS:
                updated_references.append(
                    replace(
                        reference,
                        resolution_status="builtin",
                        confidence=1.0,
                        reason="recognized Perl builtin",
                    )
                )
                continue
            if target.startswith("SUPER::"):
                updated_references.append(
                    replace(reference, reason="SUPER dispatch requires runtime inheritance state")
                )
                continue

            package = _current_package(reference, symbols_by_id)
            candidate_names: List[str] = []
            if "::" in target:
                candidate_names.append(target)
            else:
                candidate_names.append(f"{package}::{target}")
                candidate_names.extend(f"{module}::{target}" for module in imported_modules)

            candidates: List[SymbolRecord] = []
            seen_ids: Set[str] = set()
            for name in candidate_names:
                for symbol in subroutines_by_fq.get(name, ()):
                    if symbol.symbol_id not in seen_ids:
                        candidates.append(symbol)
                        seen_ids.add(symbol.symbol_id)
                if candidates and name == candidate_names[0]:
                    break

            if len(candidates) == 1:
                destination = candidates[0]
                forward[reference.file_path].add(destination.file_path)
                updated_references.append(
                    replace(
                        reference,
                        resolution_status="resolved",
                        target_symbol_id=destination.symbol_id,
                        confidence=1.0 if reference.kind == "qualified" else 0.95,
                        reason="unique project-local subroutine",
                    )
                )
            elif len(candidates) > 1:
                updated_references.append(
                    replace(
                        reference,
                        resolution_status="ambiguous",
                        confidence=min(reference.confidence, 0.4),
                        reason=f"{len(candidates)} project-local candidates",
                    )
                )
                diagnostics.append(
                    _diagnostic(
                        "perl.reference.ambiguous",
                        f"Reference {reference.target_name!r} has multiple project-local candidates.",
                        reference,
                    )
                )
            else:
                updated_references.append(reference)
                diagnostics.append(
                    _diagnostic(
                        "perl.reference.unresolved",
                        f"Reference {reference.target_name!r} was left unresolved.",
                        reference,
                    )
                )

        updated_files.append(
            replace(
                parsed,
                imports=tuple(sorted(resolved_imports)),
                references=tuple(sorted(updated_references)),
            )
        )

    for path in file_paths:
        forward.setdefault(path, set())
    reverse: DefaultDict[str, Set[str]] = defaultdict(set)
    for source_path, targets in forward.items():
        for target_path in targets:
            reverse[target_path].add(source_path)
    for path in file_paths:
        reverse.setdefault(path, set())
    dependency_index = DependencyIndex.from_mappings(forward, reverse)
    return ResolutionResult(
        parsed_files=tuple(updated_files),
        dependency_index=dependency_index,
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def affected_file_closure(
    changed_paths: Iterable[str],
    dependency_index: DependencyIndex,
) -> Tuple[str, ...]:
    """Expand changed files through deterministic reverse dependencies."""
    reverse = dependency_index.reverse_map()
    seen = {path.replace("\\", "/") for path in changed_paths if path}
    queue = deque(sorted(seen))
    while queue:
        current = queue.popleft()
        for dependent in reverse.get(current, ()):
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)
    return tuple(sorted(seen))
