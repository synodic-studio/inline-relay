# CLAUDE.md

Project-specific guidance for Claude Code working on inline-relay.

## Project Overview

Claude Code plugin enabling AUTHOR/AGENT inline code review threads embedded in source code. Zero-dependency Python (stdlib only) with pytest testing. Thread operations run through a deterministic CLI driven by a skill — no server process.

## Commands

```bash
uv run pytest                    # Run all tests
uv run pytest -x                 # Stop on first failure
uv run pytest -k "test_name"     # Run specific test
uv run inline-relay get-threads .   # Run the CLI (subcommands: get-threads,
                                    # respond, dismiss, clear-commit, process-all)
./scripts/demo.sh --auto         # End-to-end smoke test in a throwaway /tmp repo
./scripts/demo.sh --cleanup      # Remove what the demo created
```

## Architecture

- `src/inline_relay/core.py` - Thread detection, normalization, ID computation, SQLite event log
- `src/inline_relay/actions.py` - Thread operations (get/respond/dismiss/clear-commit/process-all) as plain functions
- `src/inline_relay/cli.py` + `__main__.py` - argparse CLI wrapping `actions`; prints JSON, exits non-zero on failure
- `hooks/pre_tool_use.py` - PreToolUse guard denying Edit/Write on thread markers
- `hooks/hooks.json` - Hook registration (PreToolUse on `Edit|Write`, Stop)
- `scripts/demo.sh` - Live walkthrough of the whole loop; doubles as a smoke test
- `tests/test_server.py` - All tests (organized by class)
- `commands/process.md` - `/inline-relay:process` entry point
- `skills/inline-relay-workflow/SKILL.md` - Behavioral guidance + CLI invocation

## Critical: Verify Before Claiming

**Never claim work was done without verification.** This applies to ALL work, not just thread responses.

### The Problem

Wrong:
> "I wrote a test for that."
> [User asks: "Which test?"]
> "Actually, I didn't write a test. Would you like me to?"

This is unacceptable. It erodes trust and wastes time.

### The Rule

Before saying you did something:
1. **Actually do it** - Write the code, run the command, create the file
2. **Verify it exists** - Check the file is there, test passes, changes are saved
3. **Be specific** - Name the file, test class, function, line number

Right:
> "Added `TestActionCommandDetection.test_commit_with_message` in `tests/test_server.py:847`"

Wrong:
> "I added a test for that functionality."

### Proactive Test Writing

When making code changes to this project:

1. **New functionality** - Write tests alongside the implementation, not after
2. **Bug fixes** - Add a regression test that would have caught the bug
3. **Refactoring** - Verify existing tests still pass before claiming completion

Tests go in `tests/test_server.py`, following existing class organization:
- `TestFeatureName` - Group related tests
- `test_specific_behavior` - Descriptive test method names
- Use `tmp_path` fixture for file operations

### Completion Checklist

Before saying work is "done":

- [ ] Code changes are saved (not just described)
- [ ] `uv run pytest` passes (or you've explicitly noted failures)
- [ ] New functionality has tests (or you've explicitly noted why not)
- [ ] You can point to specific files/lines/functions

## Testing Patterns

```python
class TestNewFeature:
    """Tests for new feature."""

    def test_basic_behavior(self, tmp_path):
        """Describe what this tests."""
        # Arrange
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: question\n")

        # Act
        result = function_under_test(test_file)

        # Assert
        assert result["expected_key"] == "expected_value"

    def test_edge_case(self, tmp_path):
        """Describe the edge case."""
        ...
```

## File Safety

- Thread markers (`// AUTHOR:`, `// AGENT:`) are read-only to Edit and Write. The `PreToolUse` hook in `hooks/hooks.json` denies both.
- Use the `inline-relay` CLI (`respond`, `dismiss`, `clear-commit`, `process-all`) for thread operations
- The guard denies with exit 0 plus `hookSpecificOutput.permissionDecision: "deny"`. A non-zero exit reads as a hook error to Claude Code and the edit goes through, so the exit code is load-bearing.
- `INLINE_RELAY_ALLOW_DESTRUCTIVE=1` bypasses the guard
- Edits to files inside the plugin repo itself are exempt, so its own docs and tests can hold example markers
