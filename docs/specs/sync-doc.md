
---

## Objectives

* Provide a CLI command to synchronize rce doc into the analysis system (**Neo4j + Qdrant**) following the **doc-tiny** pipeline.
* Support two modes: **Full Sync (all)** and **Incremental Sync** (changed files only).

## Configuration

* Retrieve configuration from the active config file, specifically the `doc.source` field:

```json
{
  "doc": {
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

* `dev sync doc`
* Lists folders defined in `doc.source.folder` for user selection (interactive UI).
* Defaults to **incremental** mode if metadata from a previous sync exists; otherwise, defaults to **full** sync.


* `dev sync doc all`
* Synchronizes all configured folders in **full** mode (pushes all files into the `doc-tiny` pipeline).



## Processing Workflow

1. **Init:** Read the active configuration and (optionally) update or clone the repository if a `git` URL is provided.
2. **Execution:** For each selected folder:
* **Full Mode:** Send the entire docbase to the `doc-tiny` pipeline for ingestion into Neo4j + Qdrant.
* **Incremental Mode:** Identify modified files (via `git diff` or timestamp comparison or hash ) and only send those specific files.


3. **Persistence:** Record sync metadata (timestamp, synced file list, mode) to serve as a baseline for future incremental runs.

## Integration with doc-tiny

* Utilize the exact ingestion endpoints and formats defined by **doc-tiny** (supporting both bulk and file-based ingestion).
* Refer to **doc-tiny** specs to align metadata mapping, batch sizes, and retry policies.

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
dev sync doc           # Interactive folder selection; incremental by default if metadata exists
dev sync doc all       # Full synchronization for all configured folders

```

## Scalability

* **Future Additions:** Detailed API endpoint documentation with field mapping to `doc-tiny`, payload examples, or boilerplate scripts for clone + diff logic.