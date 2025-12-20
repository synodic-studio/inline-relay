"""Tests for inline-dialogue MCP server thread handling."""

from pathlib import Path

import pytest

from inline_dialogue_mcp.server import (
    INLINE_AUTHOR_PATTERN,
    SALT_PATTERN,
    SLASH_COMMENT_EXTENSIONS,
    compute_thread_id,
    detect_action_command,
    find_all_threads,
    find_git_root,
    find_thread_by_id,
    find_thread_location,
    find_threads_in_file,
    generate_salt,
    get_threads,
    normalize_all_inline_comments,
    normalize_inline_comments,
    respond_to_thread,
    salt_duplicate_threads,
    uses_slash_comments,
)

# Access underlying functions from FunctionTool wrappers
_get_threads = get_threads.fn
_respond_to_thread = respond_to_thread.fn


class TestComputeThreadId:
    """Tests for thread ID computation."""

    def test_stable_id_same_inputs(self):
        """Same file and text produce same ID."""
        id1 = compute_thread_id("/path/file.py", "Fix this bug")
        id2 = compute_thread_id("/path/file.py", "Fix this bug")
        assert id1 == id2

    def test_different_text_different_id(self):
        """Different text produces different ID."""
        id1 = compute_thread_id("/path/file.py", "Fix this bug")
        id2 = compute_thread_id("/path/file.py", "Different text")
        assert id1 != id2

    def test_different_file_different_id(self):
        """Different file produces different ID."""
        id1 = compute_thread_id("/path/file1.py", "Fix this bug")
        id2 = compute_thread_id("/path/file2.py", "Fix this bug")
        assert id1 != id2

    def test_id_length(self):
        """ID is 8 characters."""
        thread_id = compute_thread_id("/path/file.py", "Some text")
        assert len(thread_id) == 8
        assert all(c in "0123456789abcdef" for c in thread_id)


class TestFindThreadsInFile:
    """Tests for finding threads in a single file."""

    def test_single_author_thread(self, tmp_path):
        """Detects a single AUTHOR comment."""
        test_file = tmp_path / "test.py"
        test_file.write_text("# code\n// AUTHOR: Fix this bug\ndef foo(): pass\n")

        threads = find_threads_in_file(test_file)

        assert len(threads) == 1
        assert threads[0]["thread"][0]["text"] == "Fix this bug"
        assert threads[0]["status"] == "awaiting_agent"

    def test_author_agent_thread(self, tmp_path):
        """Detects AUTHOR/AGENT conversation."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "# code\n"
            "// AUTHOR: What does this do?\n"
            "// AGENT: It processes data\n"
            "def foo(): pass\n"
        )

        threads = find_threads_in_file(test_file)

        assert len(threads) == 1
        assert len(threads[0]["thread"]) == 2
        assert threads[0]["thread"][0]["role"] == "author"
        assert threads[0]["thread"][1]["role"] == "agent"

    def test_awaiting_user_status(self, tmp_path):
        """Thread ending with empty AUTHOR has awaiting_user status."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Question?\n"
            "// AGENT: Answer\n"
            "// AUTHOR: \n"
            "def foo(): pass\n"
        )

        threads = find_threads_in_file(test_file)

        assert len(threads) == 1
        assert threads[0]["status"] == "awaiting_author"

    def test_multiple_threads(self, tmp_path):
        """Detects multiple separate threads."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: First thread\n"
            "def foo(): pass\n"
            "// AUTHOR: Second thread\n"
            "def bar(): pass\n"
        )

        threads = find_threads_in_file(test_file)

        assert len(threads) == 2
        assert threads[0]["thread"][0]["text"] == "First thread"
        assert threads[1]["thread"][0]["text"] == "Second thread"

    def test_no_threads(self, tmp_path):
        """Returns empty list for file with no threads."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass\ndef bar(): pass\n")

        threads = find_threads_in_file(test_file)

        assert threads == []


