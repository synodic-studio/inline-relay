#!/usr/bin/env python3
"""
Pre-tool-use hook for inline-dialogue thread edit guards.

Detects and warns about edits that could corrupt AUTHOR/AGENT thread structure:
1. Manual addition of // AGENT: or // AUTHOR: lines (should use MCP tools)
2. Destructive edits that remove thread markers (thread history destruction)

These are warnings rather than blocks to allow manual intervention
if the MCP server is broken or misconfigured.
"""

import json
import sys


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

    # Destructive if:
    # 1. Old had thread markers
    # 2. New has fewer markers (not just removing all)
    # 3. New still has some markers (replacement, not deletion)
    if old_total > 0 and new_total < old_total and new_total > 0:
        return True

    return False


def validate_thread_edit(tool_input: dict) -> None:
    """
    Validate Edit tool operations for thread safety.

    Emits warnings (not blocks) for:
    - Manual addition of // AGENT: or // AUTHOR: lines
    - Destructive edits that remove thread markers

    Args:
        tool_input: The tool's input parameters
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
        emit_warning("⚠️  Thread history destruction detected!")
        emit_warning("")
        emit_warning("This edit removes // AUTHOR: or // AGENT: lines.")
        emit_warning("Thread markers are READ-ONLY conversation history.")
        emit_warning("")
        emit_warning("NEVER:")
        emit_warning("  • Summarize or condense thread conversations")
        emit_warning("  • Replace multiple thread lines with fewer")
        emit_warning("  • Edit existing AUTHOR/AGENT text")
        emit_warning("")
        emit_warning("To remove a thread (when user says 'done' or 'reset'):")
        emit_warning("  dismiss_thread(thread_id, path)")
        emit_warning("")
        emit_warning("Allowing operation - this may destroy conversation history!")
        emit_warning("=" * 60)


def main():
    """Process pre-tool-use hook for thread edit guards."""
    # Read hook input from stdin
    hook_input = json.load(sys.stdin)

    tool_input = hook_input.get("tool_input", {})

    # Validate thread edits (emits warnings if issues found)
    validate_thread_edit(tool_input)

    # Always approve - warnings are informational only
    result = {"decision": "approve"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
