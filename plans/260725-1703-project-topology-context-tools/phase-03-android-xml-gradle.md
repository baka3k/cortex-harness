# Phase 03: Deep Android XML and Gradle Semantics

## Context

Android analysis already creates useful manifest, component, resource, and
Gradle facts, but coverage is narrow and duplicated across Java/Kotlin paths.
This phase deepens Android-specific semantics while aligning module identity
with the topology overlay.

## Requirements

- Parse Android manifests beyond components.
- Parse relevant Android resource XML structures and references.
- Classify Android app/library/dynamic-feature modules accurately.
- Reuse the shared Gradle/settings facts and canonical module IDs.
- Preserve existing labels, IDs where compatible, and Android MCP behavior.

## Architecture

Keep Android-specific models under `tools/android`, but delegate shared
descriptor/module resolution to `tools/project_topology`.

Add or extend facts for:

- application/manifest metadata;
- declared and requested permissions;
- uses-feature/uses-sdk/query/instrumentation evidence;
- first-class intent filters and data/deep-link patterns;
- resource files, value entries, view hierarchy, navigation/menu/config nodes;
- resource-to-resource and source-to-resource references.

Use a single shared Android ingestion path where practical. If Java and Kotlin
entrypoints must remain, both call the same extraction/writer helpers and pass
deduplicated stable facts.

## Related Files

- `code-tiny/tools/android/android_common.py`
- `code-tiny/tools/android/android_kotlin_analyzer.py`
- `code-tiny/tools/android/android_java_analyzer.py`
- `code-tiny/tools/android/android_mixed_analyzer.py`
- Shared Gradle parser/resolver from Phase 02
- Android graph write helpers or new focused writer
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/scripts/setup_constraints.py`
- New `tests/fixtures/android-topology/`
- New `tests/test_android_manifest_semantics.py`
- New `tests/test_android_resource_semantics.py`
- New `tests/test_android_module_topology.py`

## Implementation Steps

1. Extend manifest models with application attributes, SDK/version evidence,
   permission/feature/query/instrumentation/metadata facts, parser provenance,
   and diagnostics.
2. Parse every component and intent filter with precise component ownership,
   exported/enabled/permission/process attributes, categories, and data patterns.
3. Derive `effective_exported` only when Android rules and target SDK evidence
   make it safe; otherwise retain `unknown` plus diagnostic.
4. Add secure, bounded resource XML parsing:
   - value resources (`string`, plurals, arrays, styles, colors, dimens, bools,
     integers and aliases) without leaking secret-like values;
   - layout/view hierarchy and IDs;
   - navigation destinations/actions/deep links;
   - menus and selected preference/config structures;
   - `@type/name` and `?attr/name` references.
5. Represent qualifier variants without collapsing locale/device-specific files
   into one lossy record.
6. Consume Phase 02 Gradle/settings facts to classify application, library,
   dynamic feature, JVM-only, test, and unknown modules.
7. Resolve `project(...)`, type-safe project accessor, and dynamic-feature
   dependencies to canonical modules when statically known.
8. Attach manifests/resources/components to owning modules and link source
   references without creating duplicate project-level orphan facts.
9. Update Android profile labels/relationships and schema setup additively.
10. Add malformed XML, namespace, manifest-placeholder, flavor/source-set,
    duplicate qualifier, and mixed Java/Kotlin regression cases.

## Todo

- [ ] Manifest semantics cover permissions, features, queries, instrumentation,
  metadata, components, and intent filters.
- [ ] Resource facts cover values, hierarchy, navigation, qualifiers, and
  references.
- [ ] Android module kinds and dependencies use canonical topology IDs.
- [ ] Java/Kotlin Android entrypoints cannot duplicate facts.
- [ ] Existing Android graph/MCP behavior remains compatible.

## Risks

- Manifest merging happens during the Android build. Static source manifests may
  conflict; retain source-set provenance and never present an unbuilt merge as
  final truth.
- Resource values can contain credentials. Store identifiers/type/provenance and
  safe summaries; redact secret-like raw values.
- XML files can be large or malformed. Enforce size/depth/node-count limits and
  secure entity handling.
- Qualifier variants need stable distinct IDs even when logical resource names
  match.

## Success Criteria

- Fixture assertions prove every requested manifest and resource fact.
- No duplicate IDs/edges occur when both Android language paths participate.
- Module classification distinguishes app, library, and dynamic feature with
  evidence.
- Malformed/unsupported XML produces diagnostics and does not stop unrelated
  modules.