class TestSaltGeneration:
    """Tests for salt generation and pattern matching."""

    def test_generate_salt_length(self):
        """Salt is 4 characters by default."""
        salt = generate_salt()
        assert len(salt) == 4

    def test_generate_salt_custom_length(self):
        """Salt length can be customized."""
        salt = generate_salt(8)
        assert len(salt) == 8

    def test_generate_salt_alphanumeric(self):
        """Salt contains only lowercase letters and digits."""
        salt = generate_salt(100)
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789" for c in salt)

    def test_salt_pattern_matches(self):
        """SALT_PATTERN matches valid salt suffix."""
        assert SALT_PATTERN.search("Fix this [salt:ab12]")
        assert SALT_PATTERN.search("[salt:zzzz]")
        assert not SALT_PATTERN.search("Fix this")
        assert not SALT_PATTERN.search("[salt:AB12]")  # uppercase not allowed


class TestDuplicateDetection:
    """Tests for duplicate thread detection (before salting)."""

    def test_find_all_threads_detects_duplicates(self, tmp_path):
        """find_all_threads returns warnings for duplicates."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Fix this\n"
            "def foo(): pass\n"
            "// AUTHOR: Fix this\n"
            "def bar(): pass\n"
        )

        threads, warnings = find_all_threads(test_file)

        assert len(threads) == 2
        assert len(warnings) == 1
        assert warnings[0]["type"] == "duplicate_thread_id"

    def test_no_duplicate_across_files(self, tmp_path):
        """Different files with same text have different IDs (no warning)."""
        file1 = tmp_path / "file1.py"
        file2 = tmp_path / "file2.py"
        file1.write_text("// AUTHOR: Fix this\ndef foo(): pass\n")
        file2.write_text("// AUTHOR: Fix this\ndef bar(): pass\n")

        threads, warnings = find_all_threads(tmp_path)

        assert len(threads) == 2
        assert threads[0]["id"] != threads[1]["id"]
        assert len(warnings) == 0

    def test_no_duplicate_different_text(self, tmp_path):
        """No warning for different AUTHOR text."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: First issue\n"
            "def foo(): pass\n"
            "// AUTHOR: Second issue\n"
            "def bar(): pass\n"
        )

        threads, warnings = find_all_threads(test_file)

        assert len(threads) == 2
        assert len(warnings) == 0


