#!/usr/bin/env python3
"""Phase-06 vector-plane component fixtures (plan 260915-analyzer-layer-rust-cutover).

Generates the evidence fixtures the spike NO-GO said cosine gates "might not
catch": deterministic point ids (G1), 3-tier redaction (G2), message-lane
_hash_vector (G3), stale-filter + create-collection bodies (G4) and the
end-to-end rows→documents construction (G5 inputs). Every expected value is
produced by importing the REAL Python reference modules — this script never
re-implements the logic under test.

Run from the repo root:
    .venv/bin/python scripts/rust_parity/gen_vector_plane_fixtures.py
Outputs land in rust/crates/cortex-sync/tests/fixtures/ (committed).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO))

from tools.common import primary_vector_sync as pvs  # noqa: E402
from tools.common import message_scan  # noqa: E402
from tools.common.local_qdrant import model_to_dict  # noqa: E402

OUT = REPO / "rust" / "crates" / "cortex-sync" / "tests" / "fixtures"

# The redaction corpus necessarily contains credential-SHAPED strings (that is
# the G2 surface under test). The repo's sensitive-guard pre-commit hook flags
# any added line carrying those shapes unless the line also carries its
# documented allow marker. Fixture JSON is emitted one object per line, each
# with a `_guard` field, so every data line holds the marker (values stay
# byte-exact; the extra key is ignored by the reader).
GUARD = "sensitive-guard:allow synthetic redaction corpus"


def write(name: str, payload: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n", "utf-8")
    print(f"[fixtures] wrote {path.relative_to(REPO)}")


def write_g2(path_cases: list[dict], bounded_cases: list[dict]) -> None:
    """One object per line, each carrying the guard marker on its own line."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "phase06_g2_redaction.json"

    def line(obj: dict) -> str:
        merged = dict(obj)
        merged["_guard"] = GUARD
        return json.dumps(merged, ensure_ascii=False, sort_keys=False)

    body = ["{", f'  "cases": [']
    body += ["    " + line(c) + ("," if i < len(path_cases) - 1 else "") for i, c in enumerate(path_cases)]
    body += ["  ],", '  "bounded": [']
    body += ["    " + line(c) + ("," if i < len(bounded_cases) - 1 else "") for i, c in enumerate(bounded_cases)]
    body += ["  ]", "}"]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"[fixtures] wrote {path.relative_to(REPO)} (guarded one-object-per-line)")


# ── G1: deterministic point ids ─────────────────────────────────────────────
def g1_point_ids() -> None:
    cases: list[dict] = []

    def ok(parser: str, project_id: str, symbol_id: str, root_scope: str) -> None:
        cases.append(
            {
                "input": [parser, project_id, symbol_id, root_scope],
                "expect_error": False,
                "id": pvs.deterministic_point_id(parser, project_id, symbol_id, root_scope),
            }
        )

    def err(parser: str, project_id: str, symbol_id: str, root_scope: str) -> None:
        cases.append({"input": [parser, project_id, symbol_id, root_scope], "expect_error": True})

    ok("go", "Proj", "func:main", "/repo")
    ok("Go ", " Proj ", " func:main ", " /repo ")  # strip + parser-only lower
    ok("SWIFT", "aws-sa-associate", "cls:Foo/Bar", "/x/y")
    ok("perl", "p\é", "sým:ból:ids", "r\u00f6ot")  # non-ASCII in every field
    ok("shell", "p", "s", "r\u00a0")  # NBSP survives as stripped? NBSP IS python-whitespace → stripped
    ok("shell", "p", "s", "r\u0085x")  # NEL inside value stays (strip only at edges)
    ok("go", "p", "s", "a\u001cb")  # inner FS stays in scope (between edges)
    ok("go", "p\u001c", "s", "R")  # trailing FS stripped by Python (Rust must too)
    ok("jp1", "p", "s", "R\u001f")  # trailing US stripped
    ok("rust", "İ", "S", "R")  # Turkish dotted capital: lower -> 'i' + U+0307
    ok("dart", "ΑΣΒ", "Σ", "ς")  # Greek; only parser is lowered — symbol case kept
    ok("go", "p", "s" * 500, "r" * 500)
    ok("go ", "p", "s\t", "r\n")  # tabs/newlines stripped at edges
    err("", "p", "s", "r")
    err("go", "   ", "s", "r")
    err("go", "p", "", "r")
    err("go", "p", "s", "\u001c\u001d\u001e\u001f")  # whitespace-only scope after strip
    write("phase06_g1_point_ids.json", {"generator": __file__.split("scripts/")[-1], "cases": cases})


