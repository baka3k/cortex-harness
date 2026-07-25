"""Frozen public contracts and compatibility mapping for topology context."""

from __future__ import annotations

from types import MappingProxyType


CONTRACT_VERSION = 1

COMPATIBILITY_MAP = MappingProxyType(
    {
        "GradleModule": {
            "canonical_label": "ProjectModule",
            "identity": "project_id_normalized + normalized module_path",
            "strategy": (
                "use the stable ProjectModule identity, add the GradleModule "
                "compatibility label, and link any legacy module:: identity "
                "with SAME_MODULE"
            ),
            "cleanup_owner": "project_topology",
            "destructive_migration": False,
        },
        "AndroidManifest": {
            "canonical_label": "BuildDescriptor",
            "strategy": "retain specialized node and link it to ProjectModule",
            "cleanup_owner": "android",
            "destructive_migration": False,
        },
        "AndroidComponent": {
            "canonical_label": "AndroidComponent",
            "strategy": "retain specialized node and add module containment",
            "cleanup_owner": "android",
            "destructive_migration": False,
        },
        "ApiEndpoint": {
            "canonical_label": "ApiEndpoint",
            "strategy": "normalize at query time; preserve original identity",
            "cleanup_owner": "framework_overlay",
            "destructive_migration": False,
        },
        "HttpEndpoint": {
            "canonical_label": "HttpEndpoint",
            "strategy": "normalize at query time; preserve original identity",
            "cleanup_owner": "framework_overlay",
            "destructive_migration": False,
        },
        "MyBatisModule": {
            "canonical_label": "FrameworkInstance",
            "strategy": "link existing specialized facts to ProjectModule",
            "cleanup_owner": "mybatis",
            "destructive_migration": False,
        },
    }
)

CONTEXT_TOOL_CONTRACTS = MappingProxyType(
    {
        "get_project_modules": {
            "scope": ("project_id",),
            "filters": ("module_id", "module_path"),
            "pagination": ("offset", "limit"),
            "required_schema": ("ProjectModule", "HAS_DESCRIPTOR", "DEPENDS_ON"),
            "result_key": "modules",
        },
        "get_public_apis": {
            "scope": ("project_id",),
            "filters": ("module_id", "symbol_kinds", "language", "include_inferred"),
            "pagination": ("offset", "limit"),
            "required_schema": ("ProjectModule", "EXPOSES_API"),
            "result_key": "public_apis",
        },
        "get_endpoints": {
            "scope": ("project_id",),
            "filters": ("module_id", "protocol", "framework", "http_method", "query"),
            "pagination": ("offset", "limit"),
            "required_schema": ("ProjectModule", "EXPOSES_ENDPOINT"),
            "result_key": "endpoints",
        },
        "get_module_architecture_summary": {
            "scope": ("project_id", "module_id|all_modules"),
            "filters": ("detail_level", "item_limit"),
            "pagination": (),
            "required_schema": ("ProjectModule",),
            "result_key": "summary",
        },
        "get_project_special_files": {
            "scope": ("project_id",),
            "filters": (
                "module_id",
                "role",
                "parser",
                "framework",
                "parse_depth",
                "status",
            ),
            "pagination": ("offset", "limit"),
            "required_schema": ("ProjectModule", "BuildDescriptor", "HAS_DESCRIPTOR"),
            "result_key": "special_files",
        },
        "get_framework_context": {
            "scope": ("project_id",),
            "filters": ("module_id", "framework", "dimensions"),
            "pagination": ("offset", "limit"),
            "required_schema": ("ProjectModule", "FrameworkInstance", "USES_FRAMEWORK"),
            "result_key": "frameworks",
        },
    }
)

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
DEFAULT_SUMMARY_SAMPLE_LIMIT = 10
MAX_SUMMARY_SAMPLE_LIMIT = 50


__all__ = [
    "COMPATIBILITY_MAP",
    "CONTEXT_TOOL_CONTRACTS",
    "CONTRACT_VERSION",
    "DEFAULT_PAGE_LIMIT",
    "DEFAULT_SUMMARY_SAMPLE_LIMIT",
    "MAX_PAGE_LIMIT",
    "MAX_SUMMARY_SAMPLE_LIMIT",
]