class TestAutoSalting:
    """Tests for automatic salt addition to duplicates."""

    def test_salt_duplicate_threads_modifies_file(self, tmp_path):
        """salt_duplicate_threads adds salt to duplicate AUTHOR lines."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Fix this\n"
            "def foo(): pass\n"
            "// AUTHOR: Fix this\n"
            "def bar(): pass\n"
        )

        _, warnings = find_all_threads(test_file)
        modified = salt_duplicate_threads(warnings)

        assert len(modified) == 1
        content = test_file.read_text()
        assert "[salt:" in content
        # First occurrence unchanged, second has salt
        lines = content.splitlines()
        assert lines[0] == "// AUTHOR: Fix this"
        assert lines[2].startswith("// AUTHOR: Fix this [salt:")

    def test_salted_threads_have_unique_ids(self, tmp_path):
        """After salting, threads have unique IDs."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Fix this\n"
            "def foo(): pass\n"
            "// AUTHOR: Fix this\n"
            "def bar(): pass\n"
        )

        _, warnings = find_all_threads(test_file)
        salt_duplicate_threads(warnings)

        # Re-scan after salting
        threads, new_warnings = find_all_threads(test_file)

        assert len(threads) == 2
        assert threads[0]["id"] != threads[1]["id"]
        assert len(new_warnings) == 0

    def test_get_threads_auto_salts_duplicates(self, tmp_path):
        """get_threads automatically salts duplicates and reports modified files."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Fix this\n"
            "def foo(): pass\n"
            "// AUTHOR: Fix this\n"
            "def bar(): pass\n"
        )

        result = _get_threads(str(test_file))

        # Should have salted_files, not warnings
        assert "salted_files" in result
        assert len(result["salted_files"]) == 1
        assert "warnings" not in result

        # Threads should now have unique IDs
        assert len(result["threads"]) == 2
        assert result["threads"][0]["id"] != result["threads"][1]["id"]

    def test_get_threads_no_salting_when_unique(self, tmp_path):
        """get_threads doesn't modify file when threads are unique."""
        test_file = tmp_path / "test.py"
        original_content = "// AUTHOR: Unique text\ndef foo(): pass\n"
        test_file.write_text(original_content)

        result = _get_threads(str(test_file))

        assert "salted_files" not in result
        assert "warnings" not in result
        assert test_file.read_text() == original_content

    def test_already_salted_not_double_salted(self, tmp_path):
        """Threads with existing salt are not salted again."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Fix this\n"
            "def foo(): pass\n"
            "// AUTHOR: Fix this [salt:abc1]\n"
            "def bar(): pass\n"
        )

        _, warnings = find_all_threads(test_file)
        # No warnings because texts are different (one has salt)
        assert len(warnings) == 0


class TestLineDrift:
    """Tests for thread operations surviving line number changes."""

    def test_reply_after_lines_added_above(self, tmp_path):
        """Reply succeeds after lines inserted above thread."""
        test_file = tmp_path / "test.py"
        original_content = (
            "def foo():\n"
            "    pass\n"
            "// AUTHOR: What about this?\n"
            "def bar():\n"
            "    pass\n"
        )
        test_file.write_text(original_content)

        # Get thread ID before modification
        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]
        original_line = threads[0]["start_line"]
        assert original_line == 3

        # Add lines above the thread
        modified_content = (
            "import os\n"
            "import sys\n"
            "import pathlib\n"
            "\n"
            "def foo():\n"
            "    pass\n"
            "// AUTHOR: What about this?\n"
            "def bar():\n"
            "    pass\n"
        )
        test_file.write_text(modified_content)

        # Thread should now be at a different line
        threads_after, _ = find_all_threads(test_file)
        new_line = threads_after[0]["start_line"]
        assert new_line == 7  # Line shifted due to additions
        assert threads_after[0]["id"] == thread_id  # Same ID

        # Reply should still work via content-based location
        result = _respond_to_thread(thread_id, "This looks fine", str(test_file))

        assert result["success"] is True

        # Verify response was inserted correctly
        final_content = test_file.read_text()
        assert "// AGENT: This looks fine" in final_content
        assert "// AUTHOR: " in final_content  # Blank author line added

    def test_reply_after_lines_removed_above(self, tmp_path):
        """Reply succeeds after lines removed above thread."""
        test_file = tmp_path / "test.py"
        original_content = (
            "import os\n"
            "import sys\n"
            "import pathlib\n"
            "\n"
            "def foo():\n"
            "    pass\n"
            "// AUTHOR: What about this?\n"
            "def bar():\n"
            "    pass\n"
        )
        test_file.write_text(original_content)

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]
        assert threads[0]["start_line"] == 7

        # Remove lines above the thread
        modified_content = (
            "def foo():\n"
            "    pass\n"
            "// AUTHOR: What about this?\n"
            "def bar():\n"
            "    pass\n"
        )
        test_file.write_text(modified_content)

        # Thread should now be at a different line
        threads_after, _ = find_all_threads(test_file)
        assert threads_after[0]["start_line"] == 3  # Line shifted

        # Reply should still work
        result = _respond_to_thread(thread_id, "Still works", str(test_file))
        assert result["success"] is True

    def test_thread_id_stable_across_modifications(self, tmp_path):
        """Thread ID remains stable when lines shift."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Check this\ndef foo(): pass\n")

        threads1, _ = find_all_threads(test_file)
        id1 = threads1[0]["id"]

        # Add content above
        test_file.write_text(
            "import os\nimport sys\n// AUTHOR: Check this\ndef foo(): pass\n"
        )

        threads2, _ = find_all_threads(test_file)
        id2 = threads2[0]["id"]

        assert id1 == id2


