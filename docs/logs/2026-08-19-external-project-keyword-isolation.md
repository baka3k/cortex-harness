# External project-keyword isolation — 2026-08-19

## Context

Repository fixtures and documentation had accumulated deployment-specific names.
Those examples created a risk that source ingestion behavior would become coupled
to one project and that sensitive identifiers would persist in tracked artifacts.
The repository needed a repeatable isolation gate without storing the sensitive
term list beside the code it checks.

## Change

- Added a repository scanner whose denylist is supplied only by command-line
  files or an environment variable; absence of an external denylist fails closed
  (`scripts/check_project_isolation.py:22`,
  `scripts/check_project_isolation.py:171`,
  `scripts/check_project_isolation.py:197`).
- The scanner checks tracked path names and file contents in the working tree,
  index, or both. It normalizes Unicode width and case, recognizes common legacy
  encodings, and reports only a short keyword hash rather than the matching term
  (`scripts/check_project_isolation.py:35`,
  `scripts/check_project_isolation.py:111`,
  `scripts/check_project_isolation.py:130`).
- Project-specific fixtures and examples were replaced with generic synthetic
  names, and the generic shell fixture is explicitly tracked despite the broad
  fixture ignore rule (`.gitignore:79`,
  `tests/fixtures/shell-application/batch_entry.sh:1`,
  `plans/260731-1500-legacy-migration-parser-coverage/phase-03-shell-script-analyzer.md:5`).
- Tests exercise encoded content, path-name findings, index-only findings,
  Unicode normalization, and missing-denylist failure
  (`tests/test_project_isolation.py:32`,
  `tests/test_project_isolation.py:47`,
  `tests/test_project_isolation.py:60`,
  `tests/test_project_isolation.py:70`).

## Impact

**Risk level: medium.** Tracked source, tests, fixtures, and documentation can be
checked against organization-owned project terms without copying those terms
into the repository or logs. A final working-tree scan with the external list
verified 1,191 tracked files and found no occurrences.

Index scanning is intentionally separate from working-tree scanning, so a local
unstaged cleanup can be verified before staging and the combined view can be
enforced after the index is updated.

## Decision

The denylist remains outside version control and the scanner emits hashes only.
This avoids making the enforcement mechanism a new source of sensitive strings.
The gate scans both paths and decoded content because identifier leakage can live
in fixture filenames as well as source text, and normalization prevents width or
case variants from bypassing the check.

## References

- Isolation scanner: `scripts/check_project_isolation.py:1`
- Scanner tests: `tests/test_project_isolation.py:32`
- Generic fixture: `tests/fixtures/shell-application/batch_entry.sh:1`
- Related parser plan: [Shell script analyzer](../../plans/260731-1500-legacy-migration-parser-coverage/phase-03-shell-script-analyzer.md)
