---
description: >
  Use this skill when working with AUTHOR/AGENT inline code review threads.
  Triggers on: "inline dialogue", "AUTHOR/AGENT threads", "code review threads",
  "process threads", thread-related MCP tool usage.
---

# Inline Dialogue Workflow

Behavioral guidance for processing AUTHOR/AGENT inline code review threads.

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
- These have `action_required` in the thread data
- Execute the action immediately (no response needed)
- `done`/`reset` → `dismiss_thread(thread_id, path)`
- `commit`/`commit file` → `clear_and_commit(thread_id, path, message)`

## What IS and ISN'T Blocked

Thread markers do NOT block edits to surrounding code. Only edits that touch the `// AUTHOR:` or `// AGENT:` lines themselves are blocked.

**You CAN:**
- Edit code above, below, or around thread markers
- Add new code near threads
- Refactor code in the same file as threads

**You CANNOT:**
- Edit or delete `// AUTHOR:` or `// AGENT:` lines directly (use MCP tools instead)
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
3. **Only use respond_to_thread for responses** - It appends, never replaces
4. **Preserve full thread history** - The author uses this to follow the conversation; never condense or rewrite previous exchanges
5. **Edit surgically** - When making code changes near a thread, work around it
