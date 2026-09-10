"""End-to-end integration tests for the C# Roslyn primary analyzer upgrade.

Tests are organized by phase:
- Phase 01: Worker builds and emits versioned deterministic evidence.
- Phase 02: Python adapter — manifest protocol, fallback semantics.
- Phase 03: Full member inventory (properties, fields, events, delegates,
  generics, attributes, XML docs, accessibility).
- Phase 04: Semantic framework items (EF, gRPC, SignalR, BackgroundService,
  Auth, Logging, NuGet).
- Phase 05: Framework registry advertises new labels/relationships.
- Phase 06: Performance, regression, and provenance smoke tests.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

CODE_TINY = Path(__file__).resolve().parents[1] / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.csharp.roslyn_adapter import (
    CSHARP_ROSLYN_PROTOCOL_VERSION,
    analyze_csharp_files,
    is_roslyn_available,
    parse_provenance,
)
from tools.csharp.models import (
    CSHARP_NODE_LABELS,
    CSHARP_RELATIONSHIP_TYPES,
    project_metadata_to_payload,
    roslyn_evidence_to_payload,
    stats_from_conversion,
)
from tools.csharp.framework_items import extract_framework_items


DOTNET_AVAILABLE = shutil.which("dotnet") is not None


SAMPLE_FULL = '''using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Sample.FullInventory
{
    /// <summary>Account entity used by EF</summary>
    public class Account
    {
        [Key]
        public int Id { get; set; }

        [Column("account_name")]
        public string Name { get; set; } = "";

        public const int MaxAccounts = 100;
        public static readonly Account Default = new Account(0);
        private int _balance;

        public delegate void ChangedEventHandler(string reason);
        public event ChangedEventHandler? Changed;

        public Account() { }

        public Account(int initial) { _balance = initial; }

        public virtual async Task DepositAsync(decimal amount, bool verbose = false)
        {
            await Task.Delay(1);
            _balance += (int)amount;
            Changed?.Invoke("deposit");
        }

        public override string ToString() => "Account#" + Id;
    }

    public struct Money
    {
        public decimal Amount { get; set; }
        public string Currency { get; set; }
    }

    public interface IAccountRepo<T> where T : Account, new()
    {
        Task<T?> FindAsync(int id);
    }

    public record AccountDto(int Id, string Name);

    public enum AccountStatus { Active, Suspended, Closed }

    public class AppDbContext : DbContext
    {
        public DbSet<Account> Accounts => Set<Account>();
    }

    [Authorize(Policy = "AdminOnly")]
    public class AdminController
    {
        [AllowAnonymous]
        public void PublicAction() { }
    }

    public class HeartbeatService : BackgroundService
    {
        private readonly ILogger<HeartbeatService> _logger;
        private static readonly ActivitySource Activity = new ActivitySource("Sample.Heartbeat");

        protected override Task ExecuteAsync(CancellationToken stoppingToken)
        {
            _logger.LogInformation("started");
            return Task.CompletedTask;
        }
    }

    public class ChatHub : Hub { }

    public class GreeterService : GrpcServiceBase<GreeterService> { }
}
'''


def _write_sample(tmpdir: Path, body: str = SAMPLE_FULL, name: str = "sample.cs") -> str:
    target = Path(tmpdir, name)
    target.write_text(body, encoding="utf-8")
    return str(target)


@unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
class WorkerBuildAndProtocolTest(unittest.TestCase):
    def test_roslyn_available_probes_worker(self) -> None:
        self.assertTrue(is_roslyn_available())

    def test_worker_returns_known_protocol_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file_path = _write_sample(Path(temporary))
            payload = analyze_csharp_files(
                root=temporary, files=[file_path], verbose=False,
            )
            self.assertEqual(
                payload.get("protocol_version"), CSHARP_ROSLYN_PROTOCOL_VERSION,
            )

    def test_worker_workspace_kind_is_safe_compilation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file_path = _write_sample(Path(temporary))
            payload = analyze_csharp_files(
                root=temporary, files=[file_path], verbose=False,
            )
            self.assertEqual(payload.get("workspace_kind"), "safe_compilation")
            self.assertTrue(payload.get("semantic_enabled"))

    def test_worker_returns_versioned_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            file_path = _write_sample(Path(temporary))
            payload = analyze_csharp_files(
                root=temporary, files=[file_path], verbose=False,
            )
            results = payload["results"]
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["ok"])
            ev = results[0]["evidence"]
            self.assertIn("types", ev)
            self.assertIn("members", ev)
            self.assertIn("fields", ev)
            self.assertIn("events", ev)
            self.assertIn("delegates", ev)
            self.assertIn("parameters", ev)


class AdapterContractTest(unittest.TestCase):
    def test_default_worker_project_resolves(self) -> None:
        from tools.csharp.roslyn_adapter import default_worker_project
        project = default_worker_project()
        self.assertTrue(project.endswith("CSharpRoslynWorker.csproj"))
        self.assertTrue(os.path.isfile(project))

    def test_parse_provenance_marks_roslyn_backend(self) -> None:
        provenance = parse_provenance({
            "coverage_status": "full",
            "workspace_kind": "safe_compilation",
            "semantic_enabled": True,
        })
        self.assertEqual(provenance["parser_language"], "csharp_roslyn_workspace")
        self.assertTrue(provenance["parser_available"])
        self.assertEqual(provenance["roslyn_workspace_kind"], "safe_compilation")

    def test_parse_provenance_marks_syntax_only_when_semantic_off(self) -> None:
        provenance = parse_provenance({
            "coverage_status": "syntax_only",
            "workspace_kind": "none",
            "semantic_enabled": False,
        })
        self.assertEqual(provenance["parser_language"], "csharp_roslyn_syntax")


@unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
class FallbackTest(unittest.TestCase):
    def test_force_failure_returns_none_cache(self) -> None:
        from tools.csharp.roslyn_integration import RoslynFirstRunner, RoslynRunnerOptions
        opts = RoslynRunnerOptions(
            enabled=True,
            worker_project_path="/nonexistent/path/CSharpRoslynWorker.csproj",
        )
        runner = RoslynFirstRunner(
            root=tempfile.gettempdir(),
            options=opts,
            tree_sitter_fallback=lambda *a: ([],) * 7,
        )
        cache = runner.try_load(["/tmp/sample.cs"])
        # Either the build failed (cache=None and last_error set), or the build
        # produced an empty cache (no files matched). Either way, the runner
        # must NOT silently report success.
        if cache is not None:
            self.assertEqual(cache.success_by_relpath, {})
            self.assertEqual(cache.provenance.get("coverage_status"), "empty")
        else:
            self.assertIsNotNone(runner.last_error)

    def test_disable_roslyn_skips_runner(self) -> None:
        from tools.csharp.roslyn_integration import RoslynFirstRunner, RoslynRunnerOptions
        opts = RoslynRunnerOptions(enabled=False)
        runner = RoslynFirstRunner(
            root=tempfile.gettempdir(),
            options=opts,
            tree_sitter_fallback=lambda *a: ([],) * 7,
        )
        cache = runner.try_load(["/tmp/sample.cs"])
        self.assertIsNone(cache)


@unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
class MemberInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.mkdtemp()
        self.file_path = _write_sample(Path(self.temporary))
        self.payload = analyze_csharp_files(
            root=self.temporary, files=[self.file_path], verbose=False,
        )
        self.evidence = self.payload["results"][0]["evidence"]
        self.converted = roslyn_evidence_to_payload(self.evidence)

    def tearDown(self) -> None:
        shutil.rmtree(self.temporary, ignore_errors=True)

    def test_types_capture_class_struct_interface_record_enum(self) -> None:
        names = {t["name"]: t["kind"] for t in self.converted["types"]}
        self.assertEqual(names.get("Account"), "class")
        self.assertEqual(names.get("Money"), "struct")
        self.assertEqual(names.get("IAccountRepo"), "interface")
        self.assertEqual(names.get("AccountDto"), "record")
        self.assertEqual(names.get("AccountStatus"), "enum")

    def test_record_is_marked_in_evidence(self) -> None:
        account_dto = next(t for t in self.evidence["types"] if t["name"] == "AccountDto")
        self.assertTrue(account_dto.get("is_record"))

    def test_methods_and_constructors_extracted(self) -> None:
        names = [m["name"] for m in self.converted["functions"]]
        self.assertIn("DepositAsync", names)
        self.assertIn("ToString", names)
        # Two constructors
        ctor_count = sum(1 for m in self.converted["functions"] if m["member_kind"] == "constructor")
        self.assertEqual(ctor_count, 2)

    def test_properties_extracted(self) -> None:
        prop_names = [p["name"] for p in self.converted.get("properties", [])]
        self.assertIn("Id", prop_names)
        self.assertIn("Name", prop_names)
        self.assertIn("Amount", prop_names)

    def test_fields_extracted_with_modifiers(self) -> None:
        field_names = {f["name"]: f for f in self.converted.get("fields", [])}
        self.assertTrue(field_names["MaxAccounts"].get("is_const"))
        self.assertTrue(field_names["Default"].get("is_readonly"))
        self.assertTrue(field_names["Default"].get("is_static"))
        self.assertFalse(field_names["_balance"].get("is_const"))

    def test_events_extracted(self) -> None:
        event_names = [e["name"] for e in self.converted.get("events", [])]
        self.assertIn("Changed", event_names)

    def test_delegates_extracted(self) -> None:
        delegate_names = [d["name"] for d in self.converted.get("delegates", [])]
        self.assertIn("ChangedEventHandler", delegate_names)

    def test_type_inheritance_chain_present(self) -> None:
        # Account extends nothing meaningful; HeartbeatService extends BackgroundService
        # and AccountDto is a record with implicit inheritance.
        relations = self.converted["relations"]
        kinds = {r["rel_type"] for r in relations}
        self.assertIn("IMPLEMENTS_INTERFACE", kinds)
        self.assertIn("EXTENDS_CLASS", kinds)

    def test_resolved_call_target_captured(self) -> None:
        # Changed?.Invoke("deposit") should resolve to the ChangedEventHandler.
        calls = [c for c in self.converted["calls"] if c.get("callee_name") == "Invoke"]
        self.assertTrue(calls, "expected an Invoke call to be extracted")
        self.assertTrue(any(c.get("resolved") for c in calls))

    def test_generic_constraint_captured(self) -> None:
        # IAccountRepo<T> where T : Account, new()
        iface = next(t for t in self.evidence["types"] if t["name"] == "IAccountRepo")
        self.assertIn("T", iface.get("type_parameters", []))
        self.assertIn("T", iface.get("type_constraints", {}))

    def test_xml_doc_captured(self) -> None:
        # Account has a <summary> tag
        account = next(t for t in self.evidence["types"] if t["name"] == "Account")
        self.assertIn("Account", account.get("xml_doc", ""))


@unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
class FrameworkItemsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.mkdtemp()
        self.file_path = _write_sample(Path(self.temporary))
        self.payload = analyze_csharp_files(
            root=self.temporary, files=[self.file_path], verbose=False,
        )
        self.evidence = self.payload["results"][0]["evidence"]
        self.converted = roslyn_evidence_to_payload(self.evidence)

    def tearDown(self) -> None:
        shutil.rmtree(self.temporary, ignore_errors=True)

    def test_ef_mapping_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata=project_metadata_to_payload(self.payload.get("project")),
        )
        kinds = {item.kind for item in items}
        self.assertIn("EfEntityMapping", kinds)
        self.assertTrue(any(
            item.properties.get("is_db_context") for item in items
            if item.kind == "EfEntityMapping"
        ))
        self.assertTrue(any(
            item.properties.get("table_name") == "Account" for item in items
            if item.kind == "EfEntityMapping"
        ))

    def test_signalr_hub_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata={},
        )
        self.assertTrue(
            any(item.kind == "SignalRHub" and "ChatHub" in item.name for item in items),
            "expected SignalRHub ChatHub",
        )

    def test_grpc_service_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata={},
        )
        self.assertTrue(
            any(item.kind == "GrpcService" and "GreeterService" in item.name for item in items),
            "expected GrpcService GreeterService",
        )

    def test_background_service_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata={},
        )
        bg = [item for item in items if item.kind == "BackgroundService"]
        self.assertTrue(bg)
        self.assertEqual(bg[0].properties.get("execute_method"), "ExecuteAsync")

    def test_auth_policy_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata={},
        )
        policies = [item for item in items if item.kind == "AuthPolicy"]
        # Authorize@AdminController + AllowAnonymous@PublicAction
        self.assertGreaterEqual(len(policies), 2)

    def test_logging_telemetry_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata={},
        )
        log = [item for item in items if item.kind == "LoggingTelemetry"]
        # Should detect both ILogger<> and ActivitySource
        kinds = {item.properties.get("instrumentation_type") for item in log}
        self.assertIn("logger", kinds)
        self.assertIn("activity_source", kinds)

    def test_nuget_dependency_extracted(self) -> None:
        items = extract_framework_items(
            [(self.file_path, self.converted)],
            project_metadata={
                "project_path": "x.csproj",
                "packages": [
                    {"name": "Microsoft.EntityFrameworkCore", "version": "8.0.0", "is_development": False},
                    {"name": "Grpc.Tools", "version": "2.60.0", "is_development": True},
                ],
                "project_references": [],
            },
        )
        nuget = [item for item in items if item.kind == "NuGetDependency"]
        self.assertEqual(len(nuget), 2)
        self.assertTrue(any(item.properties.get("is_development") for item in nuget))

    def test_no_false_positives_for_unrelated_symbols(self) -> None:
        """Plain types/methods without framework patterns must NOT trigger items."""
        plain_payload = {
            "types": [
                {
                    "name": "Plain", "qualified_name": "Ns.Plain", "kind": "class",
                    "file_path": "x.cs", "start_line": 1, "end_line": 1,
                    "attributes": [], "base_types": [], "implemented_interfaces": [],
                },
            ],
            "functions": [
                {
                    "name": "M", "qualified_name": "Ns.Plain.M", "kind": "method",
                    "member_kind": "method", "symbol_id": "x",
                    "file_path": "x.cs", "start_line": 2, "end_line": 2,
                    "attributes": [], "scope_name": "Ns.Plain",
                },
            ],
            "fields": [],
            "events": [],
            "delegates": [],
            "parameters": [],
        }
        items = extract_framework_items(
            [("x.cs", plain_payload)],
            project_metadata={},
        )
        kinds = {item.kind for item in items}
        self.assertFalse(
            kinds & {"SignalRHub", "GrpcService", "BackgroundService", "EfEntityMapping"},
            f"unexpected framework items: {kinds}",
        )


class FrameworkRegistryTest(unittest.TestCase):
    def test_csharp_labels_cover_member_inventory(self) -> None:
        for label in ("Property", "Field", "Event", "Delegate",
                      "Parameter", "GenericParameter", "PackageReference"):
            self.assertIn(label, CSHARP_NODE_LABELS)

    def test_csharp_labels_cover_framework_items(self) -> None:
        for label in ("EfEntityMapping", "GrpcService", "SignalRHub",
                      "BackgroundService", "AuthPolicy", "LoggingTelemetry",
                      "NuGetDependency"):
            self.assertIn(label, CSHARP_NODE_LABELS)

    def test_csharp_relationships_cover_new_edges(self) -> None:
        for rel in ("HAS_PROPERTY", "HAS_FIELD", "HAS_EVENT", "HAS_DELEGATE",
                    "EXTENDS_CLASS", "IMPLEMENTS_INTERFACE",
                    "DEPENDS_ON_PACKAGE", "REFERENCES_PROJECT",
                    "MAPS_ENTITY", "EXPOSES_GRPC", "EXPOSES_HUB",
                    "RUNS_BACKGROUND", "ENFORCES_POLICY", "EMITS_LOG",
                    "TRACES_ACTIVITY", "SEMANTIC_OF"):
            self.assertIn(rel, CSHARP_RELATIONSHIP_TYPES)


@unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
class PerformanceTest(unittest.TestCase):
    def test_syntax_mode_completes_quickly(self) -> None:
        """Smoke test that a small file parses within a reasonable time."""
        with tempfile.TemporaryDirectory() as temporary:
            file_path = _write_sample(Path(temporary))
            start = time.time()
            payload = analyze_csharp_files(
                root=temporary, files=[file_path], verbose=False,
            )
            elapsed = time.time() - start
            self.assertLess(elapsed, 30.0, f"parse took {elapsed:.2f}s")
            self.assertTrue(payload["results"][0]["ok"])


class ConversionStatsTest(unittest.TestCase):
    def test_stats_count_each_kind(self) -> None:
        payload = {
            "types": [{"name": "A"}, {"name": "B"}],
            "functions": [{"name": "M1"}, {"name": "M2"}],
            "fields": [{"name": "f"}],
            "events": [{"name": "e"}],
            "delegates": [{"name": "d"}],
            "properties": [{"name": "p"}],
            "calls": [{"callee_name": "X", "resolved": True},
                      {"callee_name": "Y", "resolved": False}],
        }
        stats = stats_from_conversion(payload)
        self.assertEqual(stats.types, 2)
        self.assertEqual(stats.members, 2)
        self.assertEqual(stats.fields, 1)
        self.assertEqual(stats.events, 1)
        self.assertEqual(stats.delegates, 1)
        self.assertEqual(stats.properties, 1)
        self.assertEqual(stats.calls, 2)
        self.assertEqual(stats.resolved_calls, 1)


if __name__ == "__main__":
    unittest.main()