class TestFindThreadLocation:
    """Tests for content-based thread location."""

    def test_finds_thread_by_content(self, tmp_path):
        """Locates thread by first AUTHOR text, not cached line number."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "import os\n"
            "// AUTHOR: Review this\n"
            "def foo(): pass\n"
        )

        line = find_thread_location(test_file, "Review this")

        assert line == 2

    def test_returns_none_for_missing_thread(self, tmp_path):
        """Returns None when thread text not found."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass\n")

        line = find_thread_location(test_file, "Not in file")

        assert line is None


class TestRespondToThread:
    """Tests for adding responses to threads."""

    def test_basic_response(self, tmp_path):
        """Adds AGENT response and blank AUTHOR line."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Question?\ndef foo(): pass\n")

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        result = _respond_to_thread(thread_id, "Answer!", str(test_file))

        assert result["success"] is True
        content = test_file.read_text()
        lines = content.splitlines()
        assert lines[0] == "// AUTHOR: Question?"
        assert lines[1] == "// AGENT: Answer!"
        assert lines[2] == "// AUTHOR: "
        assert lines[3] == "def foo(): pass"

    def test_response_preserves_indentation(self, tmp_path):
        """Response preserves thread's existing indentation."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("func foo() {\n    // AUTHOR: Question?\n}\n")

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        result = _respond_to_thread(thread_id, "Answer!", str(test_file))

        assert result["success"] is True
        content = test_file.read_text()
        lines = content.splitlines()
        assert lines[1] == "    // AUTHOR: Question?"
        assert lines[2] == "    // AGENT: Answer!"
        assert lines[3] == "    // AUTHOR: "

    def test_response_to_nonexistent_thread(self, tmp_path):
        """Returns error for missing thread ID."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass\n")

        result = _respond_to_thread("nonexistent", "Response", str(test_file))

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_blocks_identical_duplicate_response(self, tmp_path):
        """Blocks identical duplicate response."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Question?\n"
            "// AGENT: First answer\n"
            "// AUTHOR: \n"
            "def foo(): pass\n"
        )

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        # Attempting identical response should fail
        result = _respond_to_thread(thread_id, "First answer", str(test_file))

        assert result["success"] is False
        assert "Duplicate response blocked" in result["error"]

        # File should be unchanged
        content = test_file.read_text()
        assert content.count("// AGENT:") == 1

    def test_warns_on_different_additional_response(self, tmp_path):
        """Allows different response but warns when thread awaiting user."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Question?\n"
            "// AGENT: First answer\n"
            "// AUTHOR: \n"
            "def foo(): pass\n"
        )

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        # Different response should succeed with warning
        result = _respond_to_thread(thread_id, "Additional clarification", str(test_file))

        assert result["success"] is True
        assert "warning" in result
        assert "additional response" in result["warning"].lower()

        # File should have both responses
        content = test_file.read_text()
        assert content.count("// AGENT:") == 2
        assert "First answer" in content
        assert "Additional clarification" in content

    def test_response_preserves_multiline_thread(self, tmp_path):
        """Response appends to existing multi-line thread."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: First question\n"
            "// AGENT: First answer\n"
            "// AUTHOR: Follow up\n"
            "def foo(): pass\n"
        )

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        result = _respond_to_thread(thread_id, "Follow up answer", str(test_file))

        assert result["success"] is True
        content = test_file.read_text()
        assert "// AUTHOR: Follow up" in content
        assert "// AGENT: Follow up answer" in content


class TestFindThreadById:
    """Tests for finding threads by ID."""

    def test_finds_existing_thread(self, tmp_path):
        """Locates thread by ID."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Test thread\ndef foo(): pass\n")

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        found = find_thread_by_id(test_file, thread_id)

        assert found is not None
        assert found["id"] == thread_id

    def test_returns_none_for_missing(self, tmp_path):
        """Returns None for nonexistent ID."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Test thread\ndef foo(): pass\n")

        found = find_thread_by_id(test_file, "nonexistent")

        assert found is None


