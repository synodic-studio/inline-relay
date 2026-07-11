---
description: Process AUTHOR/AGENT inline code review threads
argument-hint: "[path]"
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash
---

# Process inline-relay Threads

Scan for and process all AUTHOR/AGENT threads in the codebase using the
`inline-relay` CLI. Follow the **inline-relay-workflow** skill for behavioral
rules (act first, past tense, editing around markers).

The CLI lives in the plugin; invoke it as:

```bash
uv --directory "$CLAUDE_PLUGIN_ROOT" run inline-relay <subcommand> ...
```

## Workflow

1. **Find threads.** Run `get-threads` with the path argument (a file or
   directory). If no path was given, use `.`:
   ```bash
   uv --directory "$CLAUDE_PLUGIN_ROOT" run inline-relay get-threads "$PATH"
   ```
   Parse the JSON. Each thread has an `id`, `status`, and possibly an
   `action_required`.

2. **Handle each thread by status:**
   - `action_required` → execute it immediately (no response):
     - dismiss: `inline-relay dismiss --id ID --path "$PATH"`
     - commit: `inline-relay clear-commit --file FILE`
     - Or run `inline-relay process-all "$PATH"` to clear every pending action at once.
   - `awaiting_agent` → read the code, **make the change with Edit**, verify it,
     then respond: write the reply text to a temp file with the Write tool and
     run `inline-relay respond --id ID --path "$PATH" --response-file TMPFILE`.
   - `awaiting_author` → skip (it's the human's turn).

3. **Loop** until no `awaiting_agent` threads remain.

## Arguments

- `path` (optional): File or directory to scan. If provided, pass it exactly to
  `get-threads`. Only use `.` if no path argument was given.

## Critical: Act First, Verify, Then Respond

For each `awaiting_agent` thread:
1. **Make the change** - Edit the code, save the file
2. **Verify it worked** - Confirm the change exists
3. **Then respond** - `respond` with a past-tense description of completed work

Never respond with what you "will" do. Responses are receipts for completed
work. Never pass the response as a shell argument — always use `--response-file`
so quotes, `$`, `|`, and backticks survive intact.
