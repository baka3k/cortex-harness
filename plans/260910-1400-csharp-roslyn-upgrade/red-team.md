# Red Team Review — C# Roslyn Primary Analyzer Upgrade

## Review Date: 2026-09-10
## Reviewer: Plan Author (self-review)

---

### Challenge 1: Roslyn Worker Duplication vs ASP.NET Worker

**Challenge**: Tại sao tạo CSharpRoslynWorker mới khi đã có AspNetRoslynWorker trong common/aspnet? Có risk code duplication không?

**Analysis**:
- AspNetRoslynWorker được thiết kế cho framework overlay: emit evidence specifically for ASP.NET pattern matching (controllers, actions, middleware)
- CSharpRoslynWorker cần emit evidence cho primary analysis: full member inventory, all member types, project metadata, NuGet deps
- Protocol contracts khác nhau: ASP.NET worker emit `evidence.types[]` với kind detection (Controller, Action, etc.), C# worker emit raw member data
- Tuy nhiên, có overlap lớn ở tầng compilation: workspace loading, syntax tree parsing, semantic model access

**Risk**: Code duplication ở CompilationService, protocol handling, build infrastructure.

**Mitigation**: 
- Consider extracting shared Roslyn compilation core vào `tools/common/roslyn_core/` (shared between C# worker and ASP.NET worker)
- OR: Extend AspNetRoslynWorker với additional extraction capabilities, controlled by request flags
- Decision: Keep separate workers for Phase 01 (speed), add shared core as Phase 07 follow-up if duplication becomes painful

**Verdict**: ACCEPTED with follow-up. Separate workers is faster to implement and less risky. Shared core optimization can come later.

---

### Challenge 2: Framework Item False Positives

**Challenge**: 11 framework item kinds với pattern matching → high risk of false positives. Ví dụ: `services.AddScoped<Foo>()` có thể là DI registration hoặc chỉ là method call trên object trùng tên.

**Analysis**:
- Roslyn semantic model giúp giảm false positives: resolve `services` type to `IServiceCollection`
- Nhưng syntax mode không có semantic resolution → chỉ dựa trên name patterns
- Some patterns rất generic: `ILogger<T>` injection vs bất kỳ constructor parameter nào

**Risk**: 
- False positives pollute graph with incorrect framework facts
- False negatives miss real patterns (less critical)

**Mitigation**:
- Confidence scoring: each framework item has confidence level (high/medium/low)
- Semantic mode required for high-confidence extraction
- Syntax mode only emits items with very specific patterns (e.g., attribute-based)
- Framework item nodes include `extraction_method` property (roslyn_semantic vs roslyn_syntax)
- Consumer queries can filter by confidence

**Verdict**: ACCEPTED. Confidence scoring + extraction_method metadata is sufficient. Document false positive expectations.

---

### Challenge 3: Performance Regression

**Challenge**: Roslyn workspace mode significantly slower than Tree-sitter. Risk of 5-10x slowdown on large solutions.

**Analysis**:
- Tree-sitter: ~1ms per file (syntax only)
- Roslyn syntax mode: ~10-50ms per file (no compilation)
- Roslyn workspace mode: ~100-500ms per file (full compilation + semantic model)
- Large solutions (1000+ files) could take minutes vs seconds

**Risk**:
- CI/CD pipeline timeout
- Developer experience degradation
- Memory pressure from Roslyn compilation

**Mitigation**:
- Configurable timeout (default 600s)
- Workspace mode optional (`--semantic-mode off` for syntax-only)
- Incremental mode already limits files processed
- Cache Roslyn payloads aggressively
- Consider parallel file processing within worker
- Benchmark gate: < 2x Tree-sitter on representative corpus (may need adjustment)

**Verdict**: PARTIALLY ACCEPTED. 2x target may be unrealistic for workspace mode. Adjust to:
- Syntax mode: < 3x Tree-sitter
- Workspace mode: < 10x Tree-sitter (or < 5min for 1000 files)
- Add `--semantic-mode` guidance in documentation

---

### Challenge 4: Parse Cache Invalidation Complexity

**Challenge**: Two parser backends (Roslyn + Tree-sitter) with different output schemas. Cache invalidation must handle backend switches.

**Analysis**:
- Current cache key: `file_signature|schema:version`
- If user switches from Roslyn to Tree-sitter (or vice versa), cached payloads are incompatible
- Cache version bump forces re-parse but doesn't distinguish backends

**Risk**:
- Stale cache serves wrong backend's payload
- Cache bloat from storing both backends' payloads

**Mitigation**:
- Include backend in cache key: `file_signature|schema:version|backend:roslyn_workspace`
- Separate cache directories per backend
- Worker version in cache key (worker rebuild → cache invalidation)

**Verdict**: ACCEPTED. Backend-aware cache keys are straightforward to implement.

---

### Challenge 5: ASP.NET Overlay Conflict

**Challenge**: ASP.NET overlays (aspnet_core, aspnet_framework) already consume Roslyn evidence. Could new C# Roslyn worker conflict?

**Analysis**:
- ASP.NET overlays use AspNetRoslynWorker via common/aspnet/roslyn_adapter.py
- C# primary uses CSharpRoslynWorker via csharp/roslyn_adapter.py
- Both workers analyze .cs files but emit different evidence
- ASP.NET overlays run AFTER primary, link via SEMANTIC_OF
- No ownership conflict: primary owns canonical C# nodes, overlays own framework nodes

**Risk**:
- Double Roslyn compilation (primary + overlay) → wasted work
- Conflicting evidence for same types

**Mitigation**:
- Double compilation is acceptable for Phase 01 (separate workers)
- Long-term: share compilation results between primary and overlays
- Evidence conflict: overlays use SEMANTIC_OF to link, never replace primary nodes

**Verdict**: ACCEPTED. Double compilation is inefficiency, not incorrectness. Optimize later.

---

### Challenge 6: .NET SDK Requirement

**Challenge**: Roslyn worker requires .NET SDK on host. Not all environments have it.

**Analysis**:
- Tree-sitter fallback handles this gracefully
- But: users expect Roslyn features, may not realize they're in fallback mode
- CI/CD environments may not have .NET SDK installed

**Risk**:
- Silent degradation to Tree-sitter (lower quality)
- Users unaware they're missing semantic features

**Mitigation**:
- Clear warning when fallback activated
- `dev doctor` command checks for .NET SDK
- Documentation: prerequisites section
- Parse provenance metadata shows which backend used

**Verdict**: ACCEPTED. Warning + provenance + doctor check is sufficient.

---

## Summary

| Challenge | Verdict | Action Required |
| --- | --- | --- |
| Worker duplication | Accepted | Follow-up: shared core if duplication painful |
| False positives | Accepted | Confidence scoring + extraction_method metadata |
| Performance | Partially accepted | Adjust targets: syntax <3x, workspace <10x |
| Cache invalidation | Accepted | Backend-aware cache keys |
| ASP.NET conflict | Accepted | Double compilation OK for now |
| .NET SDK requirement | Accepted | Warning + doctor check |

## Recommendations

1. Adjust performance targets to be realistic
2. Add confidence scoring to framework items
3. Include backend in cache keys
4. Add .NET SDK check to `dev doctor`
5. Document fallback behavior clearly
6. Consider shared Roslyn core as future optimization
