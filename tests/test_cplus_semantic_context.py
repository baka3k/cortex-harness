"""Phase 03 compile-context registry, dependency invalidation, semantic
cache identity, and bounded scheduler contract tests."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.parse_recovery import CompileContext, sanitize_compile_arguments  # noqa: E402
from tools.cplus.semantic_context import (  # noqa: E402
    BASELINE_REPORT_VERSION,
    CONTEXT_REGISTRY_VERSION,
    CoverageState,
    CircuitOpenError,
    OverloadedError,
    RejectionReason,
    RegisteredContext,
    BoundedSemanticScheduler,
    ContextRegistry,
    ReverseInvalidationIndex,
    SemanticCache,
    SemanticCacheIdentity,
    SemanticLaneLimits,
    SemanticTask,
    build_baseline_report,
    build_cache_identity,
    build_registered_context,
    lexical_include_closure,
    parse_dependency_manifest,
)
from tools.cplus.semantic_worker import (  # noqa: E402
    SEMANTIC_WORKER_PROTOCOL_VERSION,
)


def _make_context(rel_path: str, args, root: str, tmp: str) -> RegisteredContext:
    safe = sanitize_compile_arguments(
        ["clang++"] + args + [rel_path],
        root=root,
        directory=root,
        source_path=str(Path(root) / rel_path),
    )
    encoded = json.dumps(safe, separators=(",", ":"))
    import hashlib

    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    compile_context = CompileContext(
        file_path=rel_path,
        arguments=safe,
        fingerprint=fingerprint,
    )
    Path(root, rel_path).touch()
    return build_registered_context(
        compile_context,
        project="proj",
        root=root,
        working_dir=root,
        coverage=CoverageState.FAITHFUL,
    )


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.registry = ContextRegistry()

    def test_faithful_context_registers_eligible(self):
        context = _make_context("a.cpp", ["-DFOO=1"], self.root, self._tmp.name)
        registered = self.registry.register(context)
        self.assertTrue(registered.eligible)
        self.assertEqual(registered.coverage, CoverageState.FAITHFUL)
        self.assertEqual(self.registry.variants("proj", "a.cpp"), [registered])

    def test_synthetic_context_fails_closed_for_publication(self):
        context = _make_context("a.cpp", ["-DFOO=1"], self.root, self._tmp.name)
        synthetic = RegisteredContext(
            **{**context.to_json(), "coverage": CoverageState.SYNTHETIC}
        )
        registered = self.registry.register(synthetic)
        self.assertFalse(registered.eligible)
        self.assertEqual(
            registered.rejection_reason, RejectionReason.SYNTHETIC_CONTEXT
        )
        summary = self.registry.rejection_summary()
        self.assertEqual(summary.get("synthetic_context"), 1)

    def test_multiple_configurations_preserved_without_implicit_winner(self):
        first = _make_context("a.cpp", ["-DCFG=1"], self.root, self._tmp.name)
        second = _make_context("a.cpp", ["-DCFG=2"], self.root, self._tmp.name)
        self.registry.register(first)
        self.registry.register(second)
        variants = self.registry.variants("proj", "a.cpp")
        self.assertEqual(len(variants), 2)
        self.assertNotEqual(
            variants[0].config_fingerprint, variants[1].config_fingerprint
        )

    def test_variant_cap_bounds_selection(self):
        for index in range(5):
            self.registry.register(
                _make_context("a.cpp", [f"-DCFG={index}"], self.root, self._tmp.name)
            )
        selected = self.registry.select("proj", "a.cpp", variant_cap=3)
        self.assertEqual(len(selected), 3)

    def test_explicit_profiles_select_declared_variants_only(self):
        contexts = [
            self.registry.register(
                _make_context("a.cpp", [f"-DCFG={index}"], self.root, self._tmp.name)
            )
            for index in range(3)
        ]
        wanted = [contexts[1].config_fingerprint]
        selected = self.registry.select("proj", "a.cpp", explicit_profiles=wanted)
        self.assertEqual(
            [c.config_fingerprint for c in selected], wanted
        )

    def test_registry_roundtrip_and_version_gate(self):
        context = _make_context("a.cpp", ["-DFOO=1"], self.root, self._tmp.name)
        self.registry.register(context)
        path = str(Path(self.root, "registry.json"))
        self.registry.save(path)
        loaded = ContextRegistry.load(path)
        self.assertEqual(loaded.variants("proj", "a.cpp")[0], context)
        with open(path) as handle:
            data = json.load(handle)
        data["version"] = "bogus"
        bogus = str(Path(self.root, "registry_bad.json"))
        with open(bogus, "w") as handle:
            json.dump(data, handle)
        with self.assertRaises(ValueError):
            ContextRegistry.load(bogus)
        self.assertEqual(
            json.load(open(path))["version"], CONTEXT_REGISTRY_VERSION
        )

    def test_context_identity_covers_target_sysroot(self):
        base = _make_context("a.cpp", ["-DFOO=1"], self.root, self._tmp.name)
        targeted = RegisteredContext(
            **{
                **base.to_json(),
                "config_fingerprint": "cfg-targeted",
                "target_identity": "sha-target",
                "sysroot_identity": "sha-sysroot",
            }
        )
        self.registry.register(base)
        self.registry.register(targeted)
        variants = self.registry.variants("proj", "a.cpp")
        self.assertEqual(len(variants), 2)


class DependencyManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_parse_makefile_dep_manifest_bounded(self):
        deps_file = Path(self._tmp.name, "tu.d")
        deps_file.write_text(
            "build/tu.o: src/tu.cpp \\\n  inc/a.h inc/b.h\n"
            + "\n".join(f"inc/extra{i}.h" for i in range(30))
        )
        deps = parse_dependency_manifest(str(deps_file), max_entries=10)
        self.assertEqual(len(deps), 10)
        self.assertIn("src/tu.cpp", deps)
        self.assertIn("inc/a.h", deps)

    def test_reverse_invalidation_index_fanout(self):
        index = ReverseInvalidationIndex()
        index.add("tu-a", ["inc/shared.h", "src/a.cpp"])
        index.add("tu-b", ["inc/shared.h"])
        self.assertEqual(index.fanout("inc/shared.h"), 2)
        self.assertEqual(index.impacted(["inc/shared.h"]), {"tu-a", "tu-b"})
        self.assertEqual(index.impacted(["src/a.cpp"]), {"tu-a"})
        self.assertEqual(index.impacted(["unrelated.cpp"]), set())
        roundtrip = ReverseInvalidationIndex.from_json(index.to_json())
        self.assertEqual(roundtrip.impacted(["inc/shared.h"]), {"tu-a", "tu-b"})

    def test_lexical_include_closure_fallback(self):
        base = Path(self._tmp.name)
        (base / "src").mkdir()
        (base / "src" / "tu.cpp").write_text('#include "a.h"\nint main(){}\n')
        (base / "src" / "a.h").write_text('#include "b.h"\n')
        (base / "src" / "b.h").write_text("// leaf\n")
        closure = lexical_include_closure(str(base / "src" / "tu.cpp"), root=str(base))
        names = {Path(p).name for p in closure}
        self.assertEqual(names, {"a.h", "b.h"})


class SemanticCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = SemanticCache(str(Path(self._tmp.name, "cache")))
        self.context = RegisteredContext(
            project="proj",
            rel_path="a.cpp",
            config_fingerprint="cfg-1",
            arguments_digest="args-1",
            working_dir_rel=".",
            target_identity="",
            sysroot_identity="",
            resource_dir_identity="",
            toolchain_version="tc-1",
            coverage=CoverageState.FAITHFUL,
            rejection_reason=RejectionReason.NOT_REJECTED,
            source_fingerprint="src-1",
        )

    def _identity(self, **overrides):
        base = dict(
            source_rel_path="a.cpp",
            source_fingerprint="src-1",
            dependency_fingerprints=("dep-1",),
            config_fingerprint="cfg-1",
            coverage=CoverageState.FAITHFUL,
            proc_source_map_version="",
        )
        base.update(overrides)
        return SemanticCacheIdentity(**base)

    def test_unchanged_identity_hits_cache(self):
        identity = build_cache_identity(self.context, dependency_fingerprints=["dep-1"])
        self.cache.store(identity, {"callsites": []})
        self.assertEqual(self.cache.load(identity), {"callsites": []})

    def test_every_changed_semantic_input_invalidates(self):
        identity = build_cache_identity(self.context, dependency_fingerprints=["dep-1"])
        self.cache.store(identity, {"callsites": []})
        changed_inputs = [
            self._identity(source_fingerprint="src-2"),
            self._identity(dependency_fingerprints=("dep-2",)),
            self._identity(config_fingerprint="cfg-2"),
            self._identity(coverage=CoverageState.SYNTHETIC),
            self._identity(proc_source_map_version="map-v2"),
        ]
        for changed in changed_inputs:
            self.assertIsNone(self.cache.load(changed), changed)

    def test_worker_version_invalidation(self):
        from unittest import mock

        import tools.cplus.semantic_context as semantic_context

        identity = build_cache_identity(self.context, dependency_fingerprints=[])
        baseline = identity.fingerprint()
        with mock.patch.object(
            semantic_context, "SEMANTIC_WORKER_PROTOCOL_VERSION", "3"
        ):
            bumped = identity.fingerprint()
        self.assertNotEqual(baseline, bumped)
        self.assertNotEqual(baseline, "")


class SchedulerTests(unittest.TestCase):
    def _task(self, name: str, size: int = 10) -> SemanticTask:
        context = RegisteredContext(
            project="proj",
            rel_path=f"{name}.cpp",
            config_fingerprint=f"cfg-{name}",
            arguments_digest="a",
            working_dir_rel=".",
            target_identity="",
            sysroot_identity="",
            resource_dir_identity="",
            toolchain_version="tc",
            coverage=CoverageState.FAITHFUL,
            rejection_reason=RejectionReason.NOT_REJECTED,
        )
        return SemanticTask(identity=name, payload_bytes=size, context=context)

    def test_backpressure_rejects_over_queue_budget(self):
        scheduler = BoundedSemanticScheduler(
            SemanticLaneLimits(max_queue_items=2, max_queue_bytes=100)
        )
        scheduler.submit(self._task("a"))
        scheduler.submit(self._task("b"))
        with self.assertRaises(OverloadedError):
            scheduler.submit(self._task("c"))
        status = scheduler.status()
        self.assertEqual(status["rejected_overloaded"], 1)
        self.assertEqual(status["queued_items"], 2)

    def test_drain_yields_and_reports_latency(self):
        scheduler = BoundedSemanticScheduler()
        scheduler.submit(self._task("a"))
        scheduler.submit(self._task("b"))
        metrics = scheduler.drain(lambda task: True)
        self.assertEqual(metrics.completed, 2)
        status = scheduler.status()
        self.assertEqual(status["queued_items"], 0)
        self.assertGreaterEqual(status["latency_p50_seconds"], 0.0)

    def test_cancellation_checkpoint_stops_task(self):
        scheduler = BoundedSemanticScheduler()
        scheduler.submit(self._task("a"))
        scheduler.cancel("a")
        seen = []

        def handler(task):
            seen.append(task.identity)
            return scheduler.cancellation_checkpoint(task.identity) or True

        metrics = scheduler.drain(handler)
        self.assertEqual(seen, [])
        self.assertEqual(metrics.cancelled, 1)

    def test_circuit_breaker_trips_on_non_yield_streak(self):
        scheduler = BoundedSemanticScheduler()
        for index in range(BoundedSemanticScheduler.MAX_NON_YIELD_STREAK):
            scheduler.submit(self._task(f"n{index}"))
        scheduler.drain(lambda task: False)
        self.assertTrue(scheduler.status()["circuit_open"])
        with self.assertRaises(CircuitOpenError):
            scheduler.submit(self._task("after"))


class BaselineReportTests(unittest.TestCase):
    def test_report_quantifies_coverage_variants_and_fanout(self):
        registry = ContextRegistry()
        index = ReverseInvalidationIndex()
        index.add("tu-a", ["inc/shared.h"])
        index.add("tu-b", ["inc/shared.h"])
        with tempfile.TemporaryDirectory() as tmp:
            for index_num in range(2):
                registry.register(
                    _make_context("a.cpp", [f"-DCFG={index_num}"], tmp, tmp)
                )
        report = build_baseline_report(
            registry,
            index,
            scheduler_status={"queued_items": 0},
            cache_hits=8,
            cache_misses=2,
            changed_tu_latencies=[0.1, 0.2, 0.3, 0.4, 1.0],
        )
        self.assertEqual(report["version"], BASELINE_REPORT_VERSION)
        self.assertEqual(report["tu_count"], 1)
        self.assertEqual(report["duplicate_variant_tus"], 1)
        self.assertEqual(report["top_fanout"][0]["consumer_count"], 2)
        self.assertEqual(report["cache_hit_rate"], 0.8)
        self.assertEqual(report["changed_tu_latency_p50_seconds"], 0.3)


if __name__ == "__main__":
    unittest.main()
