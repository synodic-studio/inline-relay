# inline-dialogue MCP Server - Tool Definitions

## `get_threads`

Find all AUTHOR/AGENT threads in the codebase.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | string | No | Directory or file to search. Defaults to cwd. |

### Returns

```json
{
  "threads": [
    {
      "id": "a1b2c3",
      "file": "Sources/App/NetworkManager.swift",
      "start_line": 47,
      "thread": [
        {"role": "author", "line": 47, "text": "should we rename this to loadData?"},
        {"role": "agent", "line": 48, "text": "Renamed. Better reflects async nature."},
        {"role": "author", "line": 49, "text": ""}
      ],
      "status": "awaiting_user"
    }
  ],
  "summary": {"total": 5, "pending": 2, "awaiting_user": 3}
}
```

### Notes

- `id` is hash of file path + first AUTHOR text (stable even if lines shift)
- `status`: `pending` = has unprocessed AUTHOR text, `awaiting_user` = ends with blank AUTHOR line
- Thread detection: consecutive AUTHOR/AGENT lines = one thread. Code between = separate threads.
- If parsing fails: returns `{"parse_error": true, "file": "...", "raw_content": "..."}` so LLM can fall back to reading the file manually

---

## `respond_to_thread`

Add an AGENT response to a thread. Enforces formatting mechanically.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `thread_id` | string | Yes | ID from `get_threads` |
| `response` | string | Yes | The response text (without `// AGENT:` prefix) |
| `path` | string | No | Directory or file to search. Use same path as `get_threads`. |

### Returns

```json
{
  "success": true,
  "file": "Sources/App/NetworkManager.swift"
}
```

### Behavior

1. Locates thread by content hash (not line number - handles shifts from edits above)
2. Normalizes existing formatting if needed (moves inline markers to own line, removes indentation)
3. Appends `// AGENT: {response}` on own line, no indentation
4. Adds blank `// AUTHOR: ` line after

---

## `clear_and_commit`

Clear ALL thread markers from a file and commit that file only.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `file` | string | Yes | Path to the file |
| `message` | string | No | Commit message. Auto-generates if not provided. |

### Returns

```json
{
  "success": true,
  "file": "Sources/App/NetworkManager.swift",
  "lines_removed": 4,
  "commit_hash": "a1b2c3d",
  "other_staged_files": ["Tests/AppTests.swift"]
}
```

### Behavior

1. Removes ALL `// AUTHOR:` and `// AGENT:` lines from the file
2. Stages only this file
3. Commits with message
4. Other staged files remain staged (not committed)

---

## `dismiss_thread`

Remove a single thread without committing.

### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `thread_id` | string | Yes | ID from `get_threads` |
| `path` | string | No | Directory or file to search. Use same path as `get_threads`. |

### Returns

```json
{
  "success": true,
  "file": "Sources/App/NetworkManager.swift",
  "lines_removed": 4
}
```

### Behavior

- Removes AUTHOR/AGENT lines for this thread only
- Does NOT commit
- File remains modified in working directory
- Other threads in same file are preserved
