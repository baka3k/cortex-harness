# Phase 05: Complete Advanced Semantics and Hardening

## Context

The design specification's final success criteria require state, dependency, localization/theme, and platform integration graphs beyond the focused MVP. These capabilities are package- and platform-sensitive and should be added only after the core contract is stable.

## Requirements

- Support the state, DI, navigation-package, localization/theme, and platform constructs named in the specification.
- Resolve package APIs by library/element identity and record package versions.
- Link Method/Event/BasicMessage channels to native artifacts when evidence exists.
- Preserve provider parity, bounded incremental behavior, and query performance.
- Document unsupported dynamic patterns and version coverage.

## Architecture

Add Python extractor plugins behind the stable fact protocol rather than branching the main visitor. Each plugin declares package URI/version support, emitted semantic kinds, and evidence/confidence rules. Native bridges reuse existing Android graph nodes and add iOS/plist-derived platform facts without duplicating canonical native symbols.

Advanced extractors:

- state: ChangeNotifier, ValueNotifier, Provider, Riverpod, Bloc, Cubit, GetX;
- dependency injection: get_it and injectable registrations/consumers;
- routing: GoRouter and AutoRoute declarations and calls;
- presentation resources: ThemeData/theme usage and ARB localization keys/usages;
- platform: MethodChannel, EventChannel, BasicMessageChannel, AndroidManifest, Info.plist, Gradle, and native handler links.

## Related Files

Create under `code-tiny/tools/flutter/extractors/`:

- `state_extractor.py`
- `dependency_injection_extractor.py`
- `router_package_extractor.py`
- `theme_localization_extractor.py`
- `platform_channel_extractor.py`

Create or extend:

- `code-tiny/tools/flutter/native_resolver.py`
- `tests/fixtures/flutter-app/android/`
- `tests/fixtures/flutter-app/ios/`
- `tests/test_flutter_advanced_semantics.py`
- `tests/test_flutter_native_bridge.py`
- `tests/test_flutter_provider_parity.py`
- `tests/test_flutter_performance.py`

Modify:

- `code-tiny/mcp/framework_registry.py`
- `code-tiny/scripts/setup_constraints.py`
- `docs/HARNESS_WORKFLOW.md`
- relevant installation documentation for Python parser prerequisites.

## Implementation Steps

1. Add a package-version-aware extractor registry and explicit unsupported-version diagnostics.
2. Implement core notifier and named state-management extractors with provider/consumer flow edges.
3. Implement get_it/injectable registration and dependency-consumer edges.
4. Implement GoRouter/AutoRoute extraction and merge results with core navigation without duplicate routes.
5. Parse themes and ARB resources; link declarations, keys, and code usage.
6. Extract platform channels and correlate channel names/methods with existing Android and new iOS platform evidence.
7. Run logical parity tests on Neo4j and FalkorDB, performance/incremental baselines, and the full regression suite.
8. Document package-version coverage, confidence semantics, limitations, setup, and extension points.

## Todo

- [ ] Define the supported package/version matrix.
- [ ] Add one positive and one ambiguous fixture for every extractor family.
- [ ] Verify native channel links never rely on channel name alone when multiple candidates exist.
- [ ] Establish full-scan and one-file incremental baselines.
- [ ] Complete live provider parity or record explicit environment exclusions.

## Risks

- State and routing packages evolve independently and may use generated code heavily.
- Channel names can be dynamic or shared, making native correlation ambiguous.
- Adding all advanced relationships to default graph expansion can increase query fan-out.
- Live provider services may be unavailable even when logical contract tests pass.

## Success Criteria

- The fixture reconstructs state, DI, advanced navigation, theme/localization, and platform-channel graphs.
- All package-specific facts include detected package version and source evidence.
- Ambiguous native and dynamic targets are reported with confidence/diagnostics.
- One-file changes remain bounded to affected dependency and semantic regions.
- Neo4j/FalkorDB logical parity and MCP query tests pass or have explicit, evidence-backed environment exclusions.
- The spec-complete gate in `plan.md` is satisfied.
