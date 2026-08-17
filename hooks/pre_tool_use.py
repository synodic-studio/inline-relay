#!/usr/bin/env python3
"""
Pre-tool-use hook for inline-relay thread edit guards.

Detects and DENIES edits that could corrupt AUTHOR/AGENT thread structure:
1. Any Edit that touches thread markers (in old_string or new_string)
2. Any Write to a file that contains thread markers

Thread markers are READ-ONLY to the general-purpose edit tools. Every marker
change routes through the `inline-relay` CLI instead.

Protocol: a denial is exit 0 plus a `hookSpecificOutput.permissionDecision`
of "deny" on stdout. A non-zero exit is a *non-blocking* hook error to Claude
Code, so exiting non-zero here would silently let the edit through.

ESCAPE HATCH: Set environment variable INLINE_RELAY_ALLOW_DESTRUCTIVE=1
to bypass the guard (for emergencies when the CLI is broken).
"""

# The hook runs under whatever `python3` the machine resolves, which is often
# the 3.9 that ships with macOS. Without this, the `X | None` annotations below
# raise at import time, the hook exits non-zero, and Claude Code reads that as a
# hook error and lets the edit through -- the guard fails open, silently.
from __future__ import annotations

import json
import os
import sys


def is_bypass_enabled() -> bool:
    """Check if destructive edit bypass is enabled via environment variable."""
    return os.environ.get("INLINE_RELAY_ALLOW_DESTRUCTIVE", "").strip() == "1"


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


# The CLI is the only supported path for changing markers. Listed in the denial
# so the reason doubles as the instruction for what to do instead.
_CLI_ALTERNATIVES = (
    "  inline-relay respond --id ID --path PATH   (answer a thread)\n"
    "  inline-relay dismiss --id ID --path PATH   (close a resolved thread)\n"
    "  inline-relay clear-commit --file FILE      (strip markers and commit)"
)


def deny(reason: str) -> dict:
    """Build the PreToolUse payload that denies a tool call."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


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

    Thread markers are READ-ONLY. Any edit that involves them is denied.
    The `inline-relay` CLI is the only path that may change them.

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

    DENIES any edit that touches thread markers (in old_string or new_string).
    Thread markers are completely read-only via Edit tool.

    Args:
        tool_input: The tool's input parameters

    Returns:
        Deny payload if the edit touches threads, None if allowed
    """
    # Allow edits within the plugin itself (for development)
    file_path = tool_input.get("file_path", "")
    if is_within_plugin(file_path):
        return None

    if not edit_touches_thread_markers(tool_input):
        return None

    if is_bypass_enabled():
        emit_warning("inline-relay: marker edit allowed by INLINE_RELAY_ALLOW_DESTRUCTIVE=1")
        return None

    return deny(
        "DENIED by inline-relay: this Edit touches AUTHOR/AGENT thread markers, "
        "which are read-only to the Edit tool.\n"
        "Route the change through the CLI instead:\n" + _CLI_ALTERNATIVES
    )


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

    DENIES any write to a file that currently contains thread markers.
    Thread files are completely read-only via Write tool.

    Args:
        tool_input: The tool's input parameters

    Returns:
        Deny payload if the file has threads, None if allowed
    """
    file_path = tool_input.get("file_path", "")

    # Allow writes within the plugin itself (for development)
    if is_within_plugin(file_path):
        return None

    if not file_has_thread_markers(file_path):
        return None

    if is_bypass_enabled():
        emit_warning("inline-relay: marker overwrite allowed by INLINE_RELAY_ALLOW_DESTRUCTIVE=1")
        return None

    return deny(
        "DENIED by inline-relay: this file holds an open AUTHOR/AGENT review thread, "
        "so a whole-file Write would silently destroy it.\n"
        "Route the change through the CLI instead:\n" + _CLI_ALTERNATIVES
    )


def evaluate(hook_input: dict) -> dict | None:
    """Return the deny payload for a hook input, or None to let the tool run."""
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    if tool_name == "Edit":
        return validate_thread_edit(tool_input)
    if tool_name == "Write":
        return validate_write(tool_input)
    return None


def main():
    """Process pre-tool-use hook for thread edit guards."""
    hook_input = json.load(sys.stdin)
    decision = evaluate(hook_input)

    if decision:
        print(json.dumps(decision, indent=2))

    # Always exit 0: Claude Code only reads this JSON on a zero exit. A non-zero
    # exit is treated as a hook error and the edit proceeds.
    return 0


if __name__ == "__main__":
    sys.exit(main())
