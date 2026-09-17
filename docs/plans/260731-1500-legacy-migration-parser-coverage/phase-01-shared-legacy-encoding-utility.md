# Phase 01: Shared Legacy-Encoding Utility

## Context

Every downstream phase (Pro*C, shell, JP1, INI) reads Shift-JIS (CP932) legacy source files. No shared encoding-detection utility exists today: `cobol/parser.py` has a cp037/cp1252 fallback with COBOL-keyword scoring, and `cplus/rc_parser.py` has BOM sniffing plus a `("utf-8", "cp932")` fallback chain, but neither is reusable outside its own module. Building this first avoids four parsers each re-inventing (and inconsistently getting wrong) encoding detection.

## Requirements

- New module `code-tiny/tools/common/legacy_encoding.py` exposing `read_legacy_text(path: str) -> LegacyTextResult` (text, chosen encoding name, list of diagnostics).
- Detection order: BOM sniff (UTF-8/UTF-16 BE/LE, reuse the heuristic already in `rc_parser.decode_rc_bytes`) → attempt strict UTF-8 decode → attempt CP932 (Shift-JIS) decode → CP1252 with `errors="replace"` as last resort.
- When both UTF-8 and CP932 decode without raising, prefer UTF-8 only if re-encoding the decoded text back to UTF-8 bytes reproduces the original bytes; otherwise prefer CP932. Record the chosen encoding as a diagnostic (`info` severity) so silent mojibake is never truly silent.
- Do not change behavior for existing callers yet (this phase only adds the utility + tests); wiring it into `cplus_analyzer`, the new `shell`/`jp1` analyzers, and the `.ini` descriptor parser happens in their own phases.
- Add at least one CP932-encoded fixture (small synthetic Japanese-comment C or shell snippet) plus one UTF-8 fixture and one UTF-16 (BOM) fixture to prove all three paths.

## Architecture

Pure-function module, no I/O side effects beyond reading the given path. Returns a small dataclass:

```python
@dataclass(frozen=True)
class LegacyTextResult:
    text: str
    encoding: str
    diagnostics: List[Diagnostic]
```

Reuses the existing `Diagnostic` type already defined for COBOL (`code-tiny/tools/cobol/models.py` or wherever it's declared) if it's generic enough to import from `tools.common`; otherwise define an equally-shaped local diagnostic dataclass in `tools/common` and have COBOL's type interoperate structurally (same fields), not by forcing a cross-import that couples unrelated analyzers.

## Related Files

Create:
- `code-tiny/tools/common/legacy_encoding.py`
- `tests/test_legacy_encoding.py`
- `tests/fixtures/legacy-encoding/utf8_sample.txt`
- `tests/fixtures/legacy-encoding/cp932_sample.txt` (binary, hand-crafted Shift-JIS bytes)
- `tests/fixtures/legacy-encoding/utf16_bom_sample.txt`

Reference:
- `code-tiny/tools/cplus/rc_parser.py` (BOM sniff + cp932 fallback pattern)
- `code-tiny/tools/cobol/parser.py:50-65` (fallback-chain-with-scoring pattern)
