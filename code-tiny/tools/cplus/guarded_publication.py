"""Phase 06 guarded publication, staged replacement, and rollback.

This module integrates the Phase 01–05 evidence contract with the owning
graph-journal, parser-quality, and store-concurrency contracts.  It does not
add a second writer, journal, scheduler, or generation manager: evidence
writes are compiled through the canonical schema/relationship contracts and
the durable mutation journal, and graph/vector generations are published
atomically through the concurrency owner's ``publish(manifest, validate)``
boundary.

Publication requires both trust dimensions:

- parse trust: the file's quality tier and evidence policy permit strong
  relations (parser-quality owner contract);
- semantic trust: the call-evidence contract, the Pro*C bundle state, and
  the source-map quality permit the requested edge class.

Failure in either dimension downgrades or quarantines evidence *before*
writes; it never converts to an empty successful strict graph.  Pro*C
publication carries two independently validated sub-results in one staged
replacement set: ``sql_original_result`` and ``semantic_mapped_result``.
SQL facts may publish while the semantic lane is missing or rejected, and
mapped calls may publish while SQL grammar enrichment is unavailable — but
no stale strong edge survives a downgrade, deletion, or context change.

Consumers:
- ``tools/cplus/cplus_analyzer.py`` sanitizes Pro*C vector items and exposes
  publication status.
- The Phase 07 pilot orchestrator drives staged replacement and atomic
  publication through this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.common.call_evidence import (
    PROC_NODE_LABELS,
    RESOLUTION_CLASS_DIRECT_RESOLVED,
    is_strong_call_evidence,
)
from tools.common.reliability import (
    FailureClass,
    FailureRecord,
    RunOutcome,
    RunPhase,
)
from tools.cplus.proc_source_map import (
    bundle_state_allows_strict_calls,
    is_strict_map_quality,
)

GUARDED_PUBLICATION_SCHEMA_VERSION = "1"
SEMANTIC_PUBLICATION_POLICY_VERSION = "semantic-publication-v1"

# Publication modes.  ``gated`` is the only mode that may publish semantic
# strong evidence; ``off`` is containment (weak evidence only); ``rollback``
# selects the last valid semantic generation without reparsing source.
SEMANTIC_PUBLICATION_MODES = frozenset({"gated", "off", "rollback"})
ENV_SEMANTIC_PUBLICATION_MODE = "CORTEX_SEMANTIC_PUBLICATION_MODE"

# Parse-quality tiers from which strict CALLS evidence may publish.  The
# parser-quality owner keeps ``quarantined`` (and unknown tiers) closed.
PARSE_TIERS_ALLOWING_STRONG = frozenset({"clean", "recovered"})

# Generated-code classes that must never reach embeddings or reports.
VECTOR_FORBIDDEN_GENERATED_CLASSES = frozenset(
    {
        "precompiler_wrapper",
        "precompiler_runtime",
        "generated_declaration",
        "unmapped_generated",
    }
)

# Bounded credential markers for vector/report scrubbing.  Precompiler
# command lines and connect descriptors must never be embedded; matching is
# deliberately coarse and fails closed (drop the item, keep the accounting).
_CREDENTIAL_MARKERS = (
    "password=",
    "passwd=",
    "identified by",
    "user id=",
    "userid=",
    "connect_data",
    "oracle_sid",
    "sqlca.connect",
)


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def secret_bearing_text(text: str) -> str | None:
    """Return the first credential marker found in ``text``, if any."""

    lowered = str(text or "").lower()
    for marker in _CREDENTIAL_MARKERS:
        if marker in lowered:
            return marker
    return None


# ---------------------------------------------------------------------------
# Policy and the two-dimension publication gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticPublicationPolicy:
    """Guarded-publication configuration for one project/scope."""

    mode: str = "gated"
    policy_version: str = SEMANTIC_PUBLICATION_POLICY_VERSION
    strong_relation_types: tuple[str, ...] = ("CALLS",)
    parse_tiers_allowing_strong: frozenset[str] = PARSE_TIERS_ALLOWING_STRONG

    def __post_init__(self) -> None:
        if self.mode not in SEMANTIC_PUBLICATION_MODES:
            raise ValueError(f"unknown semantic publication mode: {self.mode!r}")
        if not any(str(relation).strip() for relation in self.strong_relation_types):
            raise ValueError("policy requires at least one strong relation type")
        object.__setattr__(
            self, "parse_tiers_allowing_strong", frozenset(self.parse_tiers_allowing_strong)
        )

    @property
    def semantic_publication_allowed(self) -> bool:
        return self.mode == "gated"

    def with_environment(self, env: Mapping[str, str] | None = None) -> "SemanticPublicationPolicy":
        """Apply the operator's environment override (rollback/off switch)."""

        environment = os.environ if env is None else env
        raw_mode = str(environment.get(ENV_SEMANTIC_PUBLICATION_MODE) or "").strip().lower()
        if not raw_mode:
            return self
        if raw_mode not in SEMANTIC_PUBLICATION_MODES:
            raise ValueError(
                f"{ENV_SEMANTIC_PUBLICATION_MODE} must be one of "
                f"{sorted(SEMANTIC_PUBLICATION_MODES)}: {raw_mode!r}"
            )
        return SemanticPublicationPolicy(
            mode=raw_mode,
            policy_version=self.policy_version,
            strong_relation_types=self.strong_relation_types,
            parse_tiers_allowing_strong=self.parse_tiers_allowing_strong,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GUARDED_PUBLICATION_SCHEMA_VERSION,
            "mode": self.mode,
            "policy_version": self.policy_version,
            "strong_relation_types": list(self.strong_relation_types),
            "parse_tiers_allowing_strong": sorted(self.parse_tiers_allowing_strong),
            "semantic_publication_allowed": self.semantic_publication_allowed,
        }


