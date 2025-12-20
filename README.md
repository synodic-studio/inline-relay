# inline-dialogue-plugin

Claude Code plugin for AUTHOR/AGENT inline code review threads.

## Overview

This plugin provides:
- **MCP server** with tools for scanning, reading, and responding to inline dialogue threads
- **Edit guards** (hooks) to prevent corruption of thread structure

## Installation

Add to your Claude Code plugins directory or install via `claude plugins add`.

## Structure

```
inline-dialogue-plugin/
├── .claude-plugin/
│   └── plugin.json           # Plugin manifest
├── src/
│   └── inline_dialogue_mcp/  # MCP server code
├── hooks/
│   ├── hooks.json            # Hook configuration
│   └── pre_tool_use.py       # Thread edit guards
├── .mcp.json                  # MCP server config
└── pyproject.toml             # Python package definition
```

## MCP Tools

- `scan_for_threads` - Find all AUTHOR/AGENT threads in a directory
- `get_thread` - Read a specific thread's content and history
- `respond_to_thread` - Add a new response to an existing thread

## Thread Format

Threads are inline code comments with this structure:

```swift
// AUTHOR: Why is this method so slow?
// AGENT: The current implementation has O(n²) complexity...
// AUTHOR: Can we optimize it?
// AGENT[action_required]: Yes, I'll refactor to use a hash map.
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run MCP server directly
uv run inline-dialogue-mcp
```
