"""Typed, provider-neutral facts for project topology analysis."""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from tools.common.project_scope import project_id_lookup_key


SCHEMA_VERSION = "1.0"
ANALYZER_VERSION = "1.0"


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ModuleKind(_ValueEnum):
    ROOT = "root"
    ANDROID_APPLICATION = "android_application"
    ANDROID_LIBRARY = "android_library"
    ANDROID_DYNAMIC_FEATURE = "android_dynamic_feature"
    JVM_APPLICATION = "jvm_application"
    JVM_LIBRARY = "jvm_library"
    MAVEN_MODULE = "maven_module"
    NATIVE_EXECUTABLE = "native_executable"
    NATIVE_LIBRARY = "native_library"
    PACKAGE = "package"
    DATABASE = "database"
    TEST = "test"
    UNKNOWN = "unknown"


class DescriptorType(_ValueEnum):
    GRADLE_SETTINGS = "gradle_settings"
    GRADLE_BUILD = "gradle_build"
    GRADLE_VERSION_CATALOG = "gradle_version_catalog"
    MAVEN_POM = "maven_pom"
    ANT_BUILD = "ant_build"
    CMAKE = "cmake"
    MAKE = "make"
    ANDROID_MANIFEST = "android_manifest"
    PROTOBUF = "protobuf"
    PACKAGE_MANIFEST = "package_manifest"
    WORKSPACE = "workspace"
    TOOLCHAIN = "toolchain"
    RUNTIME_CONFIG = "runtime_config"
    FRAMEWORK_CONFIG = "framework_config"
    MIGRATION = "migration"
    RESOURCE = "resource"
    DEPLOYMENT = "deployment"
    UNKNOWN = "unknown"


class DescriptorRole(_ValueEnum):
    IDENTITY = "identity"
    TOPOLOGY = "topology"
    DEPENDENCY = "dependency"
    CONFIGURATION = "configuration"
    FRAMEWORK = "framework"
    INTERFACE = "interface"
    RESOURCE = "resource"
    DEPLOYMENT = "deployment"
    GENERATED = "generated"
    SECRET_BEARING = "secret-bearing"


class ParseDepth(_ValueEnum):
    IDENTITY = "identity"
    TOPOLOGY = "topology"
    DEPENDENCY = "dependency"
    SEMANTIC = "semantic"
    UNSUPPORTED = "unsupported"


class DependencyScope(_ValueEnum):
    COMPILE = "compile"
    RUNTIME = "runtime"
    TEST = "test"
    PROVIDED = "provided"
    PLUGIN = "plugin"
    BUILD = "build"
    UNKNOWN = "unknown"


class EndpointProtocol(_ValueEnum):
    HTTP = "http"
    GRPC = "grpc"
    ROUTE = "route"
    PAGE = "page"
    SERVICE = "service"
    UNKNOWN = "unknown"


class Visibility(_ValueEnum):
    PUBLIC = "public"
    PROTECTED = "protected"
    INTERNAL = "internal"
    PACKAGE = "package"
    PRIVATE = "private"
    EXPORTED = "exported"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ConfidenceLevel(_ValueEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class DiagnosticCode(_ValueEnum):
    MALFORMED_DESCRIPTOR = "malformed_descriptor"
    DESCRIPTOR_TOO_LARGE = "descriptor_too_large"
    DYNAMIC_EXPRESSION = "dynamic_expression"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    AMBIGUOUS_MODULE_KIND = "ambiguous_module_kind"
    MODULE_PATH_ESCAPE = "module_path_escape"
    UNSUPPORTED_CONSTRUCT = "unsupported_construct"
    SECRET_REDACTED = "secret_redacted"
    IO_ERROR = "io_error"
    XML_UNSAFE = "xml_unsafe"
    LIMIT_EXCEEDED = "limit_exceeded"


_DRIVE_RE = re.compile(r"^[A-Za-z]:/")
_SECRET_RE = re.compile(
    r"(?:api[_-]?key|secret|token|password|passwd|private[_-]?key|credential)",
    re.IGNORECASE,
)


def normalize_module_path(value: Any) -> str:
    """Normalize a repository-relative module path and reject path escapes."""

    text = str(value or "").strip().replace("\\", "/")
    if not text or text == ".":
        return "."
    if text.startswith("/") or _DRIVE_RE.match(text):
        raise ValueError(f"module path must be repository-relative: {value!r}")
    normalized = posixpath.normpath(text)
    if normalized in {"", "."}:
        return "."
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"module path escapes repository root: {value!r}")
    return normalized.lstrip("./")


def normalize_file_path(value: Any) -> str:
    return normalize_module_path(value)


def stable_module_id(project_id: Any, module_path: Any) -> str:
    scope = project_id_lookup_key(project_id)
    if scope is None:
        raise ValueError("project_id is required for module identity")
    return f"project-module:{scope}:{normalize_module_path(module_path)}"


