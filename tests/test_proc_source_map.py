"""Phase 05 Pro*C source-map, reconciliation, bundle-state, and masking
fingerprint tests, plus the adversarial lexical/alignment regression corpus."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.proc_analyzer import prepare_proc_bytes
from tools.cplus.proc_source_map import (
    ALLOW_EXACT_LINE_PROMOTION,
    MASK_POLICY_VERSION,
    MAP_QUALITY_EXACT_LINE,
    MAP_QUALITY_EXACT_SPAN,
    MAP_QUALITY_INFERRED,
    MAP_QUALITY_INVALID,
    MAP_QUALITY_LINE_DIRECTIVE,
    MAP_QUALITY_MISSING,
    MAP_QUALITY_STALE,
    PROC_SOURCE_MAP_VERSION,
    ProcMapEntry,
    ProcSourceMap,
    bundle_state_allows_strict_calls,
    classify_proc_bundle_state,
    is_strict_map_quality,
    load_sidecar_map,
    parse_line_directive_map,
    parse_sidecar_map,
    proc_masking_fingerprint,
    reconcile_proc_semantic_callsites,
)

FIXTURES = ROOT / "tests" / "fixtures" / "proc_source_map"


# ---------------------------------------------------------------------------
# Adversarial lexical / alignment corpus
# ---------------------------------------------------------------------------


def _assert_mask_alignment(prepared) -> None:
    """Masked input must preserve byte length and newline placement."""
    assert len(prepared.masked_bytes) == len(prepared.source_bytes)
    for original, masked in zip(prepared.source_bytes, prepared.masked_bytes):
        assert (original == 0x0A) == (masked == 0x0A)


def test_mask_alignment_comments_literals_semicolons() -> None:
    source = (
        "int f(void) {\n"
        "  /* comment with ; and EXEC SQL SELECT * FROM DUAL; */\n"
        "  const char *s = \"EXEC SQL DELETE FROM X WHERE N = '; '\";\n"
        "  char c = ';';\n"
        "  EXEC SQL SELECT COUNT(*) INTO :n FROM EMP;\n"
        "  return 0;\n"
        "}\n"
    )
    prepared = prepare_proc_bytes(source.encode("utf-8"))
    _assert_mask_alignment(prepared)
    # Only the real EXEC SQL region is masked out of SQL authority.
    assert [region.operation_upper for region in prepared.regions] == ["SELECT"]
    assert prepared.regions[0].targets == ("EMP",)


def test_mask_alignment_raw_strings_and_multiline_sql() -> None:
    source = (
        "auto q = R\"x(EXEC SQL DROP TABLE NOISE;)x\";\n"
        "int g(void) {\n"
        "  EXEC SQL SELECT NAME\n"
        "     INTO :nm\n"
        "     FROM CUSTOMER\n"
        "     WHERE ID = :id;\n"
        "  EXEC SQL ROLLBACK;\n"
        "}\n"
    )
    prepared = prepare_proc_bytes(source.encode("utf-8"))
    _assert_mask_alignment(prepared)
    assert [region.operation_upper for region in prepared.regions] == ["SELECT", "ROLLBACK"]
    assert prepared.regions[0].start_line == 3
    assert prepared.regions[0].end_line == 6
    assert set(prepared.regions[0].host_variables) == {"nm", "id"}


def test_mask_alignment_unterminated_block_is_diagnosed() -> None:
    source = "int h(void) {\n  EXEC SQL SELECT 1 INTO :x FROM DUAL\n  return 0\n}\n"
    prepared = prepare_proc_bytes(source.encode("utf-8"))
    _assert_mask_alignment(prepared)
    codes = {d.code for d in prepared.diagnostics}
    assert "unterminated_exec_sql" in codes


def test_mask_alignment_cp932_and_lossy_decode() -> None:
    # CP932 multibyte comment before real SQL: byte offsets must stay aligned.
    source = "/* 日本語コメント ; */\nint k(void) {\n  EXEC SQL SELECT 2 INTO :y FROM DUAL;\n}\n"
    prepared = prepare_proc_bytes(source.encode("cp932"))
    _assert_mask_alignment(prepared)
    assert prepared.encoding == "cp932"
    assert [region.operation_upper for region in prepared.regions] == ["SELECT"]

    # Bytes valid in neither UTF-8 nor CP932 (0x81 0x7F is an invalid CP932
    # pair): lossy decode keeps the source/masked pair length-aligned in the
    # normalized UTF-8 domain.
    raw = b"\x81\x7f EXEC SQL SELECT 3 INTO :z FROM DUAL;\n"
    lossy = prepare_proc_bytes(raw)
    assert any(d.code == "encoding_replacement" for d in lossy.diagnostics)
    assert len(lossy.source_bytes) == len(lossy.masked_bytes)
    assert [r.operation_upper for r in lossy.regions] == ["SELECT"]


def test_mask_alignment_embedded_plsql_block() -> None:
    source = (
        "int p(void) {\n"
        "  EXEC SQL EXECUTE\n"
        "  BEGIN\n"
        "    UPDATE EMP SET SAL = SAL * 2 WHERE DEPTNO = :d;\n"
        "  END;\n"
        "  END-EXEC;\n"
        "}\n"
    )
    prepared = prepare_proc_bytes(source.encode("utf-8"))
    _assert_mask_alignment(prepared)
    assert len(prepared.regions) >= 1
    assert prepared.regions[0].operation_upper == "EXECUTE"


# ---------------------------------------------------------------------------
# Masking fingerprint (parse-cache invalidation)
# ---------------------------------------------------------------------------


def test_masking_fingerprint_is_versioned_and_source_bound() -> None:
    one = proc_masking_fingerprint("a" * 64)
    two = proc_masking_fingerprint("b" * 64)
    assert one.startswith(MASK_POLICY_VERSION + ":")
    assert one != two
    assert proc_masking_fingerprint("a" * 64) == one


# ---------------------------------------------------------------------------
# #line directive provider
# ---------------------------------------------------------------------------


def test_line_directive_map_regions_and_quality() -> None:
    generated = (FIXTURES / "app_gen.c").read_text(encoding="utf-8")
    source_map = parse_line_directive_map(
        generated, original_path="app.pc", generated_path="app_gen.c"
    )
    assert source_map.provider == "line_directive"
    assert source_map.entries
    assert all(
        entry.quality == MAP_QUALITY_LINE_DIRECTIVE for entry in source_map.entries
    )
    # Never byte-exact: original byte spans stay unknown.
    assert all(entry.original_start < 0 for entry in source_map.entries)

    first = source_map.entries[0]
    assert first.original_start_line == 5  # #line 5 "app.pc"
    lookup = source_map.lookup(first.generated_start, first.generated_start + 10)
    assert lookup.status == "mapped"
    assert lookup.quality == MAP_QUALITY_LINE_DIRECTIVE
    assert lookup.original_start_line == 5
    assert not is_strict_map_quality(lookup.quality)

    # Preamble before the first directive is unmapped.
    preamble_end = generated.index("#line")
    missing = source_map.lookup(0, preamble_end - 1)
    assert missing.status == "missing"


def test_line_directive_map_without_directives() -> None:
    source_map = parse_line_directive_map("int x;\n", original_path="a.pc")
    assert source_map.entries == ()
    assert source_map.diagnostics[0][0] == "map_no_line_directives"
    assert source_map.lookup(0, 5).status == "missing"


# ---------------------------------------------------------------------------
# Sidecar provider
# ---------------------------------------------------------------------------


def _sidecar_data(quality=MAP_QUALITY_EXACT_SPAN, **overrides):
    generated = (FIXTURES / "app_gen.c").read_text(encoding="utf-8")
    original = (FIXTURES / "app.pc").read_text(encoding="utf-8")
    call = generated.index("helper_validate(customer_name)")
    orig_call = original.index("helper_validate(customer_name)")
    data = {
        "version": PROC_SOURCE_MAP_VERSION,
        "map_id": "map-1",
        "original_path": "app.pc",
        "generated_path": "app_gen.c",
        "original_sha256": "o" * 64,
        "generated_sha256": "g" * 64,
        "entries": [
            {
                "generated": [call, call + len("helper_validate(customer_name)")],
                "original": [orig_call, orig_call + len("helper_validate(customer_name)")],
                "quality": quality,
                "generated_code_class": "original_application",
            }
        ],
    }
    data.update(overrides)
    return data, generated, original


def test_sidecar_map_exact_span_lookup() -> None:
    data, generated, original = _sidecar_data()
    source_map = parse_sidecar_map(data)
    assert source_map.provider == "sidecar"
    entry = source_map.entries[0]
    lookup = source_map.lookup(entry.generated_start, entry.generated_end)
    assert lookup.status == "mapped"
    assert lookup.quality == MAP_QUALITY_EXACT_SPAN
    assert is_strict_map_quality(lookup.quality)
    assert original[lookup.original_start:lookup.original_end] == "helper_validate(customer_name)"


def test_sidecar_map_hash_mismatch_is_stale() -> None:
    data, _, _ = _sidecar_data()
    data["generated_sha256"] = "x" * 64
    source_map = parse_sidecar_map(data, expected_generated_sha256="g" * 64)
    assert source_map.has_stale
    entry = source_map.entries[0]
    lookup = source_map.lookup(entry.generated_start, entry.generated_end)
    assert lookup.status == "stale"
    assert lookup.quality == MAP_QUALITY_STALE


def test_sidecar_map_malformed_entries_are_invalid() -> None:
    data, _, _ = _sidecar_data()
    data["entries"] = [{"generated": [5, 3]}]
    source_map = parse_sidecar_map(data)
    assert source_map.has_invalid
    assert source_map.lookup(0, 10).status == "invalid"

    bad_version, _, _ = _sidecar_data()
    bad_version["version"] = "proc-source-map-v0"
    assert parse_sidecar_map(bad_version).has_invalid


def test_sidecar_map_root_containment(tmp_path: Path) -> None:
    import json

    data, _, _ = _sidecar_data()
    inside = tmp_path / "map.json"
    inside.write_text(json.dumps(data), encoding="utf-8")
    assert load_sidecar_map(str(inside), root=str(tmp_path)).map_id == "map-1"
    try:
        load_sidecar_map(str(inside), root=str(tmp_path / "elsewhere"))
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("sidecar map outside the root must be rejected")


def test_map_conflicting_entries() -> None:
    data, generated, _ = _sidecar_data()
    span = data["entries"][0]
    data["entries"].append({**span, "original": [1, 20]})
    source_map = parse_sidecar_map(data)
    lookup = source_map.lookup(span["generated"][0], span["generated"][1])
    assert lookup.status == "conflict"
    assert lookup.quality == MAP_QUALITY_INVALID


# ---------------------------------------------------------------------------
# Strict mapping gate
# ---------------------------------------------------------------------------


def test_exact_line_not_strict_by_default() -> None:
    assert not ALLOW_EXACT_LINE_PROMOTION
    assert is_strict_map_quality(MAP_QUALITY_EXACT_SPAN)
    for quality in (
        MAP_QUALITY_EXACT_LINE,
        MAP_QUALITY_LINE_DIRECTIVE,
        MAP_QUALITY_INFERRED,
        MAP_QUALITY_MISSING,
        MAP_QUALITY_STALE,
        MAP_QUALITY_INVALID,
    ):
        assert not is_strict_map_quality(quality)


# ---------------------------------------------------------------------------
# Bundle lifecycle
# ---------------------------------------------------------------------------


def test_bundle_state_lifecycle() -> None:
    assert classify_proc_bundle_state(has_masked_structure=False) == "sql_only"
    assert classify_proc_bundle_state() == "lexical_ready"
    assert classify_proc_bundle_state(
        generated_available=True, map_available=True
    ) == "lexical_ready"  # no strict-quality map / context yet
    assert classify_proc_bundle_state(
        generated_available=True,
        map_available=True,
        strict_map_quality=True,
        compile_context_available=True,
    ) == "semantic_eligible"
    assert classify_proc_bundle_state(
        generated_available=True,
        map_available=True,
        strict_map_quality=True,
        compile_context_available=True,
        worker_status="ok",
    ) == "semantic_complete"
    assert classify_proc_bundle_state(
        generated_available=True,
        map_available=True,
        strict_map_quality=True,
        compile_context_available=True,
        worker_status="ok",
        reconciliation_complete=False,
    ) == "partial"
    assert classify_proc_bundle_state(
        generated_available=True, map_available=True, map_stale=True
    ) == "stale"
    assert classify_proc_bundle_state(
        generated_available=True, map_available=True, map_invalid=True
    ) == "invalid"
    assert classify_proc_bundle_state(
        generated_available=True,
        map_available=True,
        strict_map_quality=True,
        compile_context_available=True,
        worker_status="failed",
    ) == "failed"
    assert bundle_state_allows_strict_calls("semantic_complete")
    for state in ("sql_only", "lexical_ready", "partial", "stale", "invalid", "failed"):
        assert not bundle_state_allows_strict_calls(state)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _callsites(generated: str):
    def site(name, occurrence, cls="direct_resolved", generated_class="original_application"):
        start = generated.index(name, occurrence)
        return {
            "file_path": "app_gen.c",
            "call_start_byte": start,
            "call_end_byte": start + len(name),
            "call_line": 1,
            "call_column": 1,
            "callee_name": name.split("(")[0],
            "callee_usr": f"c:@F@{name.split('(')[0]}",
            "caller_usr": "c:@F@load_customer",
            "caller_symbol_id": "load_customer/1@app_gen.c",
            "callee_symbol_id": f"{name.split('(')[0]}/1@app_gen.c",
            "resolution_class": cls,
            "semantic_provider": "clang_worker",
            "config_fingerprint": "cfg-1",
            "tu_key": "app_gen.c",
            "generated_code_class": generated_class,
        }

    return [
        site("log_message(", 0),
        site("helper_validate(", 0),
        site("sqlcxt(", 0),  # precompiler runtime
        site("helper_validate(", 1) if generated.count("helper_validate(") > 1 else None,
    ]


def test_reconciliation_strict_and_generated_filtering() -> None:
    generated = (FIXTURES / "app_gen.c").read_text(encoding="utf-8")
    original = (FIXTURES / "app.pc").read_text(encoding="utf-8")
    # Sidecar covers only the application-call regions, not the runtime block.
    log_span = generated.index("log_message(")
    helper_span = generated.index("helper_validate(")
    runtime_span = generated.index("sqlcxt(")
    orig_log = original.index("log_message(")
    orig_helper = original.index("helper_validate(")

    def entry(gen_start, gen_end, orig_start, orig_end, quality=MAP_QUALITY_EXACT_SPAN):
        return {
            "generated": [gen_start, gen_end],
            "original": [orig_start, orig_end],
            "quality": quality,
            "generated_code_class": "original_application",
        }

    data = {
        "version": PROC_SOURCE_MAP_VERSION,
        "map_id": "map-strict",
        "original_path": "app.pc",
        "generated_path": "app_gen.c",
        "entries": [
            entry(log_span, log_span + len("log_message("), orig_log, orig_log + len("log_message(")),
            entry(
                helper_span,
                helper_span + len("helper_validate("),
                orig_helper,
                orig_helper + len("helper_validate("),
            ),
        ],
    }
    source_map = parse_sidecar_map(data)
    callsites = [s for s in _callsites(generated) if s]
    result = reconcile_proc_semantic_callsites(
        callsites,
        source_map=source_map,
        bundle_id="bundle-1",
        original_text=original,
        worker_status="ok",
    )

    assert result["sql_facts_preserved"] is True
    strict_names = {row["callee_name"] for row in result["strict_rows"]}
    assert strict_names == {"log_message", "helper_validate"}

    # Strict rows carry original user-visible identity plus generated
    # provenance.
    for row in result["strict_rows"]:
        assert row["file_path"] == "app.pc"
        assert row["call_start_byte"] >= 0
        assert row["generated_file"] == "app_gen.c"
        assert original[row["call_start_byte"]:row["call_end_byte"]].startswith(
            row["callee_name"]
        )

    by_name = {row["callee_name"]: row for row in result["rows"]}
    # Runtime call rejected by generated class, never a strict source call.
    assert by_name["sqlcxt"]["generated_code_class"] == "precompiler_runtime"
    assert by_name["sqlcxt"]["reject_reason"] == "generated_class_precompiler_runtime"
    assert result["rejected"].get("generated_class_precompiler_runtime") == 1


def test_reconciliation_rejects_without_strict_map() -> None:
    generated = (FIXTURES / "app_gen.c").read_text(encoding="utf-8")
    original = (FIXTURES / "app.pc").read_text(encoding="utf-8")
    line_map = parse_line_directive_map(generated, original_path="app.pc")
    callsites = [s for s in _callsites(generated) if s]
    result = reconcile_proc_semantic_callsites(
        callsites,
        source_map=line_map,
        bundle_id="bundle-2",
        original_text=original,
        worker_status="ok",
    )
    # #line maps map regions but carry conservative quality: no strict calls.
    assert result["strict_count"] == 0
    assert result["rejected"].get("map_quality_line_directive", 0) >= 1
    # Weak evidence is retained, not dropped.
    assert result["row_count"] >= 1
    assert result["sql_facts_preserved"] is True


def test_reconciliation_worker_failure_preserves_rows() -> None:
    generated = (FIXTURES / "app_gen.c").read_text(encoding="utf-8")
    original = (FIXTURES / "app.pc").read_text(encoding="utf-8")
    data, _, _ = _sidecar_data()
    source_map = parse_sidecar_map(data)
    callsites = [s for s in _callsites(generated) if s]
    result = reconcile_proc_semantic_callsites(
        callsites,
        source_map=source_map,
        bundle_id="bundle-3",
        original_text=original,
        worker_status="failed",
    )
    assert result["bundle_state"] == "failed"
    assert result["strict_count"] == 0
    assert result["rejected"].get("bundle_state_failed", 0) >= 1
    assert result["sql_facts_preserved"] is True


def test_map_fingerprint_deterministic() -> None:
    data, _, _ = _sidecar_data()
    one = parse_sidecar_map(data)
    two = parse_sidecar_map(_sidecar_data()[0])
    assert one.fingerprint == two.fingerprint
    changed = _sidecar_data()[0]
    changed["entries"][0]["original"] = [1, 2]
    assert parse_sidecar_map(changed).fingerprint != one.fingerprint