# ── G2: redaction ────────────────────────────────────────────────────────────
REDACTION_SAMPLES = [
    "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJ\n-----END RSA PRIVATE KEY-----",  # sensitive-guard:allow synthetic PEM-shape fixture
    "-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl\nc2hl\n-----END OPENSSH PRIVATE KEY-----",  # sensitive-guard:allow synthetic PEM-shape fixture
    "-----BEGIN EC PRIVATE KEY-----x-----END EC PRIVATE KEY-----",  # sensitive-guard:allow synthetic PEM-shape fixture
    "-----begin rsa private key-----\nlowercase\n-----end rsa private key-----",  # no match (case-sensitive)
    "api_key= \"quoted value\"",  # sensitive-guard:allow synthetic test input
    "api_key = 'a', other_token: 'b';",  # sensitive-guard:allow synthetic test input
    "password=\"line1\nline2\"",  # sensitive-guard:allow | DOTALL lazy value with newline
    'secret = ""',  # sensitive-guard:allow | empty quoted value
    'token="x" plus "y" text "z"',  # sensitive-guard:allow | lazy pairing across multiple quoted runs
    "TOKENIZE = not_a_secret",  # keyword prefix must still require separator
    "tokenizer='value'",  # 'token' inside longer word must not fire
    "my secret: hunter2",  # sensitive-guard:allow | bare 'secret' word + colon
    "apikey:12345",  # sensitive-guard:allow | no space after colon, unquoted digits
    "API-KEY=abc,DEF;ghi",  # sensitive-guard:allow | unquoted stops at comma
    "access_token\t=\tsecret value",  # tabs around =
    "auth-token='  spaces inside  '",  # sensitive-guard:allow synthetic test input
    "passwd=\u001cweird",  # sensitive-guard:allow | python \s stops value at U+001C
    "before password='p1' and password=\"p2\" and secret=s3 after",  # sensitive-guard:allow synthetic test input
    "password: 'unclosed",  # sensitive-guard:allow | tier-2 needs closing quote → falls to tier-3? ' included in [^\s,;]+
    "secret=[REDACTED]",  # sensitive-guard:allow | idempotence probe: second pass must not change
    "key=abc\npassword='x\ny'\nsecret=z ;more",  # sensitive-guard:allow synthetic test input
    "plain text with no secrets at all",
    "",
    "\u001c\u001d",
    "🔑 password='réfúgio 🚀'",  # sensitive-guard:allow synthetic test input
    "url=https://example.com/a?token=q",  # sensitive-guard:allow | 'token' inside query, unquoted value
    "SECRET_KEY='multi'\n'SECRET': 'quoted-key-colon'\n",  # sensitive-guard:allow synthetic test input
    "password='a'== 'b'",  # sensitive-guard:allow | match boundary after quote close
]


def g2_redaction() -> None:
    cases = [{"input": s, "redacted": pvs.redact_text(s)} for s in REDACTION_SAMPLES]
    bounded = []
    for max_chars in (4000, 50, 10):
        long = "x" * 20_000
        bounded.append({"input": [long], "max_chars": max_chars, "output": pvs._bounded_text([long], max_chars)})
    bounded.append({"input": ["a", None, "", "c"], "max_chars": 4000, "output": pvs._bounded_text(["a", None, "", "c"], 4000)})
    bounded.append(
        {
            "input": ["function: main.run", "file: src/a.go", "sümmary", None, "note", "code line"],
            "max_chars": 4000,
            "output": pvs._bounded_text(
                ["function: main.run", "file: src/a.go", "sümmary", None, "note", "code line"], 4000
            ),
        }
    )
    write_g2(cases, bounded)