class TestInlineAuthorPattern:
    """Tests for inline AUTHOR comment pattern matching."""

    def test_matches_inline_comment(self):
        """Pattern matches code followed by AUTHOR comment."""
        match = INLINE_AUTHOR_PATTERN.match("let x = 5 // AUTHOR: Is this correct?")
        assert match is not None
        assert match.group(1) == ""  # no indent
        assert match.group(2) == "let x = 5 "
        assert match.group(3) == "Is this correct?"

    def test_matches_with_indent(self):
        """Pattern captures leading indentation."""
        match = INLINE_AUTHOR_PATTERN.match("    code() // AUTHOR: question")
        assert match is not None
        assert match.group(1) == "    "
        assert match.group(2) == "code() "
        assert match.group(3) == "question"

    def test_no_match_standalone_author(self):
        """Pattern does NOT match standalone AUTHOR comment."""
        match = INLINE_AUTHOR_PATTERN.match("// AUTHOR: standalone comment")
        assert match is None

    def test_no_match_indented_standalone(self):
        """Pattern does NOT match indented standalone AUTHOR comment."""
        match = INLINE_AUTHOR_PATTERN.match("    // AUTHOR: indented standalone")
        assert match is None


class TestUsesSlashComments:
    """Tests for file extension detection."""

    def test_swift_uses_slash_comments(self, tmp_path):
        """Swift files use // comments."""
        swift_file = tmp_path / "test.swift"
        assert uses_slash_comments(swift_file) is True

    def test_python_does_not_use_slash_comments(self, tmp_path):
        """Python files do NOT use // comments."""
        py_file = tmp_path / "test.py"
        assert uses_slash_comments(py_file) is False

    def test_javascript_uses_slash_comments(self, tmp_path):
        """JavaScript files use // comments."""
        js_file = tmp_path / "test.js"
        assert uses_slash_comments(js_file) is True

    def test_typescript_uses_slash_comments(self, tmp_path):
        """TypeScript files use // comments."""
        ts_file = tmp_path / "test.tsx"
        assert uses_slash_comments(ts_file) is True

    def test_case_insensitive(self, tmp_path):
        """Extension check is case insensitive."""
        swift_file = tmp_path / "test.SWIFT"
        assert uses_slash_comments(swift_file) is True


