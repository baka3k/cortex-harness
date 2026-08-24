"""Canonical C/C++ function identity shared by structural and semantic planes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable


FUNCTION_IDENTITY_SCHEMA = "cplus-function-v2"


def normalize_syntax(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "").strip())
    value = re.sub(r"\s*([*&(),<>])\s*", r"\1", value)
    return value


@dataclass(frozen=True)
class FunctionIdentity:
    logical_id: str
    canonical_signature: str
    parameter_types: tuple[str, ...]
    qualifiers: str
    template_arity: int
    linkage: str
    legacy_alias: str
    schema_version: str = FUNCTION_IDENTITY_SCHEMA


def build_function_identity(
    *,
    qualified_name: str,
    parameter_types: Iterable[str],
    qualifiers: str = "",
    template_arity: int = 0,
    linkage: str = "external",
    rel_path: str = "",
    start_byte: int = 0,
    parseable: bool = True,
) -> FunctionIdentity:
    """Build an overload-safe logical identity without publication scope.

    Project and generation are physical storage coordinates and deliberately
    stay outside this logical identity. Repository-relative path participates
    only for internal linkage; a normalized span is a last-resort suffix for
    anonymous or unparseable declarations.
    """

    qualified = normalize_syntax(qualified_name) or "<anonymous>"
    params = tuple(normalize_syntax(value) or "?" for value in parameter_types)
    normalized_qualifiers = normalize_syntax(qualifiers)
    normalized_linkage = normalize_syntax(linkage).lower() or "external"
    discriminator = ""
    if normalized_linkage in {"internal", "unique_external", "no_linkage"}:
        discriminator = str(rel_path or "").replace("\\", "/")
    if not parseable or qualified == "<anonymous>":
        discriminator = f"{discriminator}#byte:{max(0, int(start_byte))}"
    canonical = {
        "qualified_name": qualified,
        "parameter_types": params,
        "qualifiers": normalized_qualifiers,
        "template_arity": max(0, int(template_arity)),
        "linkage": normalized_linkage,
        "discriminator": discriminator,
    }
    encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    signature = (
        f"{qualified}({','.join(params)})"
        f"|qualifiers:{normalized_qualifiers or '-'}"
        f"|template:{canonical['template_arity']}"
        f"|linkage:{normalized_linkage}"
        f"|discriminator:{discriminator or '-'}"
    )
    legacy = f"{qualified}/{len(params)}@{str(rel_path or '').replace(chr(92), '/')}"
    return FunctionIdentity(
        logical_id=f"{FUNCTION_IDENTITY_SCHEMA}::{qualified}::{digest}",
        canonical_signature=signature,
        parameter_types=params,
        qualifiers=normalized_qualifiers,
        template_arity=canonical["template_arity"],
        linkage=normalized_linkage,
        legacy_alias=legacy,
    )
