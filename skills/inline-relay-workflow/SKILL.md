---
description: >
  Use this skill when working with AUTHOR/AGENT inline code review threads.
  Triggers on: "inline dialogue", "AUTHOR/AGENT threads", "code review threads",
  "process threads", running the inline-relay CLI.
---

# Inline Dialogue Workflow

Behavioral guidance for processing AUTHOR/AGENT inline code review threads.

## Running the CLI

All thread operations go through the `inline-relay` CLI. It prints JSON to
stdout and exits non-zero on failure. Invoke it with `--project` so the plugin
is selected *without* changing the working directory — paths you pass then
resolve against the project you're reviewing, not the plugin:

```bash
IR="uv run --project \"$CLAUDE_PLUGIN_ROOT\" inline-relay"

# Find all threads under a path (default ".")
$IR get-threads path/to/scan

# Remove a finished thread (only when AUTHOR wrote done/reset)
$IR dismiss --id THREAD_ID --path path/to/scan

# Clear a file's markers and commit that file
$IR clear-commit --file path/to/file.swift --message "optional message"

# Execute every pending done/reset/commit action in one pass
$IR process-all path/to/scan
```

**Responding is the one that needs care.** Never pass the response as a shell
argument — quotes, `$`, `|`, and backticks in a normal review reply will break
or inject. Instead, write the response to a temp file with the Write tool, then
pass it with `--response-file`:

```bash
# 1. Write the response text to a scratch file (Write tool), then:
$IR respond --id THREAD_ID --path path/to/scan --response-file /tmp/reply.txt
```

The response text is a single-line comment; keep it to one line. Reading the
JSON back tells you `success`, plus any `warning` (e.g. future-tense language)
or `appended` note.

## Never edit markers with Edit/Write

`respond`, `dismiss`, `clear-commit`, and `process-all` are the ONLY ways to
add or remove `// AUTHOR:` / `// AGENT:` lines. The pre-tool-use hook blocks
Edit/Write that touch marker lines. Use Edit freely for the surrounding code.

## Act First, Then Report

Your response becomes permanent record. The workflow is:

1. **Read** the AUTHOR request
2. **Execute** the action (edit code, add comment, refactor, etc.)
3. **Then respond** describing what you DID - past tense only

Your response is a receipt for completed work, not a statement of intent.

**Wrong:**
```
// AUTHOR: extract this into a helper
// AGENT: I'll create a formatDuration() helper.
```

**Right:**
```
// AUTHOR: extract this into a helper
// AGENT: Created formatDuration() helper at line 23.
```

**Also wrong:**
```
// AUTHOR: remove the #if canImport check
// AGENT: Will remove it along with the one at the top.
```

**Right:**
```
// AUTHOR: remove the #if canImport check
// AGENT: Removed both #if canImport checks (lines 8 and 42).
```

When AUTHOR asks for something, do it first, then describe what you did. If uncertain about approach, pick the most reasonable one. The user can redirect in the next AUTHOR line.

For truly large changes (many files, significant line count), consider offering to split into a separate PR - but bias toward completing the work unless it's clearly unwieldy.

## Termination Commands

When AUTHOR writes exactly `done`, `reset`, `commit`, or `commit file`:
- These have `action_required` in the `get-threads` output
- Execute the action immediately (no response needed)
- `done`/`reset` → `dismiss --id THREAD_ID --path PATH`
- `commit`/`commit file` → `clear-commit --file FILE`
- Or handle every pending action in one call with `process-all PATH`

## What IS and ISN'T Blocked

Thread markers do NOT block edits to surrounding code. Only edits that touch the `// AUTHOR:` or `// AGENT:` lines themselves are blocked.

**You CAN:**
- Edit code above, below, or around thread markers
- Add new code near threads
- Refactor code in the same file as threads

**You CANNOT:**
- Edit or delete `// AUTHOR:` or `// AGENT:` lines directly (use the inline-relay CLI instead)
- Use Write tool on files containing threads (use Edit tool)

If you think "threads are blocking my edits" - you're wrong. Make the code changes, then respond.

## Editing Around Threads

When code you need to change has thread markers in the middle, make **separate edits** above and below:

**Scenario:** You need to refactor this function, but there's a thread in it:
```swift
func process() {
    let data = fetch()
    // AUTHOR: should this use async/await?
    // AGENT: Good idea, but needs broader changes.
    // AUTHOR: 
    transform(data)
    save(data)
}
```

**Wrong:** Trying to edit the whole function at once (old_string includes markers → blocked)

**Right:** Make two edits:
1. Edit lines above the thread (`let data = fetch()`)
2. Edit lines below the thread (`transform(data)` and `save(data)`)
3. Leave the thread markers untouched

The thread stays in place. After the author dismisses it, it disappears.

## Critical Rules

1. **Use Edit tool for code changes** - Write could destroy thread markers
2. **Treat thread markers as read-only** - Never edit existing `// AUTHOR:` or `// AGENT:` lines
3. **Only use the `respond` command for responses** - It appends, never replaces
4. **Preserve full thread history** - The author uses this to follow the conversation; never condense or rewrite previous exchanges
5. **Edit surgically** - When making code changes near a thread, work around it

## Verification Before Response

Before running the `respond` command:

1. **Confirm the change exists** - File is saved, code is present
2. **Be specific** - Reference line numbers, function names, file paths
3. **Never claim future work** - "Created X" not "Will create X"

If you realize you haven't actually made the change, make it first. Don't respond with what you "would" do.

**Vague (bad):**
```
// AGENT: Done.
// AGENT: Fixed it.
// AGENT: Added the test.
```

**Specific (good):**
```
// AGENT: Removed redundant check at line 45.
// AGENT: Added null guard in processData() before the forEach.
// AGENT: Added TestActionCommand.test_reset_clears_state in test_server.py.
```
