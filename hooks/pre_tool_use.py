#!/usr/bin/env python3
"""
Pre-tool-use hook for inline-relay thread edit guards.

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


def get_plugin_root() -> str | None:
    """Get the root directory of this plugin (parent of hooks/)."""
    try:
        hook_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.dirname(hook_dir)
    except Exception:
        return None


def is_inline_relay_dev_directory(directory: str) -> bool:
    """Check if a directory is an inline-relay plugin development directory.
    
    Identifies by checking for .claude-plugin/plugin.json with name containing
    "inline-relay".
    """
    if not directory or not os.path.isdir(directory):
        return False
    
    try:
        plugin_json = os.path.join(directory, ".claude-plugin", "plugin.json")
        if os.path.exists(plugin_json):
            with open(plugin_json, "r") as f:
                import json
                data = json.load(f)
                if "inline-relay" in data.get("name", "").lower():
                    return True
        return False
    except Exception:
        return False


def is_within_plugin(file_path: str) -> bool:
    """Check if file_path is within the plugin directory itself.

    When developing the plugin, we want to allow edits to plugin files
    (like SKILL.md with example markers) without blocking.
    
    Allows edits when:
    1. File is within the installed plugin (where this hook runs from), OR
    2. CWD is an inline-relay dev directory AND file is within CWD
    """
    if not file_path:
        return False
    
    try:
        abs_file = os.path.abspath(file_path)
        
        # Check if within the installed plugin (where this hook runs from)
        plugin_root = get_plugin_root()
        if plugin_root and abs_file.startswith(plugin_root + os.sep):
            return True
        
        # Check if CWD is an inline-relay dev directory and file is within it
        cwd = os.getcwd()
        if is_inline_relay_dev_directory(cwd):
            abs_cwd = os.path.abspath(cwd)
            if abs_file.startswith(abs_cwd + os.sep):
                return True
        
        return False
    except Exception:
        return False


def emit_warning(message: str) -> None:
    """Emit a warning message to stderr."""
    print(message, file=sys.stderr)


# All recognized thread marker prefixes (constructed to avoid literal markers in source)
_COMMENT_PREFIXES = ["//", "#", "--"]
_MARKER_ROLES = ["AUTHOR:", "AGENT:"]
_MARKER_PATTERNS = [f"{p} {r}" for p in _COMMENT_PREFIXES for r in _MARKER_ROLES]


def _contains_thread_markers(text: str) -> bool:
    """Check if text contains any thread marker pattern."""
    return any(pattern in text for pattern in _MARKER_PATTERNS)


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

    return _contains_thread_markers(old_string) or _contains_thread_markers(new_string)


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
    # Allow edits within the plugin itself (for development)
    file_path = tool_input.get("file_path", "")
    if is_within_plugin(file_path):
        return None

    if edit_touches_thread_markers(tool_input):
        emit_warning("=" * 60)
        emit_warning("🚫 BLOCKED: Edit touches thread markers!")
        emit_warning("")
        emit_warning("Thread markers (// or # or -- AUTHOR:/AGENT:) are READ-ONLY.")
        emit_warning("You cannot add, edit, or remove them via the Edit tool.")
        emit_warning("")
        emit_warning("Use the inline-relay MCP tools instead:")
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

    return _contains_thread_markers(content)


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

    # Allow writes within the plugin itself (for development)
    if is_within_plugin(file_path):
        return None

    if file_has_thread_markers(file_path):
        emit_warning("=" * 60)
        emit_warning("🚫 BLOCKED: Write to file with thread markers!")
        emit_warning("")
        emit_warning("This file contains AUTHOR:/AGENT: thread markers.")
        emit_warning("Files with thread markers are READ-ONLY via Write tool.")
        emit_warning("")
        emit_warning("Use the inline-relay MCP tools instead:")
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