@dataclass(frozen=True)
class PublicationGateDecision:
    """Outcome of composing the parse-trust and semantic-trust gates."""

    allowed: bool
    reason: str
    parse_trust: str  # clean | recovered | retry_required | quarantined | unknown
    semantic_trust: str  # strong | weak | none

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "parse_trust": self.parse_trust,
            "semantic_trust": self.semantic_trust,
        }


def parse_trust_for(
    file_quality: Mapping[str, Any] | None,
    evidence_policy: Mapping[str, Any] | None,
) -> tuple[str, str | None]:
    """Parse-quality trust for one file under the parser-quality contract.

    Returns ``(tier, blocking_reason)``.  The tier comes from the attached
    quality provenance; an explicit ``strong_relations_allowed is False``
    policy blocks exactly like a quarantined tier, and unknown tiers fail
    closed.
    """

    quality = dict(file_quality or {})
    tier = str(quality.get("tier") or "").strip().lower()
    if not tier:
        tier = "unknown"
    policy = dict(evidence_policy or {})
    if policy.get("strong_relations_allowed") is False:
        return tier, "evidence_policy_forbids_strong_relations"
    if tier == "quarantined":
        return tier, "parse_quality_quarantined"
    if tier == "unknown":
        return tier, "parse_quality_unknown"
    return tier, None


def strong_edge_publication_decision(
    *,
    policy: SemanticPublicationPolicy,
    file_quality: Mapping[str, Any] | None = None,
    evidence_policy: Mapping[str, Any] | None = None,
    evidence_row: Mapping[str, Any] | None = None,
    bundle_state: str = "",
    map_quality: str = "",
    generated_code_class: str = "",
) -> PublicationGateDecision:
    """Compose parse trust and semantic trust without weakening either owner.

    A strict ``CALLS`` edge may publish only when every dimension agrees:
    the policy mode is ``gated``, the file's parse tier permits strong
    relations, the row is accepted strong call evidence, and — for Pro*C
    mapped evidence — the bundle state and per-entry map quality admit
    strict original-source calls.  Weak evidence is never upgraded here.
    """

    tier, parse_reason = parse_trust_for(file_quality, evidence_policy)
    if not policy.semantic_publication_allowed:
        return PublicationGateDecision(False, f"semantic_publication_{policy.mode}", tier, "none")
    if parse_reason is not None:
        return PublicationGateDecision(False, parse_reason, tier, "none")
    if tier not in policy.parse_tiers_allowing_strong:
        return PublicationGateDecision(False, f"parse_tier_{tier}", tier, "none")

    row = dict(evidence_row or {})
    if not row:
        return PublicationGateDecision(False, "missing_evidence_row", tier, "none")
    if not is_strong_call_evidence(row):
        return PublicationGateDecision(False, "not_strong_call_evidence", tier, "weak")
    if generated_code_class and generated_code_class != "original_application":
        return PublicationGateDecision(
            False, f"generated_class_{generated_code_class}", tier, "weak"
        )
    if map_quality and not is_strict_map_quality(map_quality):
        return PublicationGateDecision(False, f"map_quality_{map_quality}", tier, "weak")
    if bundle_state and not bundle_state_allows_strict_calls(bundle_state):
        return PublicationGateDecision(False, f"bundle_state_{bundle_state}", tier, "weak")
    return PublicationGateDecision(True, "accepted", tier, "strong")


# ---------------------------------------------------------------------------
# Accounting and the Pro*C two-sub-result split
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubResultAccounting:
    """Exact discovered/accepted/quarantined/rejected/deleted accounting."""

    discovered: int
    accepted: int
    quarantined: int
    rejected: int = 0
    deleted: int = 0

    def __post_init__(self) -> None:
        counts = (self.discovered, self.accepted, self.quarantined, self.rejected, self.deleted)
        if any(value < 0 for value in counts):
            raise ValueError("sub-result accounting counts must be non-negative")
        # `deleted` counts edges removed from the active generation (stale-edge
        # deletions); it is orthogonal to the discovered/accepted/quarantined/
        # rejected balance of items evaluated in the current pass.
        if self.discovered != self.accepted + self.quarantined + self.rejected:
            raise ValueError("sub-result accounting is not balanced")

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "accepted": self.accepted,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
            "deleted": self.deleted,
        }


@dataclass
class StagedSubResult:
    """One independently validated Pro*C publication sub-result."""

    name: str  # sql_original_result | semantic_mapped_result
    status: str  # accepted | rejected | quarantined
    rows: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    accounting: SubResultAccounting = SubResultAccounting(0, 0, 0)
    preserves_prior_facts: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "row_count": len(self.rows),
            "reasons": list(self.reasons),
            "accounting": self.accounting.to_dict(),
            "preserves_prior_facts": self.preserves_prior_facts,
        }


SQL_ORIGINAL_RESULT = "sql_original_result"
SEMANTIC_MAPPED_RESULT = "semantic_mapped_result"


