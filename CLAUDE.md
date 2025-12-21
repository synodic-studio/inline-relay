# CLAUDE.md

Project-specific guidance for Claude Code working on inline-dialogue-plugin.

## Project Overview

MCP plugin enabling AUTHOR/AGENT inline code review threads embedded in source code. Python-based with pytest testing, FastMCP server architecture.

## Commands

```bash
uv run pytest                    # Run all tests (80 tests)
uv run pytest -x                 # Stop on first failure
uv run pytest -k "test_name"     # Run specific test
uv run python -m inline_dialogue_mcp.server  # Run MCP server
```

## Architecture

- `inline_dialogue_mcp/core.py` - Thread detection, normalization, ID computation
- `inline_dialogue_mcp/server.py` - MCP tool implementations
- `hooks/pre_tool_use.py` - Edit guards protecting thread markers
- `tests/test_server.py` - All tests (organized by class)
- `skills/inline-dialogue-workflow/SKILL.md` - Behavioral guidance for thread processing

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
- Use `mock_ctx` fixture for MCP context

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

- **Never use Write tool** on files containing thread markers (use Edit)
- Thread markers (`// AUTHOR:`, `// AGENT:`) are protected by hooks
- Use MCP tools (`respond_to_thread`, `dismiss_thread`) for thread operations