# ── G3: _hash_vector ─────────────────────────────────────────────────────────
def g3_hash_vector() -> None:
    texts = [
        "hello",
        "sender | receiver | payload | explanation",
        "a.b:c_d:e",  # ':' and '.' are token chars
        "UPPER lower MiXeD",
        "İstanbul ẞ ΑΣΒΣ ﬁ",  # unicode lower traps (turkish, sharp s, final sigma, ligature)
        "ΑΣ ΑΣ.ΑΣ ΑΣ1",  # final-sigma context: '.'/'1' are token chars → not word-end
        "🚀 rocket",  # emoji dropped by ASCII token class
        "   ",  # no tokens → all-zero normalized
        "",
        "tok\x1ctok",  # FS splits tokens (not in class)
        "x" * 5000,
        "call:symbol.name value,another;third:done",
    ]
    cases = []
    for size in (1024, 8, 1):
        for text in texts:
            cases.append(
                {"text": text, "size": size, "vector": message_scan._hash_vector(text, size)}
            )
    write("phase06_g3_hash_vector.json", {"cases": cases})


# ── G4: stale filters + collection bodies (captured from the reference) ─────
class CaptureStore:
    """Records the calls the reference would make — the fixture IS the call."""

    def __init__(self, exists: bool = True, info: dict | None = None):
        self.exists = exists
        self.info = info or {}
        self.deleted: list = []
        self.created: list = []
        self.indexed: list = []

    def collection_exists(self, name):
        return self.exists

    def get_collection_info(self, name):
        return self.info

    def delete(self, collection, filter_selector=None, wait=None):  # delete_by_filter path
        self.deleted.append({"filter_selector": model_to_dict(filter_selector), "wait": wait})

    def create_collection(self, name, vectors_config=None, **kwargs):
        self.created.append({"vectors_config": model_to_dict(vectors_config), "kwargs": model_to_dict(kwargs)})

    def create_payload_index(self, collection, field, wait=None):
        self.indexed.append({"field": field, "wait": wait})


def g4_stale_and_config() -> None:
    filters = []
    scenarios = [
        {"full_replace": True, "paths": [], "keep": []},
        {"full_replace": False, "paths": ["b.go", "a.go", "b.go", "sub\\c.go", ""], "keep": ["id-1", "id-2"]},
        {"full_replace": False, "paths": ["a.go"], "keep": []},
        {"full_replace": True, "paths": ["zz.go"], "keep": ["k"]},
    ]
    for scenario in scenarios:
        store = CaptureStore()
        # Monkeypatch delete_by_filter to capture the exact normalized filter.
        original = pvs.delete_by_filter

        def capture(_store, _collection, value, _orig=original):
            store.deleted.append({"filter": value})

        pvs.delete_by_filter = capture
        try:
            pvs._delete_stale(
                store,
                url="http://unused",
                collection="c",
                parser=scenario.get("parser", "go"),
                project_id="P rój",
                root_scope="/root",
                keep_ids=scenario["keep"],
                cleanup_paths=scenario["paths"],
                full_replace=scenario["full_replace"],
                timeout=300.0,
                retries=3,
                retry_sleep=2.0,
            )
        finally:
            pvs.delete_by_filter = original
        filters.append({"scenario": scenario, "calls": store.deleted})

    write("phase06_g4_stale_filters.json", {"cases": filters})

    # ensure_collection bodies through the REAL reference (capture kwargs).
    from tools.common import local_qdrant

    configs = []
    store = CaptureStore(exists=False)
    original_ensure = local_qdrant.ensure_collection
    # ensure_collection(store, collection, vector_size) — call pvs._ensure_collection path
    pvs._ensure_collection(store, url="u", collection="c", vector_size=1024, timeout=3.0, retries=1, retry_sleep=1.0)
    configs.append({"case": "create fresh (env defaults)", "calls": store.created})

    drift = CaptureStore(exists=True, info=None)
    drift.get_collection_info = lambda name: _info(1024)
    try:
        pvs._ensure_collection(drift, url="u", collection="c", vector_size=512, timeout=3.0, retries=1, retry_sleep=1.0)
        configs.append({"case": "size drift tolerated? (must raise)", "calls": "no-error"})
    except ValueError as exc:
        configs.append({"case": "size drift", "calls": str(exc)})

    exists_same = CaptureStore(exists=True)
    exists_same.get_collection_info = lambda name: _info(1024)
    pvs._ensure_collection(exists_same, url="u", collection="c", vector_size=1024, timeout=3.0, retries=1, retry_sleep=1.0)
    configs.append({"case": "exists, same size → no create", "calls": exists_same.created})

    idx = CaptureStore(exists=True)
    idx.get_collection_info = lambda name: _info(1024)
    pvs._ensure_project_scope_index(idx, url="u", collection="c", timeout=3.0, retries=1, retry_sleep=1.0)
    configs.append({"case": "scope index order", "calls": idx.indexed})
    write("phase06_g4_collection_bodies.json", {"cases": configs})