class TestNormalizeInlineComments:
    """Tests for normalizing inline AUTHOR comments to own lines."""

    def test_moves_inline_to_own_line_swift(self, tmp_path):
        """Inline AUTHOR comment is moved above the code in Swift."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("let x = 5 // AUTHOR: Is this right?\n")

        modified = normalize_inline_comments(test_file)

        assert modified is True
        lines = test_file.read_text().splitlines()
        assert lines[0] == "// AUTHOR: Is this right?"
        assert lines[1] == "let x = 5"

    def test_skips_python_files(self, tmp_path):
        """Python files are NOT normalized (// isn't a comment)."""
        test_file = tmp_path / "test.py"
        original = 'print("// AUTHOR: not a comment")\n'
        test_file.write_text(original)

        modified = normalize_inline_comments(test_file)

        assert modified is False
        assert test_file.read_text() == original

    def test_author_has_no_indentation(self, tmp_path):
        """AUTHOR comment has no indentation, code keeps its indentation."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("    func foo() // AUTHOR: Rename this?\n")

        normalize_inline_comments(test_file)

        lines = test_file.read_text().splitlines()
        assert lines[0] == "// AUTHOR: Rename this?"
        assert lines[1] == "    func foo()"

    def test_no_modification_for_standalone(self, tmp_path):
        """Standalone AUTHOR comments are not modified."""
        test_file = tmp_path / "test.swift"
        original = "// AUTHOR: Already on own line\nlet x = 5\n"
        test_file.write_text(original)

        modified = normalize_inline_comments(test_file)

        assert modified is False
        assert test_file.read_text() == original

    def test_multiple_inline_comments(self, tmp_path):
        """Multiple inline comments are all normalized."""
        test_file = tmp_path / "test.swift"
        test_file.write_text(
            "let a = 1 // AUTHOR: First\n"
            "let b = 2\n"
            "let c = 3 // AUTHOR: Second\n"
        )

        modified = normalize_inline_comments(test_file)

        assert modified is True
        lines = test_file.read_text().splitlines()
        assert lines[0] == "// AUTHOR: First"
        assert lines[1] == "let a = 1"
        assert lines[2] == "let b = 2"
        assert lines[3] == "// AUTHOR: Second"
        assert lines[4] == "let c = 3"


class TestNormalizeAllInlineComments:
    """Tests for normalizing inline comments across directories."""

    def test_normalizes_swift_file(self, tmp_path):
        """Swift file with inline comment is normalized."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("code() // AUTHOR: question\n")

        modified = normalize_all_inline_comments(test_file)

        assert modified == [str(test_file)]

    def test_skips_python_file(self, tmp_path):
        """Python files are skipped."""
        test_file = tmp_path / "test.py"
        test_file.write_text("code() // AUTHOR: question\n")

        modified = normalize_all_inline_comments(test_file)

        assert modified == []

    def test_normalizes_directory(self, tmp_path):
        """All Swift files in directory are normalized."""
        file1 = tmp_path / "file1.swift"
        file2 = tmp_path / "file2.swift"
        file1.write_text("a() // AUTHOR: q1\n")
        file2.write_text("b() // AUTHOR: q2\n")

        modified = normalize_all_inline_comments(tmp_path)

        assert len(modified) == 2
        assert str(file1) in modified
        assert str(file2) in modified

    def test_skips_git_directory(self, tmp_path):
        """Files in .git directory are not processed."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        git_file = git_dir / "config.swift"
        git_file.write_text("code() // AUTHOR: should ignore\n")

        modified = normalize_all_inline_comments(tmp_path)

        assert modified == []

    def test_mixed_extensions(self, tmp_path):
        """Only files with // comments are normalized."""
        swift_file = tmp_path / "test.swift"
        py_file = tmp_path / "test.py"
        swift_file.write_text("a() // AUTHOR: normalize this\n")
        py_file.write_text("a() // AUTHOR: skip this\n")

        modified = normalize_all_inline_comments(tmp_path)

        assert modified == [str(swift_file)]


class TestGetThreadsWithInlineComments:
    """Tests for get_threads with inline comment normalization."""

    def test_get_threads_normalizes_inline_swift(self, tmp_path):
        """get_threads normalizes inline comments in Swift files."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("let x = 5 // AUTHOR: Is this ok?\n")

        result = _get_threads(str(test_file))

        assert "normalized_files" in result
        assert len(result["normalized_files"]) == 1
        assert len(result["threads"]) == 1
        assert result["threads"][0]["thread"][0]["text"] == "Is this ok?"

    def test_get_threads_skips_python(self, tmp_path):
        """get_threads does not normalize Python files."""
        test_file = tmp_path / "test.py"
        # This has // AUTHOR: in a string - should NOT be normalized
        test_file.write_text('print("// AUTHOR: not real")\n')

        result = _get_threads(str(test_file))

        assert "normalized_files" not in result
        assert len(result["threads"]) == 0

    def test_get_threads_no_normalization_needed(self, tmp_path):
        """get_threads doesn't report normalized_files when none needed."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Already standalone\nlet x = 5\n")

        result = _get_threads(str(test_file))

        assert "normalized_files" not in result
        assert len(result["threads"]) == 1

    def test_normalized_thread_is_found(self, tmp_path):
        """After normalization, thread is properly detected."""
        test_file = tmp_path / "test.swift"
        test_file.write_text(
            "let startHue = max(0, centerHue - hueRange) "
            "// AUTHOR: can't these be computed props?\n"
        )

        result = _get_threads(str(test_file))

        assert result["summary"]["total"] == 1
        thread = result["threads"][0]
        assert thread["thread"][0]["text"] == "can't these be computed props?"

    def test_reply_to_normalized_thread(self, tmp_path):
        """Can reply to a thread that was normalized from inline."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("code() // AUTHOR: question\n")

        # Get threads (will normalize)
        result = _get_threads(str(test_file))
        thread_id = result["threads"][0]["id"]

        # Reply
        reply_result = _respond_to_thread(thread_id, "Answer!", str(test_file))

        assert reply_result["success"] is True
        content = test_file.read_text()
        assert "// AGENT: Answer!" in content


class TestFindGitRoot:
    """Tests for finding git repository root."""

    def test_finds_git_root_from_subdirectory(self):
        """Finds git root from a subdirectory."""
        # Use actual project directory
        project_dir = Path(__file__).parent.parent
        src_dir = project_dir / "src"

        result = find_git_root(src_dir)

        assert result is not None
        assert result == project_dir.resolve()

    def test_returns_none_for_non_git_directory(self, tmp_path):
        """Returns None for directory not in a git repo."""
        result = find_git_root(tmp_path)

        assert result is None


class TestOtherThreadsNotification:
    """Tests for other_threads notification in get_threads."""

    def test_no_other_threads_field_without_git(self, tmp_path):
        """No other_threads field when not in a git repo."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Question?\ndef foo(): pass\n")

        result = _get_threads(str(tmp_path))

        assert "other_threads" not in result

    def test_no_other_threads_when_scanning_root(self):
        """No other_threads when scanning entire repo root."""
        # Use actual project directory (the repo root)
        project_dir = Path(__file__).parent.parent

        result = _get_threads(str(project_dir))

        # When scanning root, there shouldn't be "other" threads
        assert "other_threads" not in result


