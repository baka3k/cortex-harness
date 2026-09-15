#!/usr/bin/env bash
# audit_python_disposition.sh — phase-01 tooling của plans/260915-2230-python-legacy-cleanup
#
# Phân loại mọi .py trong repo (loại trừ junk dirs) thành LIVE-RUNTIME / PARITY / DEAD,
# dựa trên import closure (AST) từ bộ seed + danh sách path-spawn keep.
#
# Usage:
#   scripts/audit_python_disposition.sh                 # in bảng đếm + liệt kê per nhóm ra stdout
#   scripts/audit_python_disposition.sh --json OUT      # ghi 3 list ra OUT.{live,parity,dead}.txt
#   scripts/audit_python_disposition.sh check-delete A [B ...]   # exit 1 nếu file/dir nằm trong LIVE hoặc PARITY
#   scripts/audit_python_disposition.sh verify-live     # exit 1 nếu thiếu file LIVE nào (chạy sau mỗi phase xoá)
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-report}"

run_closure() { python3 -W ignore - <<'PYEOF'
import ast, os, sys, json
from collections import deque

ROOT = os.getcwd()
PKG_ROOTS = [
    ("tools", "code-tiny"), ("mcp", "code-tiny"), ("testtool", "code-tiny"),
    ("livingdoc", "code-tiny"), ("skills", "code-tiny"),
    ("doc", "doc-tiny"), ("extractor", "doc-tiny"), ("gliner", "doc-tiny"),
    ("embedding_utils", "doc-tiny"), ("entity_extractors", "doc-tiny"),
    ("enviroment_loader", "doc-tiny"), ("graph_store", "doc-tiny"),
    ("graphrag_ingest_langextract", "doc-tiny"), ("graphrag_query_langextract", "doc-tiny"),
    ("mcp_graph_rag", "doc-tiny"), ("neo4j_loader", "doc-tiny"),
    ("doc_local_qdrant", "doc-tiny"), ("project_contract", "doc-tiny"),
    ("model", "doc-tiny"), ("open_ai_exec", "doc-tiny"),
    ("cortex_harness", "."), ("scripts", "."), ("installers", "."), ("tests", "."), ("harness", "."),
]
EXCLUDE_DIRS = {".git","__pycache__",".venv","node_modules","target","archived",
                "fixtures",".qwen",".cache","plans","http"}

def repo_files():
    out = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                out[os.path.relpath(os.path.join(dirpath, f), ROOT)] = True
    return out

FILES = repo_files()

def resolve(modname):
    if not modname: return []
    cands = []
    for prefix, rootdir in PKG_ROOTS:
        if modname == prefix or modname.startswith(prefix + "."):
            rel = modname.replace(".", "/")
            cands += [os.path.join(ROOT, rootdir, rel + ".py"),
                      os.path.join(ROOT, rootdir, rel, "__init__.py")]
    cands.append(os.path.join(ROOT, modname.replace(".", "/") + ".py"))
    return [os.path.relpath(c, ROOT) for c in set(cands) if os.path.relpath(c, ROOT) in FILES]

def imports_of(relpath):
    try:
        tree = ast.parse(open(os.path.join(ROOT, relpath), encoding="utf-8", errors="replace").read())
    except Exception:
        return []
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = os.path.dirname(relpath)
                for _ in range(node.level - 1): base = os.path.dirname(base)
                found.add((base.replace("/", ".") + "." if base else "") + (node.module or ""))
            elif node.module:
                found.add(node.module)
    return sorted(found)

def closure(seeds):
    seen, q = set(), deque(seeds)
    while q:
        cur = q.popleft()
        if cur in seen: continue
        seen.add(cur)
        for mod in imports_of(cur):
            for f in resolve(mod):
                if f not in seen: q.append(f)
    return seen

RUNTIME_SEEDS = [
    "harness/scripts/orchestrator.py", "harness/scripts/context_selector.py",
    "scripts/rust_mcp/embed_worker.py",
    "scripts/rust_mcp/vector_worker.py",
    "code-tiny/tools/sync/incremental_sync.py",
    "code-tiny/tools/graph/journal/consumer.py",
    "doc-tiny/graphrag_ingest_langextract.py",
    # reviewer fix #1: cortex-dev lifecycle.rs:93-95 spawns this for infra-up/infra-down/doctor
    "scripts/mcp-lifecycle.py",
    # reviewer fix #2: cortex-storage remote_probe.rs:439-446 path-spawn (dev infra-up --provision)
    "code-tiny/scripts/setup_constraints.py",
]
# reviewer fix #3: entry-point files CI/pytest chạy trực tiếp (lifecycle-macos.yml:80-110)
ENTRY_SEEDS = [
    "conftest.py",
    "tests/test_make_lifecycle.py", "tests/test_dev_lifecycle_commands.py",
    "tests/test_rust_bridge_ban.py", "tests/test_embedded_discovery_parity.py",
    "tests/test_ladybug_provider_plumbing.py", "tests/test_parity_ladybug.py",
    "code-tiny/tests/test_ladybug_driver_local.py",
]
# reviewer fix #6: gliner_sidecar.py chỉ là parity tool (compare_mind.py:293), không phải runtime spawn
PARITY_SEEDS = sorted(f for f in FILES if f.startswith("scripts/rust_parity/"))
PARITY_SEEDS += sorted(f for f in FILES
                       if f.startswith("scripts/rust_mcp/") and f not in RUNTIME_SEEDS
                       and f != "scripts/rust_mcp/gliner_sidecar.py")
PARITY_SEEDS += ["scripts/rust_mcp/gliner_sidecar.py",
                 "cortex_harness/dev.py", "cortex_harness/mcp_contract.py"]  # parity-reference + contract

live = closure(RUNTIME_SEEDS)
parity = closure(PARITY_SEEDS) - live
entry = set(ENTRY_SEEDS) & set(FILES)
dead = set(FILES) - live - parity - entry
print(json.dumps({"live": sorted(live), "parity": sorted(parity),
                  "entry": sorted(entry), "dead": sorted(dead)}))
PYEOF
}

