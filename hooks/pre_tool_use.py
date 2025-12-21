#!/usr/bin/env python3
"""
Pre-tool-use hook for inline-dialogue thread edit guards.

Detects and BLOCKS edits that could corrupt AUTHOR/AGENT thread structure:
1. Any Edit that touches thread markers (in old_string or new_string)
2. Any Write to a file that contains thread markers

Thread markers are READ-ONLY. Use MCP tools to modify them.

ESCAPE HATCH: Set environment variable INLINE_DIALOGUE_ALLOW_DESTRUCTIVE=1
to bypass blocking (for emergencies when MCP is broken).
"""

import json
import os
import sys


def is_bypass_enabled() -> bool:
    """Check if destructive edit bypass is enabled via environment variable."""
    return os.environ.get("INLINE_DIALOGUE_ALLOW_DESTRUCTIVE", "").strip() == "1"


def emit_warning(message: str) -> None:
    """Emit a warning message to stderr."""
    print(message, file=sys.stderr)


def edit_touches_thread_markers(tool_input: dict) -> bool:
    """
    Detect Edit tool touching ANY thread markers (old or new string).

    Thread markers are READ-ONLY. Any edit that involves them should be blocked.
    Use MCP tools (respond_to_thread, dismiss_thread) instead.

    Args:
        tool_input: The tool's input parameters

    Returns:
        True if this edit involves thread markers in any way
    """
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")

    has_markers_old = "// AUTHOR:" in old_string or "// AGENT:" in old_string
    has_markers_new = "// AUTHOR:" in new_string or "// AGENT:" in new_string

    return has_markers_old or has_markers_new


def validate_thread_edit(tool_input: dict) -> dict | None:
    """
    Validate Edit tool operations for thread safety.

    BLOCKS any edit that touches thread markers (in old_string or new_string).
    Thread markers are completely read-only via Edit tool.

    Args:
        tool_input: The tool's input parameters

    Returns:
        Block decision dict if touches threads, None if allowed
    """
    if edit_touches_thread_markers(tool_input):
        emit_warning("=" * 60)
        emit_warning("🚫 BLOCKED: Edit touches thread markers!")
        emit_warning("")
        emit_warning("Thread markers (// AUTHOR: and // AGENT:) are READ-ONLY.")
        emit_warning("You cannot add, edit, or remove them via the Edit tool.")
        emit_warning("")
        emit_warning("Use the inline-dialogue MCP tools instead:")
        emit_warning("  • respond_to_thread(thread_id, response, path)")
        emit_warning("  • dismiss_thread(thread_id, path)")
        emit_warning("  • clear_and_commit(file, message)")
        emit_warning("=" * 60)

        if is_bypass_enabled():
            emit_warning("")
            emit_warning("⚠️  BYPASS ENABLED - allowing edit")
            emit_warning("    (INLINE_DIALOGUE_ALLOW_DESTRUCTIVE=1)")
            return None

        return {
            "decision": "block",
            "reason": "Edit touches AUTHOR/AGENT thread markers. Use MCP tools (respond_to_thread, dismiss_thread) instead."
        }

    return None


def file_has_thread_markers(file_path: str) -> bool:
    """
    Check if an existing file contains any thread markers.

    Args:
        file_path: Path to the file to check

    Returns:
        True if file exists and contains thread markers
    """
    if not file_path:
        return False

    try:
        with open(file_path, "r") as f:
            content = f.read()
    except (OSError, FileNotFoundError):
        # File doesn't exist yet - no markers to protect
        return False

    return "// AUTHOR:" in content or "// AGENT:" in content


def validate_write(tool_input: dict) -> dict | None:
    """
    Validate Write tool operations for thread safety.

    BLOCKS any write to a file that currently contains thread markers.
    Thread files are completely read-only via Write tool.

    Args:
        tool_input: The tool's input parameters

    Returns:
        Block decision dict if file has threads, None if allowed
    """
    file_path = tool_input.get("file_path", "")

    if file_has_thread_markers(file_path):
        emit_warning("=" * 60)
        emit_warning("🚫 BLOCKED: Write to file with thread markers!")
        emit_warning("")
        emit_warning("This file contains // AUTHOR: or // AGENT: markers.")
        emit_warning("Files with thread markers are READ-ONLY via Write tool.")
        emit_warning("")
        emit_warning("Use the inline-dialogue MCP tools instead:")
        emit_warning("  • respond_to_thread(thread_id, response, path)")
        emit_warning("  • dismiss_thread(thread_id, path)")
        emit_warning("  • clear_and_commit(file, message)")
        emit_warning("=" * 60)

        if is_bypass_enabled():
            emit_warning("")
            emit_warning("⚠️  BYPASS ENABLED - allowing write")
            emit_warning("    (INLINE_DIALOGUE_ALLOW_DESTRUCTIVE=1)")
            return None

        return {
            "decision": "block",
            "reason": "File contains AUTHOR/AGENT thread markers. Use MCP tools (respond_to_thread, dismiss_thread) instead."
        }

    return None


def main():
    """Process pre-tool-use hook for thread edit guards."""
    # Read hook input from stdin
    hook_input = json.load(sys.stdin)

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    block_result = None

    if tool_name == "Edit":
        # Validate Edit tool operations
        block_result = validate_thread_edit(tool_input)
    elif tool_name == "Write":
        # Validate Write tool operations
        block_result = validate_write(tool_input)

    if block_result:
        # Destructive operation detected - block it
        print(json.dumps(block_result))
        return 1

    # Allow the operation
    print(json.dumps({"decision": "approve"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