def build_proc_staged_sub_results(
    *,
    sql_rows: Sequence[Mapping[str, Any]] = (),
    sql_relations: Sequence[Mapping[str, Any]] = (),
    sql_quarantined: int = 0,
    sql_grammar_failed: bool = False,
    semantic_reconciliation: Mapping[str, Any] | None = None,
    semantic_lane_unavailable: bool = False,
    original_region_integrity_failed: bool = False,
) -> dict[str, StagedSubResult]:
    """Build the two independently validated Pro*C sub-results.

    ``sql_original_result`` covers the original-source SQL facts (five
    labels, nine relationships).  It publishes whenever the original region
    integrity passes — including when the semantic lane is missing or
    rejected.  ``semantic_mapped_result`` covers mapped strict original
    calls; it may retain valid mapped calls when SQL grammar enrichment is
    unavailable, provided original region integrity and mapping still pass.
    """

    sql_rows = list(sql_rows)
    sql_relations = list(sql_relations)
    sql_reasons: list[str] = []
    sql_status = "accepted"
    if sql_grammar_failed or original_region_integrity_failed:
        sql_status = "rejected"
        if sql_grammar_failed:
            sql_reasons.append("sql_grammar_enrichment_failed")
        if original_region_integrity_failed:
            sql_reasons.append("original_region_integrity_failed")
    fact_count = len(sql_rows) + len(sql_relations)
    discovered = fact_count + sql_quarantined
    accepted = fact_count if sql_status == "accepted" else 0
    rejected = 0 if sql_status == "accepted" else fact_count
    sql_sub = StagedSubResult(
        name=SQL_ORIGINAL_RESULT,
        status=sql_status,
        rows=(sql_rows + sql_relations) if sql_status == "accepted" else [],
        reasons=sql_reasons,
        accounting=SubResultAccounting(
            discovered=discovered,
            accepted=accepted,
            quarantined=sql_quarantined,
            rejected=rejected,
        ),
        preserves_prior_facts=sql_status == "accepted",
    )

    if semantic_reconciliation is None:
        if semantic_lane_unavailable:
            semantic_sub = StagedSubResult(
                name=SEMANTIC_MAPPED_RESULT,
                status="rejected",
                reasons=["semantic_lane_unavailable"],
                accounting=SubResultAccounting(0, 0, 0),
                preserves_prior_facts=False,
            )
        else:
            semantic_sub = StagedSubResult(
                name=SEMANTIC_MAPPED_RESULT,
                status="rejected",
                reasons=["semantic_lane_not_attempted"],
                accounting=SubResultAccounting(0, 0, 0),
                preserves_prior_facts=False,
            )
        return {SQL_ORIGINAL_RESULT: sql_sub, SEMANTIC_MAPPED_RESULT: semantic_sub}

    reconciliation = dict(semantic_reconciliation)
    strict_rows = list(reconciliation.get("strict_rows") or [])
    all_rows = list(reconciliation.get("rows") or [])
    rejected_counts = dict(reconciliation.get("rejected") or {})
    bundle_state = str(reconciliation.get("bundle_state") or "")
    if original_region_integrity_failed:
        # Mapping may exist, but original region integrity gates the mapped
        # original-source identity: fail closed to weak evidence.
        semantic_sub = StagedSubResult(
            name=SEMANTIC_MAPPED_RESULT,
            status="rejected",
            reasons=["original_region_integrity_failed"],
            accounting=SubResultAccounting(
                discovered=len(all_rows), accepted=0, quarantined=0, rejected=len(all_rows)
            ),
            preserves_prior_facts=False,
        )
    else:
        semantic_sub = StagedSubResult(
            name=SEMANTIC_MAPPED_RESULT,
            status="accepted" if strict_rows else "quarantined",
            rows=strict_rows,
            reasons=sorted(rejected_counts) or ([f"bundle_state_{bundle_state}"] if bundle_state else []),
            accounting=SubResultAccounting(
                discovered=len(all_rows),
                accepted=len(strict_rows),
                quarantined=max(0, len(all_rows) - len(strict_rows)),
            ),
            preserves_prior_facts=True,
        )
    return {SQL_ORIGINAL_RESULT: sql_sub, SEMANTIC_MAPPED_RESULT: semantic_sub}


# ---------------------------------------------------------------------------
# Staged replacement sets
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StaleStrongEdge:
    """One strong edge from the active generation that must not survive."""

    caller_id: str
    callee_id: str
    site_id: str
    file_path: str
    reason: str  # source_deleted | source_renamed | downgraded | stale_map |
                # generated_reclassified | policy_disabled | callee_deleted

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller_id": self.caller_id,
            "callee_id": self.callee_id,
            "site_id": self.site_id,
            "file_path": self.file_path,
            "reason": self.reason,
        }


