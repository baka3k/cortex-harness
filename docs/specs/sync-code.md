
---

## Objectives

* Provide a CLI command to synchronize source code into the analysis system (**Neo4j + Qdrant**) following the **code-tiny** pipeline.
* Support two modes: **Full Sync (all)** and **Incremental Sync** (changed files only).

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
* Synchronizes all configured folders in **full** mode (pushes all files into the `code-tiny` pipeline).



## Processing Workflow

1. **Init:** Read the active configuration and (optionally) update or clone the repository if a `git` URL is provided.
2. **Execution:** For each selected folder:
* **Full Mode:** Send the entire codebase to the `code-tiny` pipeline for ingestion into Neo4j + Qdrant.
* **Incremental Mode:** Identify modified files (via `git diff` or timestamp comparison) and only send those specific files.


3. **Persistence:** Record sync metadata (timestamp, synced file list, mode) to serve as a baseline for future incremental runs.

## Integration with code-tiny

* Utilize the exact ingestion endpoints and formats defined by **code-tiny** (supporting both bulk and file-based ingestion).
* Refer to **code-tiny** specs to align metadata mapping, batch sizes, and retry policies.

## UX & Status

* **Pre-sync (Optional):** Provide a `preview` to list files queued for synchronization in incremental mode.
* **In-progress:** Display progress bars, completion rates, and summary logs.
* **Post-sync:** Generate a summary report (total files, elapsed time, and error logs).

## Operations & Safety

* **First-run Policy:** The initial run for any folder must default to **full** if no prior metadata is detected.
* **Resilience:** Implement retry/exponential backoff for network issues or ingestion failures (Qdrant/Neo4j).
* **Security:** Filter or exclude sensitive files (e.g., those containing secrets) using patterns before transmission.

## Example Commands

```bash
dev sync code           # Interactive folder selection; incremental by default if metadata exists
dev sync code all       # Full synchronization for all configured folders

```

## Scalability

* **Future Additions:** Detailed API endpoint documentation with field mapping to `code-tiny`, payload examples, or boilerplate scripts for clone + diff logic.