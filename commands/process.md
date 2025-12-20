---
description: Process AUTHOR/AGENT inline code review threads
argument-hint: "[path]"
allowed-tools:
  - Read
  - Edit
  - mcp__inline-dialogue__get_threads
  - mcp__inline-dialogue__respond_to_thread
  - mcp__inline-dialogue__dismiss_thread
  - mcp__inline-dialogue__clear_and_commit
---

# Process Inline Dialogue Threads

Scan for and process all AUTHOR/AGENT threads in the codebase.

## Workflow

1. `get_threads(path)` - Find all threads using the exact path provided by the user (file or directory). If no path given, use "."
2. For each thread:
   - If `action_required`: execute via MCP tool (`dismiss_thread(thread_id, path)` or `clear_and_commit(thread_id, path, message)`)
   - If `awaiting_agent`: read context, make changes with Edit, then `respond_to_thread(thread_id, response, path)`
   - If `awaiting_author`: skip (user's turn)
3. Loop until no `awaiting_agent` threads remain

## Arguments

- `path` (optional): File or directory to scan. **IMPORTANT**: If a path is provided, pass it exactly to `get_threads(path)`. Only use "." if no path argument was given.