def _info(size: int):
    # Minimal stand-in matching what vector_sizes() navigates:
    # info.config.params.vectors(.size). Kept shape-only so it does not depend
    # on the qdrant-client model surface (which moves between versions).
    from types import SimpleNamespace

    return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors={"size": size})))


# ── G5 inputs: full rows → documents construction ───────────────────────────
def g5_documents_e2e() -> None:
    rows = {
        "files": [
            {
                "id": "src/a.go",
                "path": "src/a.go",
                "project_id": "p5",
                "project_name": "p5",
                "language": "go",
                "repo": "p5/a",
                "code": "package main\n",
                "comment": "// file doc",
                "summary": "",
                "start_line": 1,
                "end_line": 2,
            }
        ],
        "namespaces": [
            {"id": "ns:pkg", "project_id": "p5", "qualified_name": "pkg", "kind": "namespace", "name": "pkg"},
        ],
        "types": [
            {"id": "ty:Server", "project_id": "p5", "name": "Server", "code": "type Server struct {}", "file_path": "src/a.go"},
        ],
        "functions": [
            {
                "id": "fn:Serve",
                "project_id": "p5",
                "name": "Serve",
                "qualified_name": "pkg.Server.Serve",
                "summary": "Performs Serve operation (takes 1 parameters)",
                "comment": "// Serve starts",
                "note": "semantic note",
                "code": "func (s *Server) Serve() {}",
                "file_path": "src/a.go",
                "start_line": 4,
                "end_line": 6,
            },
        ],
        "fields": [{"id": "fld:x", "project_id": "p5", "name": "x", "kind": ""}],
        "aliases": [{"id": "al:Alias", "project_id": "p5", "name": "Alias", "code": "type Alias = int"}],
        "templates": [],
        "relations": [{"id": "REL", "project_id": "p5"}],  # must be skipped
        "calls": [{"id": "CALL", "project_id": "p5"}],  # must be skipped
    }
    cases = []
    try:
        docs = pvs.documents_from_rows(rows, parser="go", root_scope="p5/a", max_chars=4000)
        cases.append(
            {
                # JSON objects lose insertion order on the Rust side; the
                # artifact contract carries ordered pairs, so the fixture does
                # too (documents_from_rows consumed the dict above).
                "input_categories": [[k, v] for k, v in rows.items()],
                "parser": "go",
                "root_scope": "p5/a",
                "max_chars": 4000,
                "expect_error": False,
                "documents": [
                    {"id": d.id, "text": d.text, "payload": d.payload} for d in docs
                ],
            }
        )
    except ValueError as exc:
        cases.append({"input_categories": [[k, v] for k, v in rows.items()], "parser": "go", "root_scope": "p5/a", "max_chars": 4000, "expect_error": True, "error": str(exc)})

    # Scope-mismatch payload guard (sync_vector_documents raises before embed).
    docs = pvs.documents_from_rows(
        {"functions": [{"id": "f", "project_id": "other"}]}, parser="go", root_scope="r", max_chars=100
    )
    mismatches = [
        d.payload.get("project_id") != "p5" or d.payload.get("parser") != "go" or d.payload.get("root_scope") != "r"
        for d in docs
    ]
    write(
        "phase06_g5_documents.json",
        {"cases": cases, "scope_mismatch_true": all(mismatches)},
    )


def main() -> int:
    g1_point_ids()
    g2_redaction()
    g3_hash_vector()
    g4_stale_and_config()
    g5_documents_e2e()
    # Digest of the fixture set for the report.
    blob = hashlib.sha256()
    for path in sorted(OUT.glob("phase06_*.json")):
        blob.update(path.read_bytes())
    print(f"[fixtures] phase06 digest sha256={blob.hexdigest()[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