def stable_fact_id(project_id: Any, kind: str, *parts: Any) -> str:
    scope = project_id_lookup_key(project_id)
    if scope is None:
        raise ValueError("project_id is required for fact identity")
    material = "\x1f".join(
        [scope, str(kind).strip().lower(), *(str(part) for part in parts)]
    )
    return f"{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def safe_summary(value: Any, *, secret_bearing: bool = False, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if secret_bearing or _SECRET_RE.search(text):
        return "[redacted]"
    return text[: max(1, int(limit))]


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class SourceEvidence:
    file_path: str
    start_line: int = 1
    end_line: Optional[int] = None
    source: str = "static"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": normalize_file_path(self.file_path),
            "start_line": max(1, int(self.start_line)),
            "end_line": int(self.end_line) if self.end_line is not None else None,
            "source": self.source,
        }


@dataclass(frozen=True)
class AnalysisDiagnostic:
    code: DiagnosticCode
    message: str
    severity: str = "warning"
    file_path: str = ""
    module_path: str = "."
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": _enum_value(self.code),
            "message": self.message,
            "severity": self.severity,
            "file_path": normalize_file_path(self.file_path) if self.file_path else "",
            "module_path": normalize_module_path(self.module_path),
            "details": _json_value(self.details),
        }


@dataclass(frozen=True)
class DescriptorFact:
    id: str
    project_id: str
    module_path: str
    path: str
    descriptor_type: DescriptorType
    role: DescriptorRole
    parser: str
    parse_depth: ParseDepth
    parser_version: str = ANALYZER_VERSION
    canonical: bool = True
    generated: bool = False
    secret_bearing: bool = False
    redacted: bool = False
    summary: str = ""
    properties: Mapping[str, Any] = field(default_factory=dict)
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    evidence: Tuple[SourceEvidence, ...] = ()
    diagnostics: Tuple[AnalysisDiagnostic, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        module_path: str,
        path: str,
        descriptor_type: DescriptorType,
        role: DescriptorRole,
        parser: str,
        parse_depth: ParseDepth,
        **kwargs: Any,
    ) -> "DescriptorFact":
        normalized_path = normalize_file_path(path)
        return cls(
            id=stable_fact_id(project_id, "project-descriptor", normalized_path),
            project_id=project_id,
            module_path=normalize_module_path(module_path),
            path=normalized_path,
            descriptor_type=descriptor_type,
            role=role,
            parser=parser,
            parse_depth=parse_depth,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class DependencyFact:
    id: str
    project_id: str
    source_module_path: str
    target: str
    scope: DependencyScope = DependencyScope.UNKNOWN
    internal: bool = False
    target_module_path: Optional[str] = None
    source: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    evidence: Tuple[SourceEvidence, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        source_module_path: str,
        target: str,
        scope: DependencyScope = DependencyScope.UNKNOWN,
        **kwargs: Any,
    ) -> "DependencyFact":
        source_path = normalize_module_path(source_module_path)
        return cls(
            id=stable_fact_id(project_id, "project-dependency", source_path, scope, target),
            project_id=project_id,
            source_module_path=source_path,
            target=str(target),
            scope=scope,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class ModuleFact:
    id: str
    project_id: str
    module_path: str
    name: str
    kind: ModuleKind = ModuleKind.UNKNOWN
    languages: Tuple[str, ...] = ()
    frameworks: Tuple[str, ...] = ()
    build_systems: Tuple[str, ...] = ()
    source_roots: Tuple[str, ...] = ()
    descriptor_ids: Tuple[str, ...] = ()
    confidence: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    diagnostics: Tuple[AnalysisDiagnostic, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        module_path: str,
        name: str = "",
        **kwargs: Any,
    ) -> "ModuleFact":
        normalized = normalize_module_path(module_path)
        resolved_name = name or ("root" if normalized == "." else Path(normalized).name)
        return cls(
            id=stable_module_id(project_id, normalized),
            project_id=project_id,
            module_path=normalized,
            name=resolved_name,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class PublicApiFact:
    id: str
    project_id: str
    module_id: str
    symbol_id: str
    name: str
    kind: str
    language: str
    visibility: Visibility
    signature: str = ""
    file_path: str = ""
    start_line: Optional[int] = None
    inferred: bool = False
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    evidence: Tuple[SourceEvidence, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class EndpointFact:
    id: str
    project_id: str
    module_id: str
    protocol: EndpointProtocol
    name: str
    path: str = ""
    method: str = ""
    framework: str = ""
    handler_id: str = ""
    service: str = ""
    request_type: str = ""
    response_type: str = ""
    client_streaming: bool = False
    server_streaming: bool = False
    file_path: str = ""
    start_line: Optional[int] = None
    security: Mapping[str, Any] = field(default_factory=dict)
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    evidence: Tuple[SourceEvidence, ...] = ()
    original_kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class SpecialFileFact:
    descriptor_id: str
    project_id: str
    module_id: str
    path: str
    role: DescriptorRole
    parser: str
    parse_depth: ParseDepth
    status: str = "present"
    framework: str = ""
    canonical: bool = True
    generated: bool = False
    secret_bearing: bool = False
    redacted: bool = False
    safe_summary: str = ""
    freshness: str = "current"
    diagnostics: Tuple[AnalysisDiagnostic, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class FrameworkInstanceFact:
    id: str
    project_id: str
    module_id: str
    framework: str
    version: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    evidence: Tuple[SourceEvidence, ...] = ()
    dimensions: Mapping[str, str] = field(default_factory=dict)
    facts: Mapping[str, Tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    diagnostics: Tuple[AnalysisDiagnostic, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class TopologyAnalysisResult:
    project_id: str
    root: str
    modules: Tuple[ModuleFact, ...] = ()
    descriptors: Tuple[DescriptorFact, ...] = ()
    dependencies: Tuple[DependencyFact, ...] = ()
    public_apis: Tuple[PublicApiFact, ...] = ()
    endpoints: Tuple[EndpointFact, ...] = ()
    special_files: Tuple[SpecialFileFact, ...] = ()
    frameworks: Tuple[FrameworkInstanceFact, ...] = ()
    diagnostics: Tuple[AnalysisDiagnostic, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "analyzer_version": ANALYZER_VERSION,
            "project_id": self.project_id,
            "root": self.root,
            "modules": [item.to_dict() for item in self.modules],
            "descriptors": [item.to_dict() for item in self.descriptors],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "public_apis": [item.to_dict() for item in self.public_apis],
            "endpoints": [item.to_dict() for item in self.endpoints],
            "special_files": [item.to_dict() for item in self.special_files],
            "frameworks": [item.to_dict() for item in self.frameworks],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def graph_rows(self) -> Tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        nodes: list[Dict[str, Any]] = []
        relationships: list[Dict[str, Any]] = []
        for module in self.modules:
            row = module.to_dict()
            row.update(
                {
                    "label": "ProjectModule",
                    "project_id_normalized": project_id_lookup_key(module.project_id),
                }
            )
            nodes.append(row)
        for descriptor in self.descriptors:
            row = descriptor.to_dict()
            row.update(
                {
                    "label": "BuildDescriptor",
                    "project_id_normalized": project_id_lookup_key(descriptor.project_id),
                    "name": Path(descriptor.path).name,
                    "file_path": descriptor.path,
                }
            )
            nodes.append(row)
            relationships.append(
                {
                    "id": stable_fact_id(
                        descriptor.project_id,
                        "topology-edge",
                        stable_module_id(descriptor.project_id, descriptor.module_path),
                        "HAS_DESCRIPTOR",
                        descriptor.id,
                    ),
                    "project_id": descriptor.project_id,
                    "source_label": "ProjectModule",
                    "source_id": stable_module_id(
                        descriptor.project_id, descriptor.module_path
                    ),
                    "type": "HAS_DESCRIPTOR",
                    "target_label": "BuildDescriptor",
                    "target_id": descriptor.id,
                    "file_path": descriptor.path,
                    "confidence": descriptor.confidence.value,
                }
            )
        module_ids = {item.module_path: item.id for item in self.modules}
        for dependency in self.dependencies:
            if dependency.internal and dependency.target_module_path in module_ids:
                relationships.append(
                    {
                        "id": dependency.id,
                        "project_id": dependency.project_id,
                        "source_label": "ProjectModule",
                        "source_id": module_ids[dependency.source_module_path],
                        "type": "DEPENDS_ON",
                        "target_label": "ProjectModule",
                        "target_id": module_ids[dependency.target_module_path],
                        "scope": dependency.scope.value,
                        "source": dependency.source,
                        "confidence": dependency.confidence.value,
                        "file_path": (
                            dependency.evidence[0].file_path
                            if dependency.evidence
                            else ""
                        ),
                    }
                )
        return nodes, relationships


@dataclass(frozen=True)
class DescriptorParseOutput:
    descriptor: DescriptorFact
    dependencies: Tuple[DependencyFact, ...] = ()
    endpoints: Tuple[EndpointFact, ...] = ()
    diagnostics: Tuple[AnalysisDiagnostic, ...] = ()


def deterministic_unique(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


__all__ = [
    "ANALYZER_VERSION",
    "SCHEMA_VERSION",
    "AnalysisDiagnostic",
    "ConfidenceLevel",
    "DependencyFact",
    "DependencyScope",
    "DescriptorParseOutput",
    "DescriptorFact",
    "DescriptorRole",
    "DescriptorType",
    "DiagnosticCode",
    "EndpointFact",
    "EndpointProtocol",
    "FrameworkInstanceFact",
    "ModuleFact",
    "ModuleKind",
    "ParseDepth",
    "PublicApiFact",
    "SourceEvidence",
    "SpecialFileFact",
    "TopologyAnalysisResult",
    "Visibility",
    "deterministic_unique",
    "normalize_file_path",
    "normalize_module_path",
    "safe_summary",
    "stable_fact_id",
    "stable_module_id",
]
