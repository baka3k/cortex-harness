# Phase 03: Capability Validation and Discovery UX

## Goal

Make MCP discovery and query failures explain the actual support boundary.

## Implementation

1. Update `list_parsers` to return canonical parser, aliases, backend,
   support level, and feature capabilities rather than directory-derived names.
2. Add a capability inspection response or fields to parser activation so users
   can see supported labels, relationships, and operations.
3. Validate parser-specific labels, relationship names, and feature requests
   before dispatch; return structured unsupported/partial diagnostics.
4. Keep generic graph queries available for `generic` profiles while marking
   framework-specific operations unavailable.
5. Document the distinction between parser profile, physical backend, and MCP
   server in the MCP README and discovery instructions.

## Acceptance

- `list_parsers` no longer overstates capabilities based on filesystem layout.
- Unsupported operations produce actionable messages naming the parser and
  missing capability.
- Alias, activation, and discovery responses are deterministic and stable.
