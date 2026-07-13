from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from tools.java.java_analyzer import parse_java_file


@dataclass(frozen=True)
class JavaIdentityIndex:
    file_path: str
    classes_by_fqcn: Dict[str, str]
    methods_exact: Dict[Tuple[str, str, int, int], str]
    methods_by_signature: Dict[Tuple[str, str, int], Tuple[str, ...]]
    parse_meta: Dict[str, object]

    def class_id(self, fqcn: str) -> str:
        return self.classes_by_fqcn.get(fqcn, "")

    def method_id(self, class_name: str, name: str, arity: int, start_line: Optional[int] = None) -> Tuple[str, str]:
        if start_line is not None:
            exact = self.methods_exact.get((class_name, name, arity, start_line))
            if exact:
                return exact, "exact"
        candidates = self.methods_by_signature.get((class_name, name, arity), ())
        if len(candidates) == 1:
            return candidates[0], "unique_signature"
        if candidates:
            return "", "ambiguous"
        return "", "missing"


class JavaIdentityProvider:
    """Typed boundary around the base Java analyzer's positional result."""

    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(os.path.abspath(root))
        self._cache: Dict[Tuple[str, int, int], JavaIdentityIndex] = {}

    def index_file(self, path: str) -> JavaIdentityIndex:
        absolute = path if os.path.isabs(path) else os.path.join(self.root, path)
        info = os.stat(absolute)
        key = (os.path.realpath(absolute), info.st_mtime_ns, info.st_size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        functions, _, classes, _, _, _, file_def, _, parse_meta = parse_java_file(absolute, self.root)
        class_map = {item.qualified_name: item.symbol_id for item in classes}
        exact: Dict[Tuple[str, str, int, int], str] = {}
        signatures: Dict[Tuple[str, str, int], list[str]] = {}
        for method in functions:
            exact[(method.class_name, method.name, method.arity, method.start_line)] = method.symbol_id
            signatures.setdefault((method.class_name, method.name, method.arity), []).append(method.symbol_id)
        index = JavaIdentityIndex(
            file_path=file_def.file_path.replace("\\", "/"),
            classes_by_fqcn=class_map,
            methods_exact=exact,
            methods_by_signature={item: tuple(sorted(values)) for item, values in signatures.items()},
            parse_meta=dict(parse_meta),
        )
        self._cache = {key: index}
        return index