@dataclass
class StagedReplacementSet:
    """Deterministic delete+write set for one guarded publication."""

    project_id: str = ""
    revision: str = ""
    policy: SemanticPublicationPolicy = field(default_factory=SemanticPublicationPolicy)
    strict_call_rows: list[dict[str, Any]] = field(default_factory=list)
    stale_strong_edges: list[StaleStrongEdge] = field(default_factory=list)
    evidence_sites: list[dict[str, Any]] = field(default_factory=list)
    evidence_observations: list[dict[str, Any]] = field(default_factory=list)
    build_configurations: list[dict[str, Any]] = field(default_factory=list)
    coverage_records: list[dict[str, Any]] = field(default_factory=list)
    proc_sub_results: dict[str, StagedSubResult] = field(default_factory=dict)
    vector_point_ids: list[str] = field(default_factory=list)

    @property
    def reaccepted_site_ids(self) -> set[str]:
        return {
            str(row.get("site_id") or (row.get("props") or {}).get("site_id") or "")
            for row in self.strict_call_rows
        }

    def sub_result(self, name: str) -> StagedSubResult:
        return self.proc_sub_results.get(name) or StagedSubResult(
            name=name, status="rejected", reasons=["not_staged"]
        )

    def fingerprint(self) -> str:
        return _canonical_hash(
            {
                "schema_version": GUARDED_PUBLICATION_SCHEMA_VERSION,
                "project_id": self.project_id,
                "revision": self.revision,
                "policy": self.policy.to_dict(),
                "strict_calls": sorted(
                    str((row.get("props") or {}).get("site_id") or row.get("site_id") or "")
                    for row in self.strict_call_rows
                ),
                "stale_strong_edges": sorted(edge.site_id for edge in self.stale_strong_edges),
                "evidence_sites": sorted(str(row.get("site_id")) for row in self.evidence_sites),
                "evidence_observations": sorted(
                    str(row.get("evidence_id")) for row in self.evidence_observations
                ),
                "build_configurations": sorted(
                    str(row.get("config_fingerprint") or row.get("configuration_id") or "")
                    for row in self.build_configurations
                ),
                "coverage_records": sorted(
                    str(record.get("fingerprint")) for record in self.coverage_records
                ),
                "proc_sub_results": {
                    name: sub.to_dict() for name, sub in sorted(self.proc_sub_results.items())
                },
                "vector_point_ids": sorted(self.vector_point_ids),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GUARDED_PUBLICATION_SCHEMA_VERSION,
            "project_id": self.project_id,
            "revision": self.revision,
            "policy": self.policy.to_dict(),
            "strict_call_count": len(self.strict_call_rows),
            "stale_strong_edges": [edge.to_dict() for edge in self.stale_strong_edges],
            "evidence_site_count": len(self.evidence_sites),
            "evidence_observation_count": len(self.evidence_observations),
            "build_configuration_count": len(self.build_configurations),
            "coverage_record_count": len(self.coverage_records),
            "proc_sub_results": {
                name: sub.to_dict() for name, sub in sorted(self.proc_sub_results.items())
            },
            "vector_point_count": len(self.vector_point_ids),
            "fingerprint": self.fingerprint(),
        }


def baseline_edge_file_path(edge: Mapping[str, Any]) -> str:
    return str(edge.get("file_path") or "")


def compute_stale_strong_edges(
    *,
    baseline_strong_edges: Iterable[Mapping[str, Any]],
    reaccepted_site_ids: Iterable[str],
    affected_files: Iterable[str],
    affected_reason: str = "downgraded",
    file_reasons: Mapping[str, str] | None = None,
) -> list[StaleStrongEdge]:
    """Every baseline strong edge in the affected scope that is not re-accepted.

    The affected scope is the staged replacement's boundary: changed or
    deleted sources, dependency/map/context changes, analyzer-version or
    policy changes.  Edges outside the scope are untouched; edges inside it
    survive only by being re-accepted in the same staged set.  Suppressing
    new strong edges without removing old ones is forbidden — that would
    leave a falsely trusted graph.
    """

    reaccepted = {str(site_id) for site_id in reaccepted_site_ids if str(site_id)}
    reasons = dict(file_reasons or {})
    stale: list[StaleStrongEdge] = []
    seen: set[str] = set()
    for edge in baseline_strong_edges:
        site_id = str(edge.get("site_id") or "")
        if not site_id or site_id in seen:
            continue
        file_path = baseline_edge_file_path(edge)
        if file_path not in affected_files:
            continue
        if site_id in reaccepted:
            continue
        seen.add(site_id)
        stale.append(
            StaleStrongEdge(
                caller_id=str(edge.get("caller_id") or ""),
                callee_id=str(edge.get("callee_id") or ""),
                site_id=site_id,
                file_path=file_path,
                reason=str(reasons.get(file_path) or affected_reason),
            )
        )
    return stale


def build_staged_replacement(
    *,
    project_id: str,
    revision: str,
    policy: SemanticPublicationPolicy,
    merge_result: Mapping[str, Any] | None = None,
    strict_call_rows: Sequence[Mapping[str, Any]] | None = None,
    evidence_sites: Sequence[Mapping[str, Any]] | None = None,
    evidence_observations: Sequence[Mapping[str, Any]] | None = None,
    build_configurations: Sequence[Mapping[str, Any]] | None = None,
    coverage_records: Sequence[Mapping[str, Any]] | None = None,
    baseline_strong_edges: Iterable[Mapping[str, Any]] = (),
    affected_files: Iterable[str] = (),
    file_reasons: Mapping[str, str] | None = None,
    proc_sub_results: Mapping[str, StagedSubResult] | None = None,
    vector_point_ids: Iterable[str] = (),
) -> StagedReplacementSet:
    """Assemble one staged replacement set from validated evidence.

    ``merge_result`` may be an ``EvidenceMergeResult`` or its mapping form;
    explicit row arguments win when both are supplied.  Two re-checks are
    enforced here: the policy mode (``off``/``rollback`` never stage strict
    rows, pairing suppression with stale-edge removal) and evidence strength
    (a claimed ``direct_resolved`` row without an approved provider and
    complete identity fails closed).  The full per-file gate composition —
    parse tier, map quality, bundle state, generated class — is applied at
    evidence assembly via ``strong_edge_publication_decision`` before rows
    reach this builder.
    """

    staged = StagedReplacementSet(
        project_id=project_id,
        revision=revision,
        policy=policy,
        proc_sub_results=dict(proc_sub_results or {}),
        vector_point_ids=[str(point_id) for point_id in vector_point_ids],
    )
    merge = merge_result
    if merge is not None and not isinstance(merge, Mapping):
        accepted_ids = getattr(merge, "accepted_function_ids", None)
        merge = {
            "strict_call_rows": [dict(row) for row in merge.strict_call_rows],
            "call_sites": [
                site.to_writer_rows(accepted_ids) for site in merge.call_sites
            ],
            "coverage_records": [
                dict(record) for record in (getattr(merge, "coverage_records", None) or [])
            ],
        }
    strict_rows = list(strict_call_rows) if strict_call_rows is not None else list(
        (merge or {}).get("strict_call_rows") or []
    )
    sites = list(evidence_sites) if evidence_sites is not None else list(
        (merge or {}).get("call_sites") or []
    )
    if not policy.semantic_publication_allowed:
        # Containment and rollback policies never stage strict rows; the
        # affected baseline edges are removed instead of being suppressed
        # while stale copies survive.
        staged.strict_call_rows = []
    else:
        verified_rows: list[dict[str, Any]] = []
        for row in strict_rows:
            props = row.get("props") if isinstance(row.get("props"), Mapping) else row
            if props.get("resolution_class") == RESOLUTION_CLASS_DIRECT_RESOLVED and not is_strong_call_evidence(props):
                raise ValueError(
                    "staged strict call row claims direct_resolved without an "
                    "approved semantic provider and complete identity fields"
                )
            verified_rows.append(dict(row))
        staged.strict_call_rows = verified_rows
    staged.evidence_sites = [dict(row) for row in sites]
    staged.evidence_observations = [dict(row) for row in (evidence_observations or [])]
    staged.build_configurations = [dict(row) for row in (build_configurations or [])]
    staged.coverage_records = [dict(record) for record in (coverage_records or [])]

    reaccepted = staged.reaccepted_site_ids
    staged.stale_strong_edges = compute_stale_strong_edges(
        baseline_strong_edges=baseline_strong_edges,
        reaccepted_site_ids=reaccepted,
        affected_files=set(affected_files),
        file_reasons=file_reasons,
    )
    return staged


STALE_STRONG_EDGE_DELETE_CYPHER = """
UNWIND $rows AS row
MATCH (caller:Function {id: row.caller_id})-[r:CALLS {site_id: row.site_id}]->(callee:Function {id: row.callee_id})
DELETE r
RETURN count(r) AS count
"""

STALE_STRONG_EDGE_READBACK_CYPHER = """
UNWIND $rows AS row
MATCH (caller:Function {id: row.caller_id})-[r:CALLS {site_id: row.site_id}]->(callee:Function {id: row.callee_id})
RETURN count(r) AS count
"""


async def apply_stale_strong_edge_deletions(
    *,
    driver: Any,
    database: str | None,
    stale_edges: Sequence[StaleStrongEdge],
    operation_id: str = "",
) -> int:
    """Delete staged stale strong edges, returning the deleted count.

    Runs inside a journal mutation fence so the required-mode write guard
    admits it.  It executes before the staged writes and before
    publication; the active generation stays physically isolated by the
    concurrency owner (staged generations build under their own generation
    root), so a failure after this delete but before publication leaves the
    active generation untouched.  The delete is idempotent — replaying it
    removes zero edges — so it carries no exact-count assertion of its own;
    ``validate_staged_publication``'s stale-survivor readback is the
    fail-closed check that no stale strong edge survived into the staged
    generation.
    """

    rows = [edge.to_dict() for edge in stale_edges]
    if not rows:
        return 0
    from tools.graph.journal.guard import journaled_mutation

    job_id = str(operation_id or str(uuid.uuid4()))
    with journaled_mutation(job_id, "staged_replacement:delete_strong_edges"):
        records, _, _ = await driver.execute_query(
            STALE_STRONG_EDGE_DELETE_CYPHER, {"rows": rows}, database
        )
        deleted = int(records[0]["count"]) if records else 0
    return deleted


# ---------------------------------------------------------------------------
# Publication validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationExpectation:
    """Exact effects a staged generation must produce before publication."""

    strict_call_count: int = 0
    evidence_site_count: int = 0
    evidence_observation_count: int = 0
    stale_strong_edge_survivors: int = 0
    vector_item_count: int = 0
    coverage_status: str = ""  # required frontier status, "" = no requirement

    def to_dict(self) -> dict[str, Any]:
        return {
            "strict_call_count": self.strict_call_count,
            "evidence_site_count": self.evidence_site_count,
            "evidence_observation_count": self.evidence_observation_count,
            "stale_strong_edge_survivors": self.stale_strong_edge_survivors,
            "vector_item_count": self.vector_item_count,
            "coverage_status": self.coverage_status,
        }


@dataclass(frozen=True)
class PublicationValidationResult:
    ok: bool
    violations: tuple[str, ...]
    checked: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": list(self.violations), "checked": dict(self.checked)}


def expected_effects(staged: StagedReplacementSet) -> PublicationExpectation:
    """Exact expected effects derived from the staged replacement set.

    Mirrors the observation writer's rule: a row produces an edge only when
    it is not flagged dangling and carries a callee endpoint.
    """

    linkable_observations = sum(
        1
        for row in staged.evidence_observations
        if not row.get("dangling")
        and str(row.get("callee_id") or row.get("callee_symbol_id") or "")
    )
    return PublicationExpectation(
        strict_call_count=len(staged.strict_call_rows),
        evidence_site_count=len(staged.evidence_sites),
        evidence_observation_count=linkable_observations,
        stale_strong_edge_survivors=0,
        vector_item_count=len(staged.vector_point_ids),
        coverage_status="",
    )


def validate_staged_publication(
    staged: StagedReplacementSet,
    *,
    readback: Mapping[str, int],
    coverage_block: Mapping[str, Any] | None = None,
    expectation: PublicationExpectation | None = None,
) -> PublicationValidationResult:
    """Fail-closed validation of graph/vector effects before publication.

    ``readback`` supplies exact post-write counts; representative strict and
    conservative query behaviour is validated through the same exact-count
    contract (strict counts must come only from accepted strong evidence,
    conservative rows must retain their classes).  Any mismatch, missing
    count, or surviving stale strong edge blocks publication.
    """

    expected = expectation or expected_effects(staged)
    checked: dict[str, int] = {}
    violations: list[str] = []

    for name, expected_count in (
        ("strict_calls", expected.strict_call_count),
        ("evidence_sites", expected.evidence_site_count),
        ("evidence_observations", expected.evidence_observation_count),
        ("vector_items", expected.vector_item_count),
    ):
        if name not in readback:
            violations.append(f"missing_readback:{name}")
            continue
        actual = int(readback[name])
        checked[name] = actual
        if actual != expected_count:
            violations.append(f"count_mismatch:{name}:expected={expected_count}:actual={actual}")

    stale_readback = int(readback.get("stale_strong_edge_survivors", -1))
    if stale_readback < 0:
        violations.append("missing_readback:stale_strong_edge_survivors")
    else:
        checked["stale_strong_edge_survivors"] = stale_readback
        if stale_readback != expected.stale_strong_edge_survivors:
            violations.append(
                f"stale_strong_edges_survived:expected=0:actual={stale_readback}"
            )

    if expected.coverage_status:
        status = str((coverage_block or {}).get("status") or "")
        if status != expected.coverage_status:
            violations.append(
                f"coverage_mismatch:expected={expected.coverage_status}:actual={status}"
            )

    return PublicationValidationResult(
        ok=not violations, violations=tuple(violations), checked=checked
    )


# ---------------------------------------------------------------------------
# Generation ledger, atomic publication, and rollback
# ---------------------------------------------------------------------------


LEDGER_HISTORY_LIMIT = 8


class SemanticGenerationLedger:
    """Durable record of the last valid semantic generations.

    Rollback must select a *known valid* generation — reparsing source
    during the incident the rollback exists for is forbidden.  The ledger
    keeps a bounded, fingerprinted history written atomically.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        empty = {"schema_version": GUARDED_PUBLICATION_SCHEMA_VERSION, "generations": []}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty
        except OSError:
            return {**empty, "unreadable": True}
        try:
            payload = json.loads(raw)
        except ValueError:
            # A present-but-corrupt ledger must be visible to operators, not
            # silently reset: rollback history is incomplete until repaired.
            return {**empty, "unreadable": True}
        if not isinstance(payload, Mapping):
            return {**empty, "unreadable": True}
        generations = payload.get("generations")
        if not isinstance(generations, list):
            generations = []
        return {
            "schema_version": GUARDED_PUBLICATION_SCHEMA_VERSION,
            "generations": [dict(entry) for entry in generations if isinstance(entry, Mapping)],
        }

    def last_valid(self) -> dict[str, Any] | None:
        generations = self.load()["generations"]
        return dict(generations[-1]) if generations else None

    def record(
        self,
        *,
        generation_id: str,
        revision: str,
        fingerprint: str,
        policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        entry = {
            "generation_id": str(generation_id),
            "revision": str(revision),
            "fingerprint": str(fingerprint),
            "policy_version": str(dict(policy).get("policy_version") or ""),
            "mode": str(dict(policy).get("mode") or ""),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        payload = self.load()
        generations = payload["generations"]
        generations = [g for g in generations if g.get("generation_id") != entry["generation_id"]]
        generations.append(entry)
        payload["generations"] = generations[-LEDGER_HISTORY_LIMIT:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        return entry

    def status(self) -> dict[str, Any]:
        payload = self.load()
        last = payload["generations"][-1] if payload["generations"] else None
        status: dict[str, Any] = {"last_valid_generation": None, "generation_count": 0}
        if payload.get("unreadable"):
            status["unreadable"] = True
            status["detail"] = "ledger file exists but could not be read; rollback history is incomplete until repaired"
        if last is not None:
            status.update(
                {
                    "last_valid_generation": last["generation_id"],
                    "last_valid_revision": last.get("revision") or "",
                    "last_valid_fingerprint": last.get("fingerprint") or "",
                    "last_valid_policy_version": last.get("policy_version") or "",
                    "generation_count": len(payload["generations"]),
                }
            )
        return status


@dataclass(frozen=True)
class PublicationOutcome:
    """Typed result of one guarded publication attempt."""

    outcome: RunOutcome
    generation_id: str = ""
    retained_generation: str = ""
    validation: PublicationValidationResult | None = None
    failure: FailureRecord | None = None
    accounting: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "generation_id": self.generation_id,
            "retained_generation": self.retained_generation,
            "validation": self.validation.to_dict() if self.validation else None,
            "failure": self.failure.to_dict() if self.failure else None,
            "accounting": dict(self.accounting),
        }


def _publication_failure(
    *,
    code: str,
    failure_class: FailureClass,
    summary: str,
    safe_action: str,
    run_id: str,
    correlation_id: str,
    details: Mapping[str, Any] | None = None,
) -> FailureRecord:
    return FailureRecord(
        code=code,
        failure_class=failure_class,
        phase=RunPhase.PUBLISHING,
        component="cplus.guarded_publication",
        retryable=failure_class
        in {FailureClass.STORAGE_UNAVAILABLE, FailureClass.TIMEOUT, FailureClass.CAPACITY},
        run_id=run_id,
        correlation_id=correlation_id,
        summary=summary,
        safe_action=safe_action,
        details=dict(details or {}),
    )


def publish_staged_generation(
    staged: StagedReplacementSet,
    *,
    validate_and_publish: Any,
    generation_id: str = "",
    revision: str = "",
    readback: Mapping[str, int],
    coverage_block: Mapping[str, Any] | None = None,
    ledger: SemanticGenerationLedger | None = None,
    run_id: str = "",
    correlation_id: str = "",
    queue_drained: bool = True,
) -> PublicationOutcome:
    """Publish one staged generation atomically or keep the last generation.

    ``validate_and_publish`` follows the concurrency owner's
    ``publish(manifest, validate)`` contract: a ``GenerationManager`` or
    ``StoreGateway`` instance is accepted directly (its ``allocate`` builds
    the real ``GenerationManifest``), or any callable taking
    ``(manifest_like, validate)``.  The caller-supplied ``validate``
    callback runs inside the publication boundary and the manifest flips
    atomically only when it passes.  Owner-side contract rejections
    (``ValueError`` — wrong target, non-isolated generation paths) return a
    terminal typed failure; any other exception at the boundary is treated
    as ambiguous and must be reconciled before retry.  Every failure path
    retains the previous generation — it never converts to an empty
    successful strict graph.
    """

    run_id = run_id or staged.revision or "semantic-publication"
    correlation_id = correlation_id or run_id
    retained = ledger.last_valid() if ledger is not None else None
    retained_generation = str((retained or {}).get("generation_id") or "")

    if not queue_drained:
        return PublicationOutcome(
            outcome=RunOutcome.FAILED_RETRYABLE,
            retained_generation=retained_generation,
            failure=_publication_failure(
                code="semantic_queue_not_drained",
                failure_class=FailureClass.INTEGRITY,
                summary="semantic evidence queue did not drain before publication",
                safe_action="re-run ingestion after the queue drains; the active generation is unchanged",
                run_id=run_id,
                correlation_id=correlation_id,
                details={"generation": generation_id},
            ),
            accounting=staged.to_dict(),
        )

    validation = validate_staged_publication(
        staged, readback=readback, coverage_block=coverage_block
    )
    if not validation.ok:
        return PublicationOutcome(
            outcome=RunOutcome.FAILED_TERMINAL,
            retained_generation=retained_generation,
            validation=validation,
            failure=_publication_failure(
                code="semantic_publication_validation_failed",
                failure_class=FailureClass.INTEGRITY,
                summary="staged generation failed exact-count publication validation",
                safe_action="inspect the staged replacement accounting; the active generation is unchanged",
                run_id=run_id,
                correlation_id=correlation_id,
                details={"violations": list(validation.violations)},
            ),
            accounting=staged.to_dict(),
        )

    fingerprint = staged.fingerprint()
    generation_id = generation_id or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{staged.project_id}:{fingerprint}")
    )
    source_revision = revision or staged.revision

    def _validate(manifest: Any) -> None:
        if not validation.ok:  # pragma: no cover - guarded above
            raise ValueError("staged generation failed publication validation")

    publisher = validate_and_publish
    try:
        if hasattr(publisher, "allocate") and hasattr(publisher, "publish"):
            # Real concurrency-owner boundary: build the owner's manifest and
            # publish through its atomic validate-and-flip path.
            manifest = publisher.allocate(source_revision, generation_id=generation_id)
            published = publisher.publish(manifest, _validate)
        else:
            manifest = _PublishedManifest(
                generation_id=generation_id,
                source_revision=source_revision,
                fingerprint=fingerprint,
                validation={"guarded_publication": validation.to_dict()},
            )
            published = publisher(manifest, _validate)
    except ValueError as exc:
        # The owner rejects the manifest before any write (wrong target,
        # non-isolated generation paths): terminal, not ambiguous.
        return PublicationOutcome(
            outcome=RunOutcome.FAILED_TERMINAL,
            generation_id=generation_id,
            retained_generation=retained_generation,
            validation=validation,
            failure=_publication_failure(
                code="semantic_publication_contract_rejected",
                failure_class=FailureClass.CONFIGURATION,
                summary=f"publication boundary rejected the generation manifest: {exc}",
                safe_action="correct the generation/target configuration; the active generation is unchanged",
                run_id=run_id,
                correlation_id=correlation_id,
                details={"error_type": type(exc).__name__},
            ),
            accounting=staged.to_dict(),
        )
    except Exception as exc:
        return PublicationOutcome(
            outcome=RunOutcome.AMBIGUOUS,
            generation_id=generation_id,
            retained_generation=retained_generation,
            validation=validation,
            failure=_publication_failure(
                code="semantic_publication_ambiguous",
                failure_class=FailureClass.AMBIGUOUS_MUTATION,
                summary=f"publication boundary raised before the atomic flip: {type(exc).__name__}",
                safe_action="reconcile the active-generation manifest before retrying publication",
                run_id=run_id,
                correlation_id=correlation_id,
                details={"error_type": type(exc).__name__},
            ),
            accounting=staged.to_dict(),
        )

    published_id = str(getattr(published, "generation_id", "") or generation_id)
    if ledger is not None:
        try:
            ledger.record(
                generation_id=published_id,
                revision=source_revision,
                fingerprint=fingerprint,
                policy=staged.policy.to_dict(),
            )
        except OSError as exc:
            # The generation published but the rollback ledger did not
            # record it: retryable operator action, and the published id is
            # reported so the entry can be reconciled.
            return PublicationOutcome(
                outcome=RunOutcome.FAILED_RETRYABLE,
                generation_id=published_id,
                retained_generation=retained_generation,
                validation=validation,
                failure=_publication_failure(
                    code="semantic_ledger_write_failed",
                    failure_class=FailureClass.STORAGE_UNAVAILABLE,
                    summary="generation published but the semantic generation ledger could not record it",
                    safe_action="reconcile the ledger entry for the published generation; rollback history is incomplete until then",
                    run_id=run_id,
                    correlation_id=correlation_id,
                    details={"error_type": type(exc).__name__, "published_generation": published_id},
                ),
                accounting=staged.to_dict(),
            )
    return PublicationOutcome(
        outcome=RunOutcome.SUCCESS,
        generation_id=published_id,
        retained_generation=retained_generation,
        validation=validation,
        accounting=staged.to_dict(),
    )


@dataclass(frozen=True)
class _PublishedManifest:
    """Minimal manifest shape for the publication boundary and tests."""

    generation_id: str
    source_revision: str
    fingerprint: str
    validation: Mapping[str, Any]


@dataclass(frozen=True)
class RollbackState:
    """Outcome of selecting the containment rollback configuration."""

    active: bool
    served_generation: str
    served_revision: str
    mode: str
    weak_evidence_preserved: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "served_generation": self.served_generation,
            "served_revision": self.served_revision,
            "mode": self.mode,
            "weak_evidence_preserved": self.weak_evidence_preserved,
            "detail": self.detail,
        }


def rollback_to_last_valid_generation(
    ledger: SemanticGenerationLedger,
    *,
    policy: SemanticPublicationPolicy | None = None,
) -> tuple[SemanticPublicationPolicy, RollbackState]:
    """One configuration switch back to containment plus the last valid generation.

    Disables semantic publication (weak evidence continues to publish),
    selects the ledger's last valid semantic generation — never a reparse —
    and reports what consumers are served.  With no recorded generation the
    rollback still activates containment; consumers stay on Tree-sitter
    structure plus weak edges.
    """

    rolled_back = SemanticPublicationPolicy(
        mode="rollback",
        policy_version=(policy or SemanticPublicationPolicy()).policy_version,
        strong_relation_types=(policy or SemanticPublicationPolicy()).strong_relation_types,
        parse_tiers_allowing_strong=(
            policy or SemanticPublicationPolicy()
        ).parse_tiers_allowing_strong,
    )
    last = ledger.last_valid()
    unreadable = bool(ledger.load().get("unreadable"))
    if last is None:
        detail = (
            "ledger unreadable; repair it before relying on rollback history"
            if unreadable
            else "containment active; no recorded semantic generation, serving structure plus weak evidence"
        )
        state = RollbackState(
            active=True,
            served_generation="",
            served_revision="",
            mode="rollback",
            weak_evidence_preserved=True,
            detail=detail,
        )
    else:
        state = RollbackState(
            active=True,
            served_generation=str(last.get("generation_id") or ""),
            served_revision=str(last.get("revision") or ""),
            mode="rollback",
            weak_evidence_preserved=True,
            detail=(
                "containment active; serving the last valid semantic generation "
                "without reparsing source"
            ),
        )
    return rolled_back, state


# ---------------------------------------------------------------------------
# Typed status surface
# ---------------------------------------------------------------------------


def publication_status(
    *,
    policy: SemanticPublicationPolicy,
    queue: Mapping[str, Any] | None = None,
    coverage_block: Mapping[str, Any] | None = None,
    ledger: SemanticGenerationLedger | None = None,
    revision: str = "",
    staged: StagedReplacementSet | None = None,
    rollback: RollbackState | None = None,
) -> dict[str, Any]:
    """Operator-facing queue/coverage/generation/revision/policy status."""

    status: dict[str, Any] = {
        "schema_version": GUARDED_PUBLICATION_SCHEMA_VERSION,
        "semantic_policy": policy.to_dict(),
        "queue": dict(queue or {"state": "unknown"}),
        "coverage": dict(coverage_block or {"status": "unknown"}),
        "generation": ledger.status() if ledger is not None else {"last_valid_generation": None},
        "revision": revision or (staged.revision if staged else ""),
    }
    if staged is not None:
        status["staged_replacement"] = staged.to_dict()
    if rollback is not None:
        status["rollback"] = rollback.to_dict()
    return status


# ---------------------------------------------------------------------------
# Vector and report safety
# ---------------------------------------------------------------------------


def vector_item_rejection_reason(item: Mapping[str, Any]) -> str | None:
    """Why one candidate vector item must not be embedded, or None.

    Generated code, precompiler runtime wrappers, raw commands, and
    credential-bearing text never enter embeddings or reports.  Pro*C items
    must be original-source SQL facts: only approved original SQL text and
    summaries may embed.
    """

    generated_class = str(item.get("generated_code_class") or "")
    if generated_class in VECTOR_FORBIDDEN_GENERATED_CLASSES:
        return f"generated_class_{generated_class}"
    if str(item.get("source_origin") or "") == "generated":
        return "generated_origin"
    for text_field in ("code", "summary", "note", "comment", "raw_text", "command", "raw_command", "cli_args", "conn_str"):
        marker = secret_bearing_text(str(item.get(text_field) or ""))
        if marker is not None:
            return f"credential_bearing_{text_field}"
    label = str(item.get("label") or "")
    if label in PROC_NODE_LABELS:
        # Original SQL facts embed only their original text/summary; the
        # masked/generated planes are never vector content.
        if str(item.get("vector_origin") or "") == "masked":
            return "masked_origin"
    return None


def sanitize_vector_items(
    items: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Filter candidate vector items to approved original-source content.

    Returns ``(safe_items, rejections)`` where each rejection maps the item
    identity to its typed reason, so exclusion is accounted rather than
    silent.
    """

    safe: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    for item in items:
        reason = vector_item_rejection_reason(item)
        if reason is not None:
            rejections.append(
                {
                    "identity": str(item.get("id") or item.get("symbol_id") or ""),
                    "reason": reason,
                }
            )
            continue
        safe.append(dict(item))
    return safe, rejections
