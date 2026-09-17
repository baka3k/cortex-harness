# Phase 01: Capability Registry and Alias Normalization

## Goal

Create one authoritative, validated registry for parser aliases and backend
capabilities.

## Implementation

1. Extend `FrameworkQueryConfig` or introduce a shared capability model with:
   canonical name, aliases, backend, support level, labels, relationships,
   searchable properties, default query profiles, and feature flags.
2. Move/derive Unified MCP alias sets from the registry while preserving Android
   precedence and existing generic language aliases.
3. Validate aliases are unique, canonical names are stable, and every profile
   references an allowed backend.
4. Define support levels for generic C++, Android, Spring, Struts, Flutter,
   ASP.NET, COBOL, Perl, and other registered profiles.
5. Add a compatibility adapter so existing callers of `framework_for_parser`,
   `parser_aliases`, and backend dispatch keep their current API.

## Acceptance

- One alias maps to one canonical profile.
- Android aliases still resolve to Android.
- Framework aliases resolve to their framework profile and `cplus` backend.
- Unknown aliases retain current generic behavior or produce a clear error,
  according to the existing API contract.
