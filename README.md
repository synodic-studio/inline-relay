# inline-dialogue-plugin

Claude Code plugin for AUTHOR/AGENT inline code review threads.

## Overview

This plugin provides:
- **Command** `/inline-dialogue:process` - Kick off thread processing workflow
- **Skill** - Behavioral guidance for working with threads (loads contextually)
- **MCP server** - Tools for scanning, reading, and responding to threads
- **Edit guards** (hooks) - Warnings to prevent thread corruption

## Installation

Add to your Claude Code plugins directory or install via `claude plugins add`.

## Usage

```
/inline-dialogue:process [path]
```

Scans for threads and processes all `awaiting_agent` threads until none remain.

## Structure

```
inline-dialogue-plugin/
├── .claude-plugin/plugin.json
├── commands/
│   └── process.md              # /inline-dialogue:process command
├── skills/
│   └── inline-dialogue-workflow/
│       └── SKILL.md            # Behavioral guidance
├── hooks/
│   ├── hooks.json
│   └── pre_tool_use.py         # Thread edit guards
├── src/
│   └── inline_dialogue_mcp/    # MCP server code
├── .mcp.json
└── pyproject.toml
```

## MCP Tools

- `get_threads` - Find all AUTHOR/AGENT threads in a directory
- `respond_to_thread` - Add a response to a thread
- `dismiss_thread` - Remove a completed thread
- `clear_and_commit` - Remove thread and commit file

## Thread Format

Threads are inline code comments:

```swift
// AUTHOR: Why is this method so slow?
// AGENT: The current implementation has O(n²) complexity...
// AUTHOR: Can we optimize it?
// AGENT: Done. Refactored to use a hash map - now O(n).
// AUTHOR:
```

## Development

```bash
uv sync              # Install dependencies
uv run pytest        # Run tests
uv run inline-dialogue-mcp  # Run MCP server directly
```