CLOSURE_JSON=$(run_closure)

# Path-spawn keeps: file .py được spawn THEO ĐƯỜNG DẪN từ Rust (không qua import).
# Nguồn: rust/crates — xem disposition.md mục A2/A5.
PATH_SPAWN_KEEP=$(cat <<'EOF'
code-tiny/tools/cplus/clang_worker.py
code-tiny/tools/cplus/parse_recovery.py
code-tiny/tools/cplus/semantic_worker.py
code-tiny/tools/cplus/semantic_context.py
code-tiny/tools/cplus/semantic_shadow.py
code-tiny/tools/cplus/clang_parser.py
code-tiny/mcp/unified_mcp.py
doc-tiny/mcp_graph_rag.py
# reviewer fix #5: caller duy nhất là `dev installer install` (dev.py:4800-4803 sys.path hack,
# unreachable post-cutover) — bảo tồn đến khi needs-decision chốt (phase-04)
installers/common/config_manager.py
installers/common/__init__.py
installers/windows/registry_manager.py
installers/windows/__init__.py
EOF
)

case "$MODE" in
  --json)
    OUT="${2:?--json OUT}"
    echo "$CLOSURE_JSON" | PATH_SPAWN_KEEP="$PATH_SPAWN_KEEP" python3 -c "
import json,sys,os
d=json.load(sys.stdin)
# reviewer fix #9: path-spawn keep thuộc nhóm protected, gộp vào live cho mọi consumer
for p in [l.strip() for l in os.environ['PATH_SPAWN_KEEP'].splitlines() if l.strip() and not l.strip().startswith('#')]:
    p=p.strip()
    if p in d['dead']: d['dead'].remove(p)
    if p not in d['live']: d['live'].append(p)
d['live'].sort()
for k in ('live','parity','entry','dead'):
    open('${OUT}.'+k+'.txt','w').write('\n'.join(d[k])+('\n' if d[k] else ''))
print('live=%d parity=%d entry=%d dead=%d' % (len(d['live']),len(d['parity']),len(d['entry']),len(d['dead'])))
"
    ;;
  verify-live)
    echo "$CLOSURE_JSON" | PATH_SPAWN_KEEP="$PATH_SPAWN_KEEP" python3 -c "
import json,sys,os
d=json.load(sys.stdin)
protected=set(d['live'])|set(d['parity'])|set(d['entry'])
for p in [l.strip() for l in os.environ['PATH_SPAWN_KEEP'].splitlines() if l.strip() and not l.strip().startswith('#')]: protected.add(p.strip())
missing=[f for f in protected if not os.path.exists(f)]
for f in missing: print('MISSING PROTECTED:', f)
sys.exit(1 if missing else 0)
"
    ;;
  check-delete)
    shift
    echo "$CLOSURE_JSON" | PATH_SPAWN_KEEP="$PATH_SPAWN_KEEP" python3 -c "
import json,sys,os
d=json.load(sys.stdin)
protected=set(d['live'])|set(d['parity'])|set(d['entry'])
for p in [l.strip() for l in os.environ['PATH_SPAWN_KEEP'].splitlines() if l.strip() and not l.strip().startswith('#')]: protected.add(p.strip())
bad=[]
for arg in sys.argv[1:]:
    # reviewer fix #4: chuẩn hoá về repo-relative, fail-closed với path ngoài repo
    rel=os.path.relpath(os.path.abspath(arg), os.getcwd())
    if rel.startswith('..'):
        print('OUT-OF-REPO ARG (fail-closed):', arg); sys.exit(2)
    key=rel.rstrip('/')
    if os.path.isdir(key):
        hits=[f for f in protected if f.startswith(key+'/') and os.path.exists(f)]
    else:
        hits=[key] if key in protected else []
    bad+=hits
for f in bad: print('PROTECTED (LIVE/PARITY/ENTRY/PATH-SPAWN):', f)
sys.exit(1 if bad else 0)
" "$@"
    ;;
  *)
    echo "$CLOSURE_JSON" | python3 -c "
import json,sys
from collections import Counter
d=json.load(sys.stdin)
def blk(f):
    parts=f.split('/')
    return '/'.join(parts[:2]) if parts[0] in ('code-tiny','doc-tiny','cortex_harness','harness','scripts','tests','installers') else parts[0]
for k in ('live','parity','entry','dead'):
    print('== %s (%d files) ==' % (k.upper(), len(d[k])))
    for g,c in Counter(blk(f) for f in d[k]).most_common(): print('  %-28s %d' % (g,c))
"
    ;;
esac
