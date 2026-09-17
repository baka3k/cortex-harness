# VB6 Fixture Baseline (regex engine) — reference zero

Recorded by tests/test_vb6_baseline.py (phase 01). All later
improvement claims (M1/M2/M4) are measured against these numbers.

- date: 2026-09-17
- engine: regex (parse_vb_file + resolve_calls)
- files parsed with >=1 function: 11
- functions extracted: 30
- raw call edges: 39
- resolved after resolve_calls: 37 / 39

## Capture against expected.json

- expected callsites: 47
- expected-resolvable (M2 denominator): 29
- captured (name match): 20 / 47
- captured of expected-resolvable: 18 / 29
- string-literal trap false positives: 1 (modMain.bas:29:Fake)

Known regex limitations demonstrated by this baseline: no-paren Sub
calls, `Call x`, line continuations and unqualified cross-module
calls are invisible to _CALL_RE (requires '('); string literals can
produce false calls; resolve_calls picks sorted(candidates)[0] with
no arity/project model.
