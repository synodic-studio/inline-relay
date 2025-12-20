---
description: >
  Use this skill when working with AUTHOR/AGENT inline code review threads.
  Triggers on: "inline dialogue", "AUTHOR/AGENT threads", "code review threads",
  "process threads", thread-related MCP tool usage.
---

# Inline Dialogue Workflow

Behavioral guidance for processing AUTHOR/AGENT inline code review threads.

## Bias Toward Action

Default to doing, not asking. The inline dialogue format is designed for quick iteration.

When AUTHOR requests something, do it and explain what you did. If uncertain about approach, pick the most reasonable one. The user can redirect in the next AUTHOR line.

**Wrong:**
```
// AUTHOR: extract this into a helper
// AGENT: I could create a formatDuration() helper. Want me to do that?
```

**Right:**
```
// AUTHOR: extract this into a helper [salt:pfr7]
// AGENT: Done. Created formatDuration() helper at line 23.
```

For truly large changes (many files, significant line count), consider offering to split into a separate PR - but bias toward completing the work unless it's clearly unwieldy.

## Termination Commands

When AUTHOR writes exactly `done`, `reset`, `commit`, or `commit file`:
- These have `action_required` in the thread data
- Execute the action immediately (no response needed)
- `done`/`reset` → `dismiss_thread(thread_id, path)`
- `commit`/`commit file` → `clear_and_commit(thread_id, path, message)`

## Critical Rules

1. **Use Edit tool for code changes** - Write could destroy thread markers
2. **Treat thread markers as read-only** - Never edit existing `// AUTHOR:` or `// AGENT:` lines
3. **Only use respond_to_thread for responses** - It appends, never replaces
4. **Preserve full thread history** - The author uses this to follow the conversation; never condense or rewrite previous exchanges
5. **Edit surgically** - When making code changes near a thread, work around it
