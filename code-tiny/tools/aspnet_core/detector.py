from __future__ import annotations

from tools.common.aspnet.project_metadata import ModuleDetection, detect_modules


class AspNetCoreDetector:
    def __init__(self, root: str) -> None:
        self.root = root
        self._modules = detect_modules(root, "aspnet_core")

    def discover_modules(self) -> tuple[ModuleDetection, ...]:
        return tuple(item for item in self._modules if item.detected)

    def detect_path(self, path: str, *, include_undetected: bool = False) -> ModuleDetection | None:
        normalized = path.replace("\\", "/").strip("/")
        candidates = [
            item for item in self._modules
            if (item.detected or include_undetected) and (
                item.module_path in {"", "."}
                or normalized == item.module_path
                or normalized.startswith(item.module_path.rstrip("/") + "/")
            )
        ]
        return max(candidates, key=lambda item: len(item.module_path), default=None)


def is_strong_deleted_candidate(path: str) -> bool:
    lower = path.replace("\\", "/").lower()
    name = lower.rsplit("/", 1)[-1]
    return (
        lower.endswith((".cshtml", ".razor", ".csproj"))
        or (name.startswith("appsettings") and name.endswith(".json"))
        or name in {"program.cs", "startup.cs"}
    )
