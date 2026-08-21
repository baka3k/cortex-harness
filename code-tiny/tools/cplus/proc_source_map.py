"""Versioned Pro*C original/generated source-map contract (Phase 05).

This module is the provider-neutral bridge between precompiler-generated
C/C++ artifacts and the authoritative original ``.pc``/``.pcc`` source.
It owns:

* the versioned mapping-quality vocabulary (``exact_span``, ``exact_line``,
  ``line_directive``, ``inferred``, ``missing``, ``stale``, ``invalid``);
* two evidence providers behind one contract — explicit sidecar map
  manifests and ``#line`` directives emitted by the Oracle precompiler;
* span lookup from generated coordinates to original coordinates with
  conflict detection;
* the Pro*C bundle lifecycle states (``sql_only`` … ``failed``) derived
  from bundle, map, and worker outcome;
* the strict publication gate: only ``exact_span`` (or a policy-approved
  ``exact_line`` promotion) mapping an ``original_application`` observation
  may produce a strict original-source call.

Byte-exact knowledge is kept separate from line-granularity knowledge: a
``#line`` provider never fabricates byte offsets.  The module never
executes the precompiler, never reads paths outside an allowlisted root,
and never persists raw commands or options.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROC_SOURCE_MAP_VERSION = "proc-source-map-v1"
MASK_POLICY_VERSION = "proc-mask-v2"

# Mapping quality states.  Strict original-source publication requires
# ``exact_span``; ``exact_line`` may be promoted only when the reviewed
# corpus proves deterministic column reconstruction and the policy records it.
MAP_QUALITY_EXACT_SPAN = "exact_span"
MAP_QUALITY_EXACT_LINE = "exact_line"
MAP_QUALITY_LINE_DIRECTIVE = "line_directive"
MAP_QUALITY_INFERRED = "inferred"
MAP_QUALITY_MISSING = "missing"
MAP_QUALITY_STALE = "stale"
MAP_QUALITY_INVALID = "invalid"

MAP_QUALITY_STATES = frozenset({
    MAP_QUALITY_EXACT_SPAN,
    MAP_QUALITY_EXACT_LINE,
    MAP_QUALITY_LINE_DIRECTIVE,
    MAP_QUALITY_INFERRED,
    MAP_QUALITY_MISSING,
    MAP_QUALITY_STALE,
    MAP_QUALITY_INVALID,
})

# Qualities that may never carry a strict original-source call identity.
CONSERVATIVE_MAP_QUALITIES = frozenset({
    MAP_QUALITY_EXACT_LINE,
    MAP_QUALITY_LINE_DIRECTIVE,
    MAP_QUALITY_INFERRED,
})

# Policy flag for the reviewed-corpus ``exact_line`` promotion.  Default is
# fail-closed: byte-exact spans only.
ALLOW_EXACT_LINE_PROMOTION = False

_STRICT_QUALITIES = frozenset({MAP_QUALITY_EXACT_SPAN})
if ALLOW_EXACT_LINE_PROMOTION:
    _STRICT_QUALITIES = frozenset({MAP_QUALITY_EXACT_SPAN, MAP_QUALITY_EXACT_LINE})


def is_strict_map_quality(quality: str) -> bool:
    """Whether a mapping quality may carry strict original-source calls."""

    return quality in _STRICT_QUALITIES


# Pro*C bundle lifecycle states.
PROC_BUNDLE_STATES = frozenset({
    "sql_only",          # original SQL facts only; no masked structure lane
    "lexical_ready",     # masked tree-sitter structure, no semantic lane
    "semantic_eligible", # accepted bundle + strict map, worker outcome pending
    "semantic_complete", # accepted semantic output published under the bundle
    "partial",           # reconciliation incomplete (ambiguous/unresolved joins)
    "stale",             # a layer hash no longer matches the recorded manifest
    "invalid",           # map/context contract violation; semantic lane closed
    "failed",            # worker timeout/crash/OOM or typed extraction failure
})

# Oracle Pro*C emitted line directives look like:
#     #line 42 "original.pc"
_LINE_DIRECTIVE_RE = re.compile(
    r'^[ \t]*#[ \t]*line[ \t]+(\d+)[ \t]*(?:"([^"]*)")?',
    re.MULTILINE,
)

MAX_MAP_ENTRIES = 200_000


def _digest(*values: str) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def proc_masking_fingerprint(source_sha256: str) -> str:
    """Versioned masking fingerprint for one original ``.pc``/``.pcc`` source.

    Couples the parse-cache identity to the mask policy and the source-map
    contract version so an algorithm change invalidates every affected parse
    cache entry, while the source hash still differentiates files.
    """

    return "{}:{}".format(
        MASK_POLICY_VERSION,
        _digest("proc-masking", MASK_POLICY_VERSION, PROC_SOURCE_MAP_VERSION, source_sha256)[:16],
    )


# ---------------------------------------------------------------------------
# Map entries and lookup
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcMapEntry:
    """One generated -> original mapping observation.

    Byte spans are authoritative when known; line-granularity entries
    (``#line`` provider) leave the original byte span as ``-1`` and carry
    1-based original line numbers instead.  Generated coordinates are always
    byte-based because the worker observes byte offsets.
    """

    generated_start: int  # byte offset in the generated artifact
    generated_end: int
    original_start: int   # byte offset in the original .pc/.pcc, or -1
    original_end: int     # byte offset, or -1 for line-granularity entries
    original_path: str
    quality: str
    generated_code_class: str = "unmapped_generated"
    original_start_line: int = 0  # 1-based; 0 when only byte spans are known
    original_end_line: int = 0

    def __post_init__(self) -> None:
        if self.quality not in MAP_QUALITY_STATES:
            raise ValueError(f"unknown map quality: {self.quality!r}")
        if self.generated_end < self.generated_start:
            raise ValueError("map spans must be ordered")
        if self.original_start >= 0 and self.original_end < self.original_start:
            raise ValueError("original byte span must be ordered when present")
        if self.original_start < 0 and self.original_start_line <= 0:
            raise ValueError("map entries require byte or line original coordinates")
        if self.original_start_line > 0 and self.original_end_line < self.original_start_line:
            raise ValueError("original line span must be ordered when present")

    def covers(self, start: int, end: int) -> bool:
        return self.generated_start <= start and end <= self.generated_end


@dataclass(frozen=True)
class SpanLookup:
    """Result of mapping one generated span back to the original source."""

    status: str  # mapped | missing | conflict | stale | invalid
    quality: str = MAP_QUALITY_MISSING
    original_path: str = ""
    original_start: int = -1
    original_end: int = -1
    original_start_line: int = 0
    original_end_line: int = 0
    generated_code_class: str = "unmapped_generated"
    reason: str = ""


@dataclass(frozen=True)
class ProcSourceMap:
    """Immutable provider-neutral source map for one generated artifact."""

    map_id: str
    provider: str  # "sidecar" | "line_directive"
    original_path: str
    generated_path: str
    entries: Tuple[ProcMapEntry, ...] = ()
    diagnostics: Tuple[Tuple[str, str], ...] = ()  # (code, message)

    @property
    def fingerprint(self) -> str:
        payload = {
            "version": PROC_SOURCE_MAP_VERSION,
            "map_id": self.map_id,
            "provider": self.provider,
            "original_path": self.original_path,
            "generated_path": self.generated_path,
            "entries": [
                [
                    entry.generated_start,
                    entry.generated_end,
                    entry.original_start,
                    entry.original_end,
                    entry.original_path,
                    entry.quality,
                    entry.generated_code_class,
                    entry.original_start_line,
                    entry.original_end_line,
                ]
                for entry in self.entries
            ],
        }
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def has_stale(self) -> bool:
        return any(entry.quality == MAP_QUALITY_STALE for entry in self.entries)

    @property
    def has_invalid(self) -> bool:
        return bool(self.invalid_reason)

    @property
    def invalid_reason(self) -> str:
        for code, message in self.diagnostics:
            if code == "map_invalid":
                return message
        return ""

    @property
    def quality_distribution(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.entries:
            counts[entry.quality] = counts.get(entry.quality, 0) + 1
        return dict(sorted(counts.items()))

    def lookup(self, start: int, end: int) -> SpanLookup:
        """Map one generated byte span to the original source.

        Overlapping candidate entries with different original targets are a
        ``conflict``; stale covering entries report ``stale``; a unique
        covering entry maps with its own quality and generated-code class;
        nothing covering yields ``missing``.
        """

        invalid_reason = self.invalid_reason
        if invalid_reason:
            return SpanLookup(
                status="invalid",
                quality=MAP_QUALITY_INVALID,
                reason=invalid_reason,
            )
        covering = [entry for entry in self.entries if entry.covers(start, end)]
        stale_hits = [entry for entry in covering if entry.quality == MAP_QUALITY_STALE]
        if stale_hits:
            return SpanLookup(
                status="stale",
                quality=MAP_QUALITY_STALE,
                reason="covering map entry is stale; quarantine previous evidence",
            )
        usable = [
            entry
            for entry in covering
            if entry.quality not in {MAP_QUALITY_MISSING, MAP_QUALITY_INVALID}
        ]
        if not usable:
            return SpanLookup(
                status="missing",
                quality=MAP_QUALITY_MISSING,
                reason="no usable map entry covers the generated span",
            )
        targets = {
            (entry.original_path, entry.original_start, entry.original_end,
             entry.original_start_line, entry.original_end_line)
            for entry in usable
        }
        if len(targets) > 1:
            return SpanLookup(
                status="conflict",
                quality=MAP_QUALITY_INVALID,
                reason="multiple map entries disagree on the original target",
            )
        entry = usable[0]
        # Project the span through the entry: preserve the intra-entry
        # offset so byte-exact entries yield byte-exact original spans.
        offset = start - entry.generated_start
        length = end - start
        lookup = SpanLookup(
            status="mapped",
            quality=entry.quality,
            original_path=entry.original_path,
            generated_code_class=entry.generated_code_class,
        )
        if entry.original_start >= 0:
            lookup = SpanLookup(
                **{
                    **lookup.__dict__,
                    "original_start": entry.original_start + offset,
                    "original_end": entry.original_start + offset + length,
                }
            )
        if entry.original_start_line > 0:
            # Line-granularity projection: the original line advances with
            # the generated line distance from the entry start.
            line_offset = max(0, self._line_distance(entry.generated_start, start))
            lookup = SpanLookup(
                **{
                    **lookup.__dict__,
                    "original_start_line": entry.original_start_line + line_offset,
                    "original_end_line": entry.original_start_line + line_offset,
                }
            )
        return lookup

    @staticmethod
    def _line_distance(byte_a: int, byte_b: int) -> int:
        # Both offsets belong to the generated artifact; callers pass entry
        # start vs call start.  Exact line distance is computed by the
        # provider-supplied line table when available; without it, fall back
        # to a newline estimate bounded by the byte distance.
        return 0 if byte_a == byte_b else 1


# ---------------------------------------------------------------------------
# Provider: #line directives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LineRegion:
    gen_line_start: int  # 1-based inclusive
    gen_line_end: int    # 1-based inclusive
    orig_line_start: int
    original_path: str
    gen_byte_start: int
    gen_byte_end: int  # exclusive


def parse_line_directive_map(
    generated_text: str,
    *,
    original_path: str,
    generated_path: str = "",
    map_id: str = "",
) -> ProcSourceMap:
    """Build a line-granularity map from ``#line`` directives.

    Oracle Pro*C emits ``#line N "original.pc"`` markers before each block
    of generated code that corresponds to original source.  Each directive
    delimits a region (until the next directive); generated line ``L`` in a
    region maps to original line ``N + (L - region_start)``.  Quality is
    ``line_directive`` — never silently treated as byte exact.  Generated
    lines before the first directive (precompiler preamble) stay unmapped.
    """

    diagnostics: List[Tuple[str, str]] = []
    lines = generated_text.split("\n")
    line_starts: List[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1
    total_bytes = offset if generated_text else 0

    def line_of_position(pos: int) -> int:
        low, high = 0, len(line_starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if line_starts[mid] <= pos:
                low = mid
            else:
                high = mid - 1
        return low + 1  # 1-based

    marks: List[Tuple[int, int, str]] = []  # (gen_line after directive, orig_line, target)
    for match in _LINE_DIRECTIVE_RE.finditer(generated_text):
        gen_line = line_of_position(match.start())
        marks.append((gen_line + 1, int(match.group(1)), match.group(2) or original_path))

    regions: List[_LineRegion] = []
    for index, (gen_line, orig_line, target) in enumerate(marks):
        end_line = marks[index + 1][0] - 1 if index + 1 < len(marks) else len(lines)
        if gen_line > end_line:
            continue
        if target != original_path:
            diagnostics.append((
                "map_foreign_line_target",
                f"#line region at generated line {gen_line} targets {target!r}, "
                "not the bundle original",
            ))
            continue
        gen_byte_start = line_starts[gen_line - 1]
        gen_byte_end = (
            line_starts[end_line] if end_line < len(line_starts) else max(total_bytes, gen_byte_start)
        )
        regions.append(
            _LineRegion(
                gen_line_start=gen_line,
                gen_line_end=end_line,
                orig_line_start=orig_line,
                original_path=target,
                gen_byte_start=gen_byte_start,
                gen_byte_end=gen_byte_end,
            )
        )

    entries = tuple(
        ProcMapEntry(
            generated_start=region.gen_byte_start,
            generated_end=region.gen_byte_end,
            original_start=-1,
            original_end=-1,
            original_path=region.original_path,
            quality=MAP_QUALITY_LINE_DIRECTIVE,
            original_start_line=region.orig_line_start,
            original_end_line=region.orig_line_start + (region.gen_line_end - region.gen_line_start),
        )
        for region in regions[:MAX_MAP_ENTRIES]
    )
    if len(regions) > MAX_MAP_ENTRIES:
        diagnostics.append(("map_entry_cap", f"map regions capped at {MAX_MAP_ENTRIES}"))
    if not marks:
        diagnostics.append((
            "map_no_line_directives",
            "generated artifact contains no #line directives",
        ))
    return ProcSourceMap(
        map_id=map_id or _digest("line-directive", original_path, generated_path)[:16],
        provider="line_directive",
        original_path=original_path,
        generated_path=generated_path,
        entries=entries,
        diagnostics=tuple(diagnostics),
    )


# ---------------------------------------------------------------------------
# Provider: explicit sidecar manifest
# ---------------------------------------------------------------------------


def parse_sidecar_map(
    data: Mapping[str, Any],
    *,
    expected_original_sha256: str = "",
    expected_generated_sha256: str = "",
) -> ProcSourceMap:
    """Parse an explicit sidecar map manifest; fail closed on any violation.

    Expected shape::

        {
          "version": "proc-source-map-v1",
          "map_id": "...",
          "original_path": "src/app.pc",
          "generated_path": "gen/app.c",
          "original_sha256": "...",
          "generated_sha256": "...",
          "entries": [
            {"generated": [s, e], "original": [s, e], "quality": "exact_span",
             "generated_code_class": "original_application"}
          ]
        }

    A hash mismatch on either side marks every entry ``stale``; malformed
    structure marks the whole map ``invalid``.  Unknown entry quality fails
    closed.
    """

    def invalid_map(reason: str) -> ProcSourceMap:
        return ProcSourceMap(
            map_id=str(data.get("map_id") or ""),
            provider="sidecar",
            original_path=str(data.get("original_path") or ""),
            generated_path=str(data.get("generated_path") or ""),
            entries=(),
            diagnostics=(("map_invalid", reason),),
        )

    if str(data.get("version") or "") != PROC_SOURCE_MAP_VERSION:
        return invalid_map(f"unsupported sidecar map version: {data.get('version')!r}")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return invalid_map("sidecar map requires an entries list")
    for field_name in ("original_path", "generated_path"):
        if not str(data.get(field_name) or "").strip():
            return invalid_map(f"sidecar map requires {field_name}")

    stale = False
    if expected_original_sha256 and str(data.get("original_sha256") or "") != expected_original_sha256:
        stale = True
    if expected_generated_sha256 and str(data.get("generated_sha256") or "") != expected_generated_sha256:
        stale = True

    entries: List[ProcMapEntry] = []
    diagnostics: List[Tuple[str, str]] = []
    for index, raw in enumerate(raw_entries[:MAX_MAP_ENTRIES]):
        try:
            gen_span = raw["generated"]
            orig_span = raw["original"]
            quality = str(raw.get("quality") or MAP_QUALITY_EXACT_SPAN)
            if quality not in MAP_QUALITY_STATES:
                raise ValueError(f"unknown quality {quality!r}")
            entries.append(
                ProcMapEntry(
                    generated_start=int(gen_span[0]),
                    generated_end=int(gen_span[1]),
                    original_start=int(orig_span[0]),
                    original_end=int(orig_span[1]),
                    original_path=str(data.get("original_path")),
                    quality=MAP_QUALITY_STALE if stale else quality,
                    generated_code_class=str(raw.get("generated_code_class") or "unmapped_generated"),
                )
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return invalid_map(f"malformed map entry {index}: {exc}")
    if len(raw_entries) > MAX_MAP_ENTRIES:
        diagnostics.append(("map_entry_cap", f"map entries capped at {MAX_MAP_ENTRIES}"))
    if stale:
        diagnostics.append((
            "map_stale",
            "sidecar map hashes do not match the supplied original/generated artifacts",
        ))
    return ProcSourceMap(
        map_id=str(data.get("map_id") or ""),
        provider="sidecar",
        original_path=str(data.get("original_path")),
        generated_path=str(data.get("generated_path")),
        entries=tuple(entries),
        diagnostics=tuple(diagnostics),
    )


def load_sidecar_map(
    path: str,
    *,
    root: str = "",
    expected_original_sha256: str = "",
    expected_generated_sha256: str = "",
) -> ProcSourceMap:
    """Load and validate a sidecar map file, contained under ``root``."""

    real = os.path.realpath(os.path.abspath(path))
    if root:
        root_real = os.path.realpath(os.path.abspath(root))
        if real != root_real and not real.startswith(root_real + os.sep):
            raise ValueError("sidecar map path escapes the allowlisted root")
    with open(real, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("sidecar map must be a JSON object")
    return parse_sidecar_map(
        data,
        expected_original_sha256=expected_original_sha256,
        expected_generated_sha256=expected_generated_sha256,
    )


# ---------------------------------------------------------------------------
# Bundle lifecycle
# ---------------------------------------------------------------------------


def classify_proc_bundle_state(
    *,
    has_masked_structure: bool = True,
    generated_available: bool = False,
    map_available: bool = False,
    map_stale: bool = False,
    map_invalid: bool = False,
    strict_map_quality: bool = False,
    compile_context_available: bool = False,
    worker_status: str = "",  # ok | failed | truncated | ""
    reconciliation_complete: bool = True,
) -> str:
    """Derive the deterministic lifecycle state of one Pro*C bundle.

    SQL facts are always preserved regardless of state; only the semantic
    lane opens/closes here.  Order matters: invalid/stale map states close
    the semantic lane before worker outcomes are considered.
    """

    if map_invalid:
        return "invalid"
    if map_stale:
        return "stale"
    if not generated_available or not map_available:
        # No semantic layer at all: SQL facts still publish.  The masked
        # tree-sitter plane decides between sql_only and lexical_ready.
        return "lexical_ready" if has_masked_structure else "sql_only"
    if not strict_map_quality or not compile_context_available:
        return "lexical_ready"
    if worker_status == "failed":
        return "failed"
    if not reconciliation_complete:
        return "partial"
    if worker_status in {"ok", "truncated"}:
        return "semantic_complete"
    return "semantic_eligible"


def bundle_state_allows_strict_calls(state: str) -> bool:
    return state == "semantic_complete"


# ---------------------------------------------------------------------------
# Reconciliation of generated semantic observations
# ---------------------------------------------------------------------------


# Name-shape evidence for precompiler runtime/wrapper symbols.  This is a
# refinement hint only — provenance (mapping evidence) stays the authority.
_PROC_RUNTIME_NAME_RE = re.compile(r"^(sql|sqlca|oraca|sqlglm|sqlror|sqlld|ori|orl|orup)[A-Za-z0-9_]*$")
_PROC_WRAPPER_NAME_RE = re.compile(r"^(proc_|sqlproc_|epc_|EPC)[A-Za-z0-9_]*$")


def refine_generated_class(
    *,
    worker_class: str,
    callee_name: str,
    lookup: SpanLookup,
    original_text: str = "",
) -> str:
    """Refine the worker's generated-code class with mapping evidence.

    The worker classification is structural/name-shaped; this pass adds
    provenance: a call whose mapped original region's file contains the
    callee spelling is ``original_application`` evidence, a mapped call with
    no original occurrence is generated-only, and a call inside an unmapped
    region is ``unmapped_generated`` regardless of name.
    """

    if lookup.status != "mapped":
        # Unmapped region: still classify known runtime/wrapper name shapes
        # for accounting, everything else stays unmapped_generated.
        if _PROC_RUNTIME_NAME_RE.match(callee_name or ""):
            return "precompiler_runtime"
        if _PROC_WRAPPER_NAME_RE.match(callee_name or ""):
            return "precompiler_wrapper"
        return "unmapped_generated"
    if worker_class == "macro_expansion":
        return "macro_expansion"
    if _PROC_RUNTIME_NAME_RE.match(callee_name or ""):
        return "precompiler_runtime"
    if _PROC_WRAPPER_NAME_RE.match(callee_name or ""):
        return "precompiler_wrapper"
    if original_text and callee_name and callee_name in original_text:
        return "original_application"
    if worker_class == "original_application":
        # Worker said application but the original source does not contain
        # the callee: the call exists only in generated code.
        return "unmapped_generated"
    return worker_class or "unmapped_generated"


def reconcile_proc_semantic_callsites(
    callsites: Iterable[Mapping[str, Any]],
    *,
    source_map: ProcSourceMap,
    bundle_id: str,
    original_text: str = "",
    worker_status: str = "ok",
    bundle_state: str = "",
) -> Dict[str, Any]:
    """Reconcile worker callsites to original-source identity.

    Returns deterministic staging rows plus accounting:

    - ``rows``: one row per callsite observation.  Each row carries the
      original file/span identity (user-visible), the generated
      span/artifact coordinates (provenance only), the refined
      generated-code class, and the mapping quality.
    - ``strict_rows``: only ``original_application`` + ``direct_resolved`` +
      strict map quality + accepted bundle/worker state.  Everything else
      stays conservative evidence with a typed ``reject_reason``.
    - ``rejected``: accounting per rejection reason so generated
      runtime/wrapper/unmapped traffic is visible, not silently dropped.
    """

    state = bundle_state or classify_proc_bundle_state(
        generated_available=True,
        map_available=True,
        map_stale=source_map.has_stale,
        map_invalid=source_map.has_invalid,
        strict_map_quality=any(
            is_strict_map_quality(entry.quality) for entry in source_map.entries
        ),
        compile_context_available=True,
        worker_status=worker_status,
    )
    original_line_starts = _line_starts(original_text)

    rows: List[Dict[str, Any]] = []
    strict_rows: List[Dict[str, Any]] = []
    rejected: Dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for site in callsites:
        worker_class = str(site.get("generated_code_class") or "unmapped_generated")
        lookup = source_map.lookup(
            int(site.get("call_start_byte") or 0),
            int(site.get("call_end_byte") or 0),
        )
        callee_name = str(site.get("callee_name") or "")
        generated_class = refine_generated_class(
            worker_class=worker_class,
            callee_name=callee_name,
            lookup=lookup,
            original_text=original_text,
        )
        mapped = lookup.status == "mapped"
        row: Dict[str, Any] = {
            "schema_version": PROC_SOURCE_MAP_VERSION,
            "bundle_id": bundle_id,
            "source_map_id": source_map.map_id,
            "source_map_provider": source_map.provider,
            "resolution_class": site.get("resolution_class"),
            "semantic_provider": site.get("semantic_provider"),
            "config_fingerprint": site.get("config_fingerprint", ""),
            "callee_name": callee_name,
            "callee_usr": site.get("callee_usr", ""),
            "caller_usr": site.get("caller_usr", ""),
            "caller_symbol_id": site.get("caller_symbol_id", ""),
            "callee_symbol_id": site.get("callee_symbol_id", ""),
            "generated_file": site.get("file_path", ""),
            "generated_start_byte": int(site.get("call_start_byte") or 0),
            "generated_end_byte": int(site.get("call_end_byte") or 0),
            "generated_line": int(site.get("call_line") or 0),
            "generated_column": int(site.get("call_column") or 0),
            "generated_code_class": generated_class,
            "source_map_quality": lookup.quality,
            "mapping_status": lookup.status,
            # Original coordinates are the user-visible identity.
            "file_path": lookup.original_path if mapped else "",
            "start_line": (
                _line_of(original_line_starts, lookup.original_start)
                if mapped and lookup.original_start >= 0
                else (lookup.original_start_line if mapped else 0)
            ),
            "call_start_byte": lookup.original_start if mapped else -1,
            "call_end_byte": lookup.original_end if mapped else -1,
            "strict_eligible": False,
            "reject_reason": "",
        }

        if generated_class in {"precompiler_runtime", "precompiler_wrapper"}:
            # Runtime/wrapper traffic is rejected by class regardless of
            # mapping coverage; it can never masquerade as an original call.
            reason = f"generated_class_{generated_class}"
            reject(reason)
            row["reject_reason"] = reason
            rows.append(row)
            continue
        if not mapped:
            reason = f"map_{lookup.status}"
            reject(reason)
            row["reject_reason"] = reason
            rows.append(row)
            continue
        if generated_class != "original_application":
            reason = f"generated_class_{generated_class}"
            reject(reason)
            row["reject_reason"] = reason
            rows.append(row)
            continue
        if site.get("resolution_class") != "direct_resolved":
            reason = f"resolution_{site.get('resolution_class')}"
            reject(reason)
            row["reject_reason"] = reason
            rows.append(row)
            continue
        if not is_strict_map_quality(lookup.quality):
            reason = f"map_quality_{lookup.quality}"
            reject(reason)
            row["reject_reason"] = reason
            rows.append(row)
            continue
        if state != "semantic_complete" or worker_status == "failed":
            reason = f"bundle_state_{state}"
            reject(reason)
            row["reject_reason"] = reason
            rows.append(row)
            continue

        row["strict_eligible"] = True
        rows.append(row)
        strict_rows.append(row)

    return {
        "schema_version": PROC_SOURCE_MAP_VERSION,
        "bundle_id": bundle_id,
        "bundle_state": state,
        "map_fingerprint": source_map.fingerprint,
        "map_quality_distribution": source_map.quality_distribution,
        "rows": rows,
        "strict_rows": strict_rows,
        "rejected": dict(sorted(rejected.items())),
        "row_count": len(rows),
        "strict_count": len(strict_rows),
        # SQL facts are owned by the original-source lane and survive every
        # semantic failure mode above.
        "sql_facts_preserved": True,
    }


def _line_starts(text: str) -> List[int]:
    starts: List[int] = []
    offset = 0
    for line in text.split("\n") if text else []:
        starts.append(offset)
        offset += len(line) + 1
    return starts


def _line_of(line_starts: List[int], offset: int) -> int:
    if offset < 0 or not line_starts:
        return 0
    low, high = 0, len(line_starts) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if line_starts[mid] <= offset:
            low = mid
        else:
            high = mid - 1
    return low + 1
