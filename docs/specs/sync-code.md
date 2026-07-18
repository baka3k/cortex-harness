
---

## Objectives

* Provide a CLI command to synchronize source code into the analysis system (**Neo4j + Qdrant**) following the **code-tiny** pipeline.
* Support explicit **Full Sync** and reliable **Incremental Sync** (changed files only). The `all` subcommand selects analyzers and folders; it does not force full mode.

## Configuration

* Retrieve configuration from the active config file, specifically the `code.source` field:

```json
{
  "code": {
    "source": {
      "git": "<optional-git-url>",
      "folder": [
        "path/to/project1",
        "path/to/project2"
      ]
    }
  }
}

```

## CLI Behavior

* `dev sync code`
* Lists folders defined in `code.source.folder` for user selection (interactive UI).
* Defaults to **incremental** mode if metadata from a previous sync exists; otherwise, defaults to **full** sync.


* `dev sync code all`
* Synchronizes all non-overlapping configured roots with every available analyzer. It remains incremental after the first baseline unless `--full-scan` is supplied.



## Processing Workflow

1. **Init:** Read the active configuration and (optionally) update or clone the repository if a `git` URL is provided.
2. **Execution:** For each selected folder:
* **Full Mode:** Send the entire codebase to the `code-tiny` pipeline for ingestion into Neo4j + Qdrant.
* **Incremental Mode:** Use Git as a candidate index across committed, staged, unstaged, and untracked states, then compare a versioned SHA-256 inventory before selecting files. Non-Git roots use full hash comparison.
* **Modules/submodules:** Constrain monorepo diffs to the configured module root, recursively discover initialized submodules, and record explicit warnings for uninitialized coverage. Overlapping configured roots are deduplicated by canonical path.
* **Primary ownership:** Perl `.pl`, `.pm`, and `.t` files are routed exclusively to the `perl` analyzer. Standalone `.pod` files and extensionless shebang scripts remain unowned until content-based classification is introduced.
* **Framework overlays:** Preserve exclusive primary-language ownership, then route the global changed/deleted/impacted set through Spring, Servlet/JSP, MyBatis, Struts, Flutter, ASP.NET Framework, and ASP.NET Core detectors. ASP.NET overlays require the canonical `csharp` analyzer, are detected per project module, and never claim `.cs` ownership.


3. **Persistence:** Publish an immutable inventory generation, then atomically update schema-v2 sync state. A v1 state is backed up and conservatively rebuilt by a full scan.

Framework failures mark sync state dirty but do not roll back successful canonical language ingestion. Non-framework projects skip overlay subprocesses after detection. Framework summary entries report prerequisites, detector evidence, vector-writing capability, semantic seed collections, duration, and failure state.

## Integration with code-tiny

* Utilize the exact ingestion endpoints and formats defined by **code-tiny** (supporting both bulk and file-based ingestion).
* Refer to **code-tiny** specs to align metadata mapping, batch sizes, and retry policies.
* The Perl analyzer supports full and changed/deleted manifest runs, deterministic parse caching, service-free JSON previews, and canonical `File`/`Namespace`/`Function`/`Field` graph facts. Dynamic Perl dispatch remains explicitly unresolved.
* The ASP.NET Framework and ASP.NET Core analyzers use a shared Roslyn evidence protocol, safe bounded config/view parsers, redaction, module-scoped incremental invalidation, and one migration vocabulary. Workspace failures in `auto` mode remain explicit partial coverage rather than resolved semantic edges.

## UX & Status

* **Pre-sync (Optional):** Provide a `preview` to list files queued for synchronization in incremental mode.
* **In-progress:** Display progress bars, completion rates, and summary logs.
* **Post-sync:** Generate a summary report with outcome, source provenance, repository coverage, lock state, file totals, elapsed time, and errors.

## Operations & Safety

* **First-run Policy:** The initial run for any folder must default to **full** if no prior metadata is detected.
* **Resilience:** Implement retry/exponential backoff for network issues or ingestion failures (Qdrant/Neo4j). Lock-busy exit code `2` is not retried.
* **Security:** Filter or exclude sensitive files (e.g., those containing secrets) using patterns before transmission.

## Example Commands

```bash
dev sync code           # Interactive folder selection; incremental by default if metadata exists
dev sync code all       # All analyzers/folders; incremental unless --full-scan is supplied
dev sync code --change-detection hash --reconcile

```

## Scalability

* **Future Additions:** Detailed API endpoint documentation with field mapping to `code-tiny`, payload examples, or boilerplate scripts for clone + diff logic.
