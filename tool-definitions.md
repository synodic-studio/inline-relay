# inline-relay CLI Reference

The `inline-relay` CLI does all deterministic thread parsing and marker
manipulation. Each subcommand prints a JSON result to stdout and exits non-zero
when the result reports a failure (an `error` key, or `success` false).

Invoke it with `--project` so the plugin is selected without changing the
working directory — relative paths then resolve against the project you're
reviewing, not the plugin:

```bash
uv run --project "$CLAUDE_PLUGIN_ROOT" inline-relay <subcommand> ...
```

(For local development in this repo: `uv run inline-relay <subcommand>`.)

---

## `get-threads [PATH]`

Find all AUTHOR/AGENT threads under `PATH` (a file or directory; default `.`).

Normalizes inline AUTHOR comments to their own line, auto-salts duplicate
thread IDs, and reports threads elsewhere in the repo (read-only).

```json
{
  "threads": [
    {
      "id": "e4af2648",
      "file": "Sources/App/NetworkManager.swift",
      "start_line": 47,
      "thread": [
        {"role": "author", "line": 47, "text": "should we rename this to loadData?"},
        {"role": "agent", "line": 48, "text": "Renamed. Better reflects async nature."}
      ],
      "status": "awaiting_author"
    }
  ],
  "summary": {"total": 1, "awaiting_agent": 0, "awaiting_author": 1}
}
```

Notes:
- `id` is `SHA256(file_path + first_author_text)[:8]` — stable across line drift.
- `status`: `awaiting_agent` (last AUTHOR has text) or `awaiting_author` (ends with a blank AUTHOR placeholder).
- A thread carrying a termination command (`done`/`reset`/`commit`) gets an `action_required` field.
- If parsing fails, returns `{"parse_error": true, "file": "...", "raw_content": "..."}`.

---

## `respond --id ID [--path PATH] [--response-file FILE]`

Add an AGENT response to a thread. Locates the thread by content hash (survives
line drift), appends the AGENT line with the file's native comment prefix and
indentation, and adds a trailing empty AUTHOR placeholder.

The response text comes from `--response-file FILE` (recommended) or, if the
flag is omitted, from **stdin**. Never pass it as a shell argument — a normal
review reply contains quotes, `$`, `|`, or backticks that would break or inject.
The text is treated as a single-line comment; a trailing newline is stripped.

```json
{"success": true, "file": "Sources/App/NetworkManager.swift"}
```

Behavior:
- Blocks a response identical to the previous AGENT line (`Duplicate response blocked`).
- A *different* response to a thread awaiting the author is appended to the existing AGENT line (`"appended": true`).
- Future-tense phrasing ("I will fix…") adds a `warning` — do the work first and respond in past tense.

---

## `dismiss --id ID [--path PATH]`

Remove a single thread's markers without committing. Permitted only when the
thread carries a dismiss action (AUTHOR wrote `done` or `reset`). Other threads
in the same file are preserved.

```json
{"success": true, "file": "Sources/App/NetworkManager.swift", "lines_removed": 4}
```

---

## `clear-commit --file FILE [--message MSG]`

Remove ALL AUTHOR/AGENT markers from `FILE`, stage only that file, and commit
it. Other staged files remain staged but are not committed. Message
auto-generates if omitted.

```json
{
  "success": true,
  "file": "Sources/App/NetworkManager.swift",
  "lines_removed": 4,
  "commit_hash": "a1b2c3d",
  "other_staged_files": ["Tests/AppTests.swift"]
}
```

---

## `process-all [PATH]`

Execute every pending termination command under `PATH` in one pass. Commits run
before dismissals; if a file has both, the commit takes priority (it clears
everything).

```json
{
  "success": true,
  "actions_executed": 2,
  "results": [
    {"action": "clear_and_commit", "file": "a.swift", "lines_removed": 3, "commit_hash": "abc1234"},
    {"action": "dismiss_thread", "thread_id": "e4af2648", "file": "b.swift", "lines_removed": 4}
  ]
}
```
