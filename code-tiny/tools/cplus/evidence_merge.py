"""Deterministic Phase-04 evidence merge for C/C++/Pro*C call observations.

Merges Tree-sitter lexical candidates, Clang semantic observations, build
configurations, and coverage records by stable callsite/evidence identity —
never by callee name alone.  Exact repeated observations are deduplicated
without erasing provenance; contradictory but valid build configurations
coexist.  Compatibility strict ``CALLS`` rows are derived here, only from
accepted ``direct_resolved`` evidence, and stay linked to their stable
site/evidence identities so the derived edge can never become the authority.

Pro*C reconciliation joins the original SQL region's enclosing lexical
function with the mapped Clang semantic function, preserving ambiguity
rather than choosing by name or line proximity, and resolves host/indicator
declarations only when unique.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, AbstractSet

from tools.common.call_evidence import (
    CALL_EVIDENCE_SCHEMA_VERSION,
    COVERAGE_STATUSES,
    PROC_RELATION_TYPES,
    RESOLUTION_CLASS_DIRECT_RESOLVED,
    RESOLUTION_CLASS_LEXICAL_CANDIDATE,
    RESOLUTION_CLASS_UNRESOLVED,
    RESOLUTION_CLASSES,
    SEMANTIC_PROVIDERS,
    SemanticCoverageRecord,
    callsite_site_id,
    frontier_coverage,
    is_strong_call_evidence,
    logical_callsite_id,
    normalize_call_row,
)

EVIDENCE_MERGE_SCHEMA_VERSION = "2"


def callsite_node_id(
    caller_id: str,
    file_path: str,
    line: int,
    column: int,
    call_type: str,
    *,
    spelling_offset: int | None = None,
    expansion_offset: int | None = None,
    ordinal: int = 0,
) -> str:
    """Stable identity of the ``CallSite`` staging node.

    Deliberately callee-independent: a lexical candidate and a semantic
    observation of the same source position merge onto one CallSite.  The
    callee identity lives on each observation (``OBSERVED_AS``/``RESOLVES_TO``).
    """

    if spelling_offset is not None:
        return logical_callsite_id(
            caller_id=caller_id,
            file_path=file_path,
            spelling_offset=spelling_offset,
            expansion_offset=expansion_offset,
            ordinal=ordinal,
            call_type=call_type,
        )
    key = f"{caller_id}:{file_path}:{int(line)}:{int(column)}:{call_type}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _observation_site_parts(row: Mapping[str, Any]) -> tuple[str, str, int, int, str]:
    caller_id = str(row.get("caller_id") or row.get("caller_symbol_id") or "")
    file_path = str(row.get("file_path") or row.get("file") or "")
    line = int(row.get("line") or row.get("start_line") or 0)
    column = int(row.get("column") or row.get("start_column") or 0)
    call_type = str(row.get("call_type") or "call")
    if not caller_id or not file_path or line <= 0:
        raise ValueError(
            "call observations require caller identity and source position: "
            f"{caller_id!r}@{file_path!r}:{line}:{column}"
        )
    return caller_id, file_path, line, column, call_type


def evidence_identity(row: Mapping[str, Any]) -> str:
    """Stable identity of one deduplicated observation.

    Includes provider, resolution class, configuration fingerprint, and the
    semantic callee identity when present.  Two observations with the same
    identity are exact repeats and collapse; two observations that differ in
    configuration are distinct evidence that must coexist.
    """

    normalized = normalize_call_row(row)
    payload = {
        "schema_version": CALL_EVIDENCE_SCHEMA_VERSION,
        "site": list(_observation_site_parts(normalized)),
        "callee_id": str(normalized.get("callee_id") or normalized.get("callee_symbol_id") or ""),
        "callee_usr": str(normalized.get("callee_usr") or ""),
        "resolution_class": normalized.get("resolution_class"),
        "semantic_provider": normalized.get("semantic_provider"),
        "tu_key": str(normalized.get("tu_key") or ""),
        "config_fingerprint": str(normalized.get("config_fingerprint") or ""),
        "generated_code_class": str(normalized.get("generated_code_class") or ""),
        "source_map_quality": str(normalized.get("source_map_quality") or ""),
    }
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Strength ordering used to summarize a site's observations on the staging
# node.  Only an accepted strong observation yields strict CALLS; this is a
# reporting summary, not a promotion path.
_CLASS_STRENGTH = {
    RESOLUTION_CLASS_LEXICAL_CANDIDATE: 0,
    "constructor_call": 1,
    "dependent_template_call": 2,
    "indirect_callsite": 3,
    "declared_virtual_target": 4,
    "possible_dispatch_target": 5,
    RESOLUTION_CLASS_UNRESOLVED: 6,
    RESOLUTION_CLASS_DIRECT_RESOLVED: 7,
}


@dataclass
class MergedCallSite:
    """One callsite with all deduplicated observations and derived views."""

    site_id: str
    caller_id: str
    file_path: str
    line: int
    column: int
    call_type: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    project_id: str = ""

    @property
    def configs(self) -> list[str]:
        seen: list[str] = []
        for obs in self.observations:
            fingerprint = str(obs.get("config_fingerprint") or "")
            if fingerprint and fingerprint not in seen:
                seen.append(fingerprint)
        return seen

    @property
    def accepted_observation(self) -> dict[str, Any] | None:
        """The single accepted strong direct observation, if any.

        Only an approved semantic provider emitting ``direct_resolved`` with
        complete identity counts.  If two valid configurations disagree on
        the callee there is no single accepted observation and strict
        derivation stops (configuration policy must narrow the frontier).
        """

        accepted = [
            obs
            for obs in self.observations
            if is_strong_call_evidence(obs)
        ]
        callees = {
            (obs.get("callee_id") or obs.get("callee_symbol_id"), obs.get("callee_usr"))
            for obs in accepted
        }
        if len(callees) != 1:
            return None
        return accepted[0] if accepted else None

    @property
    def resolved_class(self) -> str:
        """Summary class for the staging node: the strongest observed class.

        The strict CALLS derivation still uses ``accepted_observation`` only;
        this summary never promotes weak evidence.
        """

        if self.accepted_observation is not None:
            return RESOLUTION_CLASS_DIRECT_RESOLVED
        return max(
            (
                str(obs.get("resolution_class") or RESOLUTION_CLASS_LEXICAL_CANDIDATE)
                for obs in self.observations
            ),
            key=lambda cls: _CLASS_STRENGTH.get(cls, 0),
        )

    def to_writer_rows(self, accepted_function_ids: AbstractSet[str] | None = None) -> Dict[str, Any]:
        """Adapter for ``write_call_evidence_sites``.

        Returns the ``{site_id, caller_id, callee_id, resolution_class,
        props}`` row shape the writer consumes; props exclude the nested
        observation list and non-scalar values so both graph providers can
        store them.  When the accepted function identities of the staged set
        are supplied, observations whose callee is not part of the staged
        generation are exposed as ``dangling_observation_ids`` on the site:
        the evidence stays queryable on the staging plane without inventing
        graph endpoints.
        """

        staging = self.to_staging_row()
        props = {
            key: value
            for key, value in staging.items()
            if isinstance(value, (str, int, float, bool))
        }
        props["config_fingerprints"] = ",".join(self.configs)
        dangling = self.dangling_observation_ids(accepted_function_ids)
        if dangling:
            props["dangling_observation_ids"] = dangling
        return {
            "site_id": self.site_id,
            "caller_id": self.caller_id,
            "callee_id": staging["callee_id"],
            "resolution_class": staging["resolution_class"],
            "props": props,
        }

    def dangling_observation_ids(
        self, accepted_function_ids: AbstractSet[str] | None = None
    ) -> list[str]:
        """Evidence ids whose callee is not part of the staged identities.

        Without an accepted-identity set nothing can be declared dangling:
        endpoint resolution is then the writer's required-endpoint contract.
        """

        if accepted_function_ids is None:
            return []
        dangling: list[str] = []
        for observation in self.observations:
            callee_id = str(
                observation.get("callee_id") or observation.get("callee_symbol_id") or ""
            )
            if callee_id and callee_id not in accepted_function_ids:
                evidence_id = str(observation.get("evidence_id") or "")
                if evidence_id:
                    dangling.append(evidence_id)
        return dangling

    def observation_writer_rows(
        self, accepted_function_ids: AbstractSet[str] | None = None
    ) -> list[dict[str, Any]]:
        """Adapter for ``write_call_evidence_observations``.

        One row per deduplicated observation carrying its stable evidence
        identity, site, callee, and scalar contract props.  Rows whose callee
        is not part of the staged identities are flagged ``dangling`` — the
        observation writer skips them for edges because their evidence is
        already persisted on the site props.
        """

        rows: list[dict[str, Any]] = []
        for observation in self.observations:
            callee_id = str(
                observation.get("callee_id") or observation.get("callee_symbol_id") or ""
            )
            row = {
                key: value
                for key, value in observation.items()
                if key not in {"_repeat_runs", "caller_id", "props"}
                and isinstance(value, (str, int, float, bool))
            }
            row["site_id"] = self.site_id
            row["callee_id"] = callee_id
            row["evidence_id"] = str(observation.get("evidence_id") or "")
            row["dangling"] = bool(
                accepted_function_ids is not None
                and callee_id
                and callee_id not in accepted_function_ids
            )
            rows.append(row)
        return rows

    def to_staging_row(self) -> dict[str, Any]:
        accepted = self.accepted_observation
        return {
            "site_id": self.site_id,
            "caller_id": self.caller_id,
            "callee_id": (accepted or {}).get("callee_id")
            or (accepted or {}).get("callee_symbol_id")
            or "",
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "call_type": self.call_type,
            "resolution_class": self.resolved_class,
            "observation_count": len(self.observations),
            "config_fingerprints": self.configs,
            "accepted": accepted is not None,
            "project_id": self.project_id,
            "schema_version": EVIDENCE_MERGE_SCHEMA_VERSION,
        }


@dataclass
class EvidenceMergeResult:
    """Outcome of one deterministic merge pass."""

    call_sites: list[MergedCallSite] = field(default_factory=list)
    strict_call_rows: list[dict[str, Any]] = field(default_factory=list)
    coverage_records: list[dict[str, Any]] = field(default_factory=list)
    duplicates_collapsed: int = 0
    observations_in: int = 0
    configuration_count: int = 0
    project_id: str = ""
    revision: str = ""
    accepted_function_ids: AbstractSet[str] | None = None

    @property
    def frontier(self) -> dict[str, Any]:
        return frontier_coverage(self.coverage_records)

    def site_writer_rows(self) -> list[dict[str, Any]]:
        """Rows for ``write_call_evidence_sites`` (dangling evidence on props)."""

        return [site.to_writer_rows(self.accepted_function_ids) for site in self.call_sites]

    def observation_writer_rows(self) -> list[dict[str, Any]]:
        """Rows for ``write_call_evidence_observations`` (dangling flagged)."""

        rows: list[dict[str, Any]] = []
        for site in self.call_sites:
            rows.extend(site.observation_writer_rows(self.accepted_function_ids))
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EVIDENCE_MERGE_SCHEMA_VERSION,
            "project_id": self.project_id,
            "revision": self.revision,
            "call_site_count": len(self.call_sites),
            "strict_call_count": len(self.strict_call_rows),
            "observations_in": self.observations_in,
            "duplicates_collapsed": self.duplicates_collapsed,
            "configuration_count": self.configuration_count,
            "frontier": self.frontier,
        }


def _normalized_config(row: Mapping[str, Any]) -> dict[str, str]:
    return {
        "config_fingerprint": str(row.get("config_fingerprint") or ""),
        "project_id": str(row.get("project_id") or ""),
        "tu_key": str(row.get("tu_key") or ""),
        "compiler": str(row.get("compiler") or ""),
        "target": str(row.get("target") or ""),
        "sysroot": str(row.get("sysroot") or ""),
    }


def merge_call_evidence(
    observations: Iterable[Mapping[str, Any]],
    *,
    coverage_records: Iterable[Mapping[str, Any] | SemanticCoverageRecord] = (),
    configurations: Iterable[Mapping[str, Any]] = (),
    project_id: str = "",
    revision: str = "",
    accepted_function_ids: AbstractSet[str] | None = None,
) -> EvidenceMergeResult:
    """Deterministically merge observations, coverage, and configurations.

    - Every row is classified under the call-evidence contract (fail closed
      on unknown classes).
    - Exact repeats (same evidence identity) collapse; provenance of each
      repeat is retained as ``observation_count`` plus the distinct runs.
    - Contradictory configurations coexist: identity includes the config
      fingerprint, so one configuration can never erase another's evidence.
    - Strict compatibility CALLS rows are derived only from a single,
      unambiguous accepted ``direct_resolved`` observation and carry the
      site/evidence identity so the edge stays traceable to its evidence.
    - ``accepted_function_ids`` are the staged generation's accepted
      ``Function`` identities; observations pointing outside them keep their
      evidence on the staging plane as dangling observations instead of
      inventing graph endpoints.
    """

    result = EvidenceMergeResult(
        project_id=project_id, revision=revision, accepted_function_ids=accepted_function_ids
    )

    identities: dict[str, dict[str, Any]] = {}
    sites: dict[str, MergedCallSite] = {}
    for raw in observations:
        result.observations_in += 1
        row = dict(raw)
        normalized = normalize_call_row(row)
        if normalized.get("resolution_class") not in RESOLUTION_CLASSES:
            raise ValueError(
                f"unknown call resolution class: {normalized.get('resolution_class')!r}"
            )
        # Fail closed exactly like the writer gate: a claimed direct_resolved
        # observation without an approved provider and complete identity is a
        # contract violation, not weak evidence to be downgraded silently.
        if normalized.get("resolution_class") == RESOLUTION_CLASS_DIRECT_RESOLVED and not is_strong_call_evidence(normalized):
            raise ValueError(
                "direct_resolved observation refused: requires an approved semantic "
                "provider and complete identity fields"
            )
        # Project isolation: observations from another project never merge.
        row_project = str(normalized.get("project_id") or project_id)
        if project_id and row_project and row_project != project_id:
            raise ValueError(
                f"cross-project observation refused: {row_project!r} != {project_id!r}"
            )
        normalized["project_id"] = row_project or project_id
        identity = evidence_identity(normalized)
        existing = identities.get(identity)
        if existing is not None:
            # Exact repeat: deduplicate, retain provenance of every run.
            result.duplicates_collapsed += 1
            runs = set(existing.get("_repeat_runs") or [])
            runs.add(str(existing.get("parse_run_id") or ""))
            runs.add(str(normalized.get("parse_run_id") or ""))
            existing["_repeat_runs"] = sorted(run for run in runs if run)
            existing["observation_count"] = int(existing.get("observation_count") or 1) + 1
            continue
        normalized["evidence_id"] = identity
        normalized.setdefault("observation_count", 1)
        identities[identity] = normalized

        caller_id, file_path, line, column, call_type = _observation_site_parts(normalized)
        raw_spelling_offset = normalized.get("spelling_start_byte")
        if raw_spelling_offset is None:
            raw_spelling_offset = normalized.get("call_start_byte")
        site_id = callsite_node_id(
            caller_id,
            file_path,
            line,
            column,
            call_type,
            spelling_offset=(
                int(raw_spelling_offset) if raw_spelling_offset is not None else None
            ),
            expansion_offset=(
                int(normalized.get("expansion_start_byte"))
                if normalized.get("expansion_start_byte") is not None
                else None
            ),
            ordinal=int(normalized.get("call_ordinal") or 0),
        )
        site = sites.get(site_id)
        if site is None:
            site = MergedCallSite(
                site_id=site_id,
                caller_id=caller_id,
                file_path=file_path,
                line=line,
                column=column,
                call_type=call_type,
                project_id=normalized["project_id"],
            )
            sites[site_id] = site
        site.observations.append(normalized)

    result.call_sites = sorted(sites.values(), key=lambda s: (s.file_path, s.line, s.column, s.caller_id))

    # Compatibility strict CALLS derivation.  One derivation path only: the
    # accepted observation on the CallSite.  Rows carry site/evidence ids so
    # the materialized edge can never outrank its evidence.
    for site in result.call_sites:
        accepted = site.accepted_observation
        if accepted is None:
            continue
        props = {
            key: value
            for key, value in accepted.items()
            if key not in {"caller_id", "callee_id", "site_id", "props", "_repeat_runs"}
            and value not in (None, [], {})
        }
        props.update(
            {
                "resolution_class": RESOLUTION_CLASS_DIRECT_RESOLVED,
                "evidence_id": accepted.get("evidence_id"),
                "site_id": site.site_id,
                "project_id": site.project_id,
                "merge_schema_version": EVIDENCE_MERGE_SCHEMA_VERSION,
                "observation_count": accepted.get("observation_count"),
            }
        )
        callee_id = accepted.get("callee_id") or accepted.get("callee_symbol_id")
        result.strict_call_rows.append(
            {
                "caller_id": site.caller_id,
                "callee_id": callee_id,
                "site_id": callsite_site_id(
                    site.caller_id,
                    str(callee_id),
                    site.file_path,
                    site.line,
                    site.column,
                    site.call_type,
                ),
                "props": props,
            }
        )

    seen_configs: dict[str, dict[str, str]] = {}
    for config in configurations:
        normalized = _normalized_config(config)
        if not normalized["config_fingerprint"]:
            continue
        if project_id:
            config_project = normalized["project_id"]
            if config_project and config_project != project_id:
                raise ValueError(
                    f"cross-project configuration refused: {config_project!r} != {project_id!r}"
                )
            normalized["project_id"] = normalized["project_id"] or project_id
        seen_configs.setdefault(normalized["config_fingerprint"], normalized)
    result.configuration_count = len(seen_configs)

    for record in coverage_records:
        if isinstance(record, SemanticCoverageRecord):
            result.coverage_records.append(record.to_dict())
        else:
            status = str((record or {}).get("status") or "")
            if status and status not in COVERAGE_STATUSES:
                raise ValueError(f"unknown coverage status: {status!r}")
            result.coverage_records.append(dict(record or {}))

    return result


# ---------------------------------------------------------------------------
# Pro*C original-source reconciliation
# ---------------------------------------------------------------------------

_JOIN_UNRESOLVED = "unresolved"
_JOIN_UNIQUE = "unique"
_JOIN_AMBIGUOUS = "ambiguous"
_JOIN_CROSS_CONFIG = "cross_config"


def _span_overlaps(
    left: tuple[int, int], right: tuple[int, int]
) -> bool:
    return left[0] <= right[1] and right[0] <= left[1]


def merge_proc_function_joins(
    sql_regions: Iterable[Mapping[str, Any]],
    *,
    project_id: str = "",
) -> list[dict[str, Any]]:
    """Reconcile each SQL region's enclosing function with semantic identity.

    Each region carries its lexical enclosing function plus the semantic
    functions mapped to the same original span (already produced by the
    source-map lane).  A unique covering semantic function yields a
    ``unique`` join; multiple candidates are retained as ``ambiguous``
    (never selected by name or line proximity); none yields ``unresolved``
    and keeps the lexical function id.
    """

    joins: list[dict[str, Any]] = []
    for region in sql_regions:
        statement_id = str(region.get("statement_id") or "")
        if not statement_id:
            raise ValueError("proc SQL regions require statement_id")
        lexical_function_id = str(region.get("enclosing_function_id") or "")
        semantic_candidates = [
            {
                "function_id": str(candidate.get("function_id") or ""),
                "bundle_id": str(candidate.get("bundle_id") or ""),
                "source_map_quality": str(candidate.get("source_map_quality") or ""),
                "config_fingerprint": str(candidate.get("config_fingerprint") or ""),
            }
            for candidate in (region.get("semantic_candidates") or [])
            if str(candidate.get("function_id") or "").strip()
        ]
        function_ids = sorted({candidate["function_id"] for candidate in semantic_candidates})
        if not function_ids:
            join_quality = _JOIN_UNRESOLVED
            target_function_id = lexical_function_id
        elif len(function_ids) == 1:
            join_quality = _JOIN_UNIQUE
            target_function_id = function_ids[0]
        else:
            join_quality = _JOIN_AMBIGUOUS
            target_function_id = lexical_function_id or function_ids[0]

        joins.append(
            {
                "statement_id": statement_id,
                "function_id": target_function_id,
                "lexical_function_id": lexical_function_id,
                "semantic_function_ids": function_ids,
                "join_quality": join_quality,
                "source_map_quality": str(region.get("source_map_quality") or ""),
                "bundle_id": str(region.get("bundle_id") or ""),
                "is_dynamic_sql": bool(region.get("is_dynamic_sql")),
                "project_id": project_id,
                "schema_version": EVIDENCE_MERGE_SCHEMA_VERSION,
            }
        )
    return joins


def resolve_proc_host_declarations(
    host_variables: Iterable[Mapping[str, Any]],
    *,
    project_id: str = "",
) -> list[dict[str, Any]]:
    """Resolve host/indicator variables to C declarations when unique.

    Each host variable carries candidate declarations with configuration
    provenance.  Exactly one candidate (globally, or one per configuration
    agreeing across configurations) yields ``unique``; same name resolving
    to different declarations under different configurations yields
    ``cross_config`` joins retained per configuration; no candidate yields
    ``unresolved``.  ``BINDS_PARAMETER`` is untouched by this join.
    """

    rows: list[dict[str, Any]] = []
    for host in host_variables:
        host_variable_id = str(host.get("host_variable_id") or host.get("id") or "")
        if not host_variable_id:
            raise ValueError("proc host variables require host_variable_id")
        candidates = [
            {
                "declaration_id": str(candidate.get("declaration_id") or ""),
                "declaration_kind": str(candidate.get("declaration_kind") or ""),
                "config_fingerprint": str(candidate.get("config_fingerprint") or ""),
            }
            for candidate in (host.get("candidates") or [])
            if str(candidate.get("declaration_id") or "").strip()
        ]
        declaration_ids = sorted({candidate["declaration_id"] for candidate in candidates})
        by_config: dict[str, set[str]] = {}
        for candidate in candidates:
            by_config.setdefault(candidate["config_fingerprint"], set()).add(
                candidate["declaration_id"]
            )
        is_indicator = bool(host.get("is_indicator"))

        def row_for(declaration_id: str, join_quality: str) -> dict[str, Any]:
            return {
                "host_variable_id": host_variable_id,
                "declaration_id": declaration_id,
                "declaration_kind": next(
                    (
                        candidate["declaration_kind"]
                        for candidate in candidates
                        if candidate["declaration_id"] == declaration_id
                    ),
                    "",
                ),
                "join_quality": join_quality,
                "is_indicator": is_indicator,
                "project_id": project_id,
                "schema_version": EVIDENCE_MERGE_SCHEMA_VERSION,
            }

        if not declaration_ids:
            rows.append(
                {
                    "host_variable_id": host_variable_id,
                    "declaration_id": "",
                    "declaration_kind": "",
                    "join_quality": _JOIN_UNRESOLVED,
                    "is_indicator": is_indicator,
                    "project_id": project_id,
                    "schema_version": EVIDENCE_MERGE_SCHEMA_VERSION,
                }
            )
        elif len(declaration_ids) == 1:
            rows.append(row_for(declaration_ids[0], _JOIN_UNIQUE))
        elif len(by_config) == 1:
            # Multiple declarations inside one configuration: ambiguous.
            for declaration_id in declaration_ids:
                rows.append(row_for(declaration_id, _JOIN_AMBIGUOUS))
        else:
            # Valid but contradictory configurations: retain each join,
            # qualified by its configuration, without choosing a winner.
            for candidate in sorted(candidates, key=lambda c: (c["config_fingerprint"], c["declaration_id"])):
                row = row_for(candidate["declaration_id"], _JOIN_CROSS_CONFIG)
                row["config_fingerprint"] = candidate["config_fingerprint"]
                rows.append(row)
    return rows


def proc_data_impact_coverage(joins: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Coverage block for a Pro*C data-impact frontier.

    Dynamic SQL, ambiguous function joins, and unresolved host declarations
    make the frontier ``partial`` even when the enclosing direct C call is
    semantically resolved.
    """

    reasons: list[str] = []
    total = 0
    for join in joins:
        total += 1
        statement = str(join.get("statement_id") or join.get("host_variable_id") or "join")
        if join.get("is_dynamic_sql"):
            reasons.append(f"{statement}: dynamic_sql (table targets cannot be enumerated)")
        quality = str(join.get("join_quality") or "")
        if quality == _JOIN_AMBIGUOUS:
            reasons.append(f"{statement}: ambiguous_function_join")
        elif quality == _JOIN_UNRESOLVED:
            reasons.append(f"{statement}: unresolved_function_join")
    status = "complete" if (total > 0 and not reasons) else ("partial" if reasons else "unknown")
    return {
        "status": status,
        "reasons": reasons,
        "join_count": total,
        "evidence_relation_types": sorted(PROC_RELATION_TYPES),
    }
