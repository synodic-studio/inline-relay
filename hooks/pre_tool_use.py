#!/usr/bin/env python3
"""
Pre-tool-use hook for inline-dialogue thread edit guards.

Detects and warns about edits that could corrupt AUTHOR/AGENT thread structure:
1. Manual addition of // AGENT: or // AUTHOR: lines (should use MCP tools)
2. Destructive edits that remove thread markers (thread history destruction)

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


def is_manual_thread_edit(tool_input: dict) -> bool:
    """
    Detect Edit tool being used to manually add // AGENT: or // AUTHOR: lines.

    These should be added via the inline-dialogue MCP tools, not manually.
    The MCP tools handle:
    - Proper formatting
    - Automatic blank // AUTHOR: line after responses
    - Indentation preservation
    - Duplicate prevention

    Args:
        tool_input: The tool's input parameters

    Returns:
        True if this edit is manually adding thread markers
    """
    new_string = tool_input.get("new_string", "")
    return "// AGENT:" in new_string or "// AUTHOR:" in new_string


def is_thread_destructive_edit(tool_input: dict) -> bool:
    """
    Detect Edit tool destroying thread history by removing markers.

    This catches the "summarization" anti-pattern where agents replace
    a multi-line thread conversation with a condensed version.

    Thread markers are conversation history - they should NEVER be edited
    or removed except via the MCP dismiss_thread tool.

    Args:
        tool_input: The tool's input parameters

    Returns:
        True if this edit removes thread markers (destructive)
    """
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")

    # Count markers in old vs new
    old_author = old_string.count("// AUTHOR:")
    old_agent = old_string.count("// AGENT:")
    new_author = new_string.count("// AUTHOR:")
    new_agent = new_string.count("// AGENT:")

    old_total = old_author + old_agent
    new_total = new_author + new_agent

    # Destructive if old had thread markers and new has fewer
    # This catches both partial removal AND complete deletion
    if old_total > 0 and new_total < old_total:
        return True

    return False


def validate_thread_edit(tool_input: dict) -> dict | None:
    """
    Validate Edit tool operations for thread safety.

    Returns block decision for destructive edits, None otherwise.

    Warns (but allows) manual addition of thread markers.
    BLOCKS edits that remove thread markers.

    Args:
        tool_input: The tool's input parameters

    Returns:
        Block decision dict if destructive, None if allowed
    """
    if is_manual_thread_edit(tool_input):
        emit_warning("=" * 60)
        emit_warning("⚠️  Manual thread edit detected!")
        emit_warning("")
        emit_warning("You're adding // AGENT: or // AUTHOR: lines manually.")
        emit_warning("Prefer the inline-dialogue MCP tool:")
        emit_warning("  respond_to_thread(thread_id, response, path)")
        emit_warning("")
        emit_warning("The MCP tool automatically:")
        emit_warning("  • Adds properly formatted // AGENT: line")
        emit_warning("  • Appends blank // AUTHOR: line for next response")
        emit_warning("  • Preserves thread indentation")
        emit_warning("  • Prevents duplicate responses")
        emit_warning("")
        emit_warning("Allowing operation - use MCP tools next time.")
        emit_warning("=" * 60)

    if is_thread_destructive_edit(tool_input):
        emit_warning("=" * 60)
        emit_warning("🚫 BLOCKED: Thread history destruction!")
        emit_warning("")
        emit_warning("This edit removes // AUTHOR: or // AGENT: lines.")
        emit_warning("Thread markers are READ-ONLY conversation history.")
        emit_warning("")
        emit_warning("NEVER:")
        emit_warning("  • Delete or 'clean up' thread comments")
        emit_warning("  • Summarize or condense thread conversations")
        emit_warning("  • Replace multiple thread lines with fewer")
        emit_warning("  • Edit existing AUTHOR/AGENT text")
        emit_warning("")
        emit_warning("To remove a thread (when user says 'done' or 'reset'):")
        emit_warning("  dismiss_thread(thread_id, path)")
        emit_warning("=" * 60)

        if is_bypass_enabled():
            emit_warning("")
            emit_warning("⚠️  BYPASS ENABLED - allowing destructive edit")
            emit_warning("    (INLINE_DIALOGUE_ALLOW_DESTRUCTIVE=1)")
            return None

        return {
            "decision": "block",
            "reason": "Edit removes AUTHOR/AGENT thread markers. Use dismiss_thread() MCP tool instead."
        }

    return None


def is_write_destructive(tool_input: dict) -> bool:
    """
    Detect Write tool destroying thread history by overwriting file with fewer markers.

    Args:
        tool_input: The tool's input parameters (file_path, content)

    Returns:
        True if this write removes thread markers (destructive)
    """
    file_path = tool_input.get("file_path", "")
    new_content = tool_input.get("content", "")

    if not file_path:
        return False

    # Read existing file content
    try:
        with open(file_path, "r") as f:
            old_content = f.read()
    except (OSError, FileNotFoundError):
        # File doesn't exist yet - can't be destructive
        return False

    # Count markers in old vs new
    old_author = old_content.count("// AUTHOR:")
    old_agent = old_content.count("// AGENT:")
    new_author = new_content.count("// AUTHOR:")
    new_agent = new_content.count("// AGENT:")

    old_total = old_author + old_agent
    new_total = new_author + new_agent

    # Destructive if old had thread markers and new has fewer
    if old_total > 0 and new_total < old_total:
        return True

    return False


def validate_write(tool_input: dict) -> dict | None:
    """
    Validate Write tool operations for thread safety.

    Returns block decision for destructive writes, None otherwise.

    Args:
        tool_input: The tool's input parameters

    Returns:
        Block decision dict if destructive, None if allowed
    """
    if is_write_destructive(tool_input):
        emit_warning("=" * 60)
        emit_warning("🚫 BLOCKED: Thread history destruction via Write!")
        emit_warning("")
        emit_warning("This Write would remove // AUTHOR: or // AGENT: lines.")
        emit_warning("Thread markers are READ-ONLY conversation history.")
        emit_warning("")
        emit_warning("To remove a thread (when user says 'done' or 'reset'):")
        emit_warning("  dismiss_thread(thread_id, path)")
        emit_warning("=" * 60)

        if is_bypass_enabled():
            emit_warning("⚠️  BYPASS ENABLED - allowing destructive write")
            return None

        return {
            "decision": "block",
            "reason": "Write removes AUTHOR/AGENT thread markers. Use dismiss_thread() MCP tool instead."
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