class TestActionCommandDetection:
    """Tests for action_required field when AUTHOR uses command patterns."""

    def test_done_command_detected(self, tmp_path):
        """'done' triggers action_required with dismiss_thread."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: done\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        assert len(result["threads"]) == 1
        thread = result["threads"][0]
        assert "action_required" in thread
        assert thread["action_required"]["action"] == "dismiss_thread"
        assert "COMMAND detected" in thread["action_required"]["note"]

    def test_commit_command_detected(self, tmp_path):
        """'commit' triggers action_required with clear_and_commit."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: commit\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" in thread
        assert thread["action_required"]["action"] == "clear_and_commit"

    def test_commit_file_command_detected(self, tmp_path):
        """'commit file' triggers action_required with clear_and_commit."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: commit file\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" in thread
        assert thread["action_required"]["action"] == "clear_and_commit"

    def test_reset_command_detected(self, tmp_path):
        """'reset' triggers action_required with dismiss_thread."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: reset\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" in thread
        assert thread["action_required"]["action"] == "dismiss_thread"

    def test_partial_match_not_detected(self, tmp_path):
        """Partial matches like 'done with refactoring' are NOT commands."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: done with the refactoring\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" not in thread
        assert thread["status"] == "awaiting_agent"

    def test_commit_in_sentence_not_detected(self, tmp_path):
        """'commit to this approach' is NOT a command."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: I want to commit to this approach\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" not in thread

    def test_case_insensitive_command(self, tmp_path):
        """Commands are case-insensitive."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: DONE\nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" in thread
        assert thread["action_required"]["action"] == "dismiss_thread"

    def test_command_with_whitespace(self, tmp_path):
        """Commands with leading/trailing whitespace still work."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR:   done   \nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" in thread

    def test_no_action_when_awaiting_author(self, tmp_path):
        """No action_required when thread is awaiting author (empty AUTHOR line)."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: question?\n// AGENT: answer\n// AUTHOR: \nfunc foo() {}\n")

        result = _get_threads(str(test_file))

        thread = result["threads"][0]
        assert "action_required" not in thread
        assert thread["status"] == "awaiting_author"
