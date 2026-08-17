"""Tests for inline-relay thread handling (actions + CLI)."""

import io
import json
from pathlib import Path

import pytest

from inline_relay.core import (
    INLINE_AUTHOR_PATTERN,
    SALT_PATTERN,
    compute_thread_id,
    find_all_threads,
    find_git_root,
    find_thread_by_id,
    find_thread_location,
    find_threads_in_file,
    generate_salt,
    get_comment_prefix,
    normalize_all_inline_comments,
    normalize_inline_comments,
    salt_duplicate_threads,
    strip_empty_trailing_author,
    uses_slash_comments,
)
from inline_relay.actions import (
    dismiss_thread as _dismiss_thread,
    get_threads as _get_threads,
    process_all_actions as _process_all_actions,
    respond_to_thread as _respond_to_thread,
)


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
        """Thread ending with empty AUTHOR has awaiting_author status."""
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
        # Empty trailing author is a placeholder, not shown in response
        assert len(threads[0]["thread"]) == 2
        assert threads[0]["thread"][-1]["role"] == "agent"

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


class TestStripEmptyTrailingAuthor:
    """Tests for stripping empty trailing author entries."""

    def test_strips_empty_trailing_author(self):
        """Removes empty author at end of thread."""
        thread = [
            {"role": "author", "line": 1, "text": "Question?"},
            {"role": "agent", "line": 2, "text": "Answer"},
            {"role": "author", "line": 3, "text": ""},
        ]
        result = strip_empty_trailing_author(thread)
        assert len(result) == 2
        assert result[-1]["role"] == "agent"

    def test_keeps_nonempty_trailing_author(self):
        """Does not strip author with content."""
        thread = [
            {"role": "author", "line": 1, "text": "Question?"},
            {"role": "agent", "line": 2, "text": "Answer"},
            {"role": "author", "line": 3, "text": "Follow-up"},
        ]
        result = strip_empty_trailing_author(thread)
        assert len(result) == 3
        assert result[-1]["text"] == "Follow-up"

    def test_keeps_trailing_agent(self):
        """Does not strip when thread ends with agent."""
        thread = [
            {"role": "author", "line": 1, "text": "Question?"},
            {"role": "agent", "line": 2, "text": "Answer"},
        ]
        result = strip_empty_trailing_author(thread)
        assert len(result) == 2

    def test_handles_empty_thread(self):
        """Returns empty list for empty input."""
        assert strip_empty_trailing_author([]) == []

    def test_single_empty_author_stripped(self):
        """Single empty author entry results in empty thread."""
        thread = [{"role": "author", "line": 1, "text": ""}]
        result = strip_empty_trailing_author(thread)
        assert len(result) == 0


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

    def test_appends_to_existing_agent_response(self, tmp_path):
        """Appends to existing AGENT line when thread is awaiting user."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            "// AUTHOR: Question?\n"
            "// AGENT: First answer\n"
            "// AUTHOR: \n"
            "def foo(): pass\n"
        )

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        # Different response should append to existing AGENT line
        result = _respond_to_thread(thread_id, "Additional clarification", str(test_file))

        assert result["success"] is True
        assert result.get("appended") is True
        assert "note" in result

        # File should have one AGENT line with both responses joined
        content = test_file.read_text()
        assert content.count("// AGENT:") == 1
        assert "First answer | Additional clarification" in content

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


class TestResponseWarnings:
    """Tests for response quality warnings (future-tense, vague claims)."""

    def test_future_tense_warning(self, tmp_path):
        """Warns about future-tense language in responses."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Fix this bug\ndef foo(): pass\n")

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        result = _respond_to_thread(
            thread_id, "I will fix the bug", str(test_file)
        )

        assert result["success"] is True
        assert "warning" in result
        assert "future-tense" in result["warning"]

    def test_specific_response_no_warning(self, tmp_path):
        """No warning for specific, past-tense response."""
        test_file = tmp_path / "test.py"
        test_file.write_text("// AUTHOR: Fix this bug\ndef foo(): pass\n")

        threads, _ = find_all_threads(test_file)
        thread_id = threads[0]["id"]

        result = _respond_to_thread(
            thread_id, "Fixed null check at line 42 in processData()", str(test_file)
        )

        assert result["success"] is True
        assert "warning" not in result



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

    def test_no_other_threads_when_scanning_repo_root(self, tmp_path):
        """No other_threads when the scan already covers the whole repo."""
        import subprocess

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        (tmp_path / "app.py").write_text(f"{_HASH_AUTHOR} Question?\ndef foo(): pass\n")

        result = _get_threads(str(tmp_path))

        assert len(result["threads"]) == 1
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


class TestProcessAllActions:
    """Tests for process_all_actions batch execution tool."""

    def test_no_pending_actions(self, tmp_path):
        """Returns success with zero actions when no commands pending."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: question?\n// AGENT: answer\n// AUTHOR: \nfunc foo() {}\n")

        result = _process_all_actions(str(test_file))

        assert result["success"] is True
        assert result["actions_executed"] == 0

    def test_dismiss_done_command(self, tmp_path):
        """Dismisses thread when AUTHOR writes 'done'."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: question?\n// AGENT: answer\n// AUTHOR: done\nfunc foo() {}\n")

        result = _process_all_actions(str(test_file))

        assert result["success"] is True
        assert result["actions_executed"] == 1
        assert result["results"][0]["action"] == "dismiss_thread"
        # Thread markers should be removed from file
        content = test_file.read_text()
        assert "AUTHOR" not in content
        assert "AGENT" not in content
        assert "func foo()" in content

    def test_dismiss_reset_command(self, tmp_path):
        """Dismisses thread when AUTHOR writes 'reset'."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: question?\n// AGENT: answer\n// AUTHOR: reset\nfunc foo() {}\n")

        result = _process_all_actions(str(test_file))

        assert result["success"] is True
        assert result["actions_executed"] == 1
        assert result["results"][0]["action"] == "dismiss_thread"
        content = test_file.read_text()
        assert "AUTHOR" not in content

    def test_multiple_dismiss_same_file(self, tmp_path):
        """Dismisses multiple threads from the same file."""
        test_file = tmp_path / "test.swift"
        test_file.write_text(
            "// AUTHOR: first question\n// AGENT: first answer\n// AUTHOR: done\n"
            "func foo() {}\n"
            "// AUTHOR: second question\n// AGENT: second answer\n// AUTHOR: done\n"
            "func bar() {}\n"
        )

        result = _process_all_actions(str(test_file))

        assert result["success"] is True
        assert result["actions_executed"] == 2
        content = test_file.read_text()
        assert "AUTHOR" not in content
        assert "func foo()" in content
        assert "func bar()" in content

    def test_skips_non_command_threads(self, tmp_path):
        """Leaves threads without termination commands untouched."""
        test_file = tmp_path / "test.swift"
        test_file.write_text(
            "// AUTHOR: needs response\nfunc foo() {}\n"
            "// AUTHOR: question\n// AGENT: answer\n// AUTHOR: done\nfunc bar() {}\n"
        )

        result = _process_all_actions(str(test_file))

        assert result["success"] is True
        assert result["actions_executed"] == 1
        content = test_file.read_text()
        # The non-command thread should still be there
        assert "needs response" in content
        # The done thread should be gone
        assert "done" not in content

    def test_no_threads_at_all(self, tmp_path):
        """Returns success when file has no threads."""
        test_file = tmp_path / "test.swift"
        test_file.write_text("func foo() {}\nfunc bar() {}\n")

        result = _process_all_actions(str(test_file))

        assert result["success"] is True
        assert result["actions_executed"] == 0

    def test_path_not_found(self, tmp_path):
        """Returns error for nonexistent path."""
        result = _process_all_actions(str(tmp_path / "nonexistent"))

        assert "error" in result

    def test_directory_scan(self, tmp_path):
        """Processes actions across multiple files in a directory."""
        file1 = tmp_path / "a.swift"
        file1.write_text("// AUTHOR: q1\n// AGENT: a1\n// AUTHOR: done\nfunc a() {}\n")
        file2 = tmp_path / "b.swift"
        file2.write_text("// AUTHOR: q2\n// AGENT: a2\n// AUTHOR: done\nfunc b() {}\n")

        result = _process_all_actions(str(tmp_path))

        assert result["success"] is True
        assert result["actions_executed"] == 2
        assert "AUTHOR" not in file1.read_text()
        assert "AUTHOR" not in file2.read_text()


class TestPluginDirectoryProtection:
    """Tests for plugin directory scanning protection."""

    def test_is_plugin_directory_detects_plugin(self, tmp_path):
        """Detects plugin directory by .claude-plugin/plugin.json."""
        from inline_relay.core import is_plugin_directory

        # Create plugin structure
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"name": "test"}')

        assert is_plugin_directory(tmp_path) is True
        assert is_plugin_directory(tmp_path / "src") is True  # Subdirectory

    def test_is_plugin_directory_non_plugin(self, tmp_path):
        """Regular directories are not detected as plugins."""
        from inline_relay.core import is_plugin_directory

        assert is_plugin_directory(tmp_path) is False

    def test_find_all_threads_rejects_plugin_directory(self, tmp_path):
        """find_all_threads raises ValueError for plugin directories."""
        from inline_relay.core import find_all_threads

        # Create plugin structure
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"name": "test"}')

        with pytest.raises(ValueError, match="Refusing to scan plugin directory"):
            find_all_threads(tmp_path)

    def test_get_threads_returns_error_for_plugin_directory(self, tmp_path):
        """get_threads returns error dict for plugin directories."""
        # Create plugin structure
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"name": "test"}')

        result = _get_threads(str(tmp_path))

        assert "error" in result
        assert "plugin directory" in result["error"]


class TestHookPluginDetection:
    """Tests for hook's plugin directory detection logic."""

    def test_detects_development_plugin_by_plugin_json(self, tmp_path, monkeypatch):
        """Detects development plugin via .claude-plugin/plugin.json when CWD matches."""
        from hooks.pre_tool_use import is_inline_relay_dev_directory, is_within_plugin

        # Create plugin structure
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"name": "inline-relay"}')

        assert is_inline_relay_dev_directory(str(tmp_path)) is True

        # File within should be detected when CWD is the plugin directory
        test_file = tmp_path / "skills" / "test.md"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("test content")
        
        # Mock CWD to be the plugin directory
        monkeypatch.chdir(tmp_path)
        assert is_within_plugin(str(test_file)) is True

    def test_rejects_non_inline_relay_plugin(self, tmp_path):
        """Rejects plugins with different names."""
        from hooks.pre_tool_use import is_inline_relay_dev_directory

        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"name": "some-other-plugin"}')

        assert is_inline_relay_dev_directory(str(tmp_path)) is False

    def test_rejects_file_outside_cwd(self, tmp_path, monkeypatch):
        """Rejects files outside CWD even if they are in a plugin directory."""
        from hooks.pre_tool_use import is_within_plugin

        # Create plugin structure in tmp_path
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text('{"name": "inline-relay"}')
        
        test_file = tmp_path / "test.py"
        test_file.write_text("code")

        # Set CWD to a different directory (not the plugin)
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)
        
        # File is in plugin dir, but CWD is not - should reject
        assert is_within_plugin(str(test_file)) is False


class TestGetCommentPrefix:
    """Tests for get_comment_prefix extension-to-prefix mapping."""

    def test_slash_extensions(self):
        """Slash-comment extensions return //."""
        for ext in [".swift", ".js", ".ts", ".java", ".go", ".rs", ".c"]:
            assert get_comment_prefix(Path(f"test{ext}")) == "//"

    def test_hash_extensions(self):
        """Hash-comment extensions return #."""
        for ext in [".py", ".rb", ".sh", ".yml", ".yaml", ".toml"]:
            assert get_comment_prefix(Path(f"test{ext}")) == "#"

    def test_dash_extensions(self):
        """Dash-comment extensions return --."""
        for ext in [".sql", ".lua", ".hs", ".elm"]:
            assert get_comment_prefix(Path(f"test{ext}")) == "--"

    def test_unknown_extension(self):
        """Unknown extensions return None."""
        assert get_comment_prefix(Path("test.xyz")) is None

    def test_case_insensitive(self):
        """Extension lookup is case insensitive."""
        assert get_comment_prefix(Path("test.PY")) == "#"
        assert get_comment_prefix(Path("test.SQL")) == "--"



# Helpers to construct marker strings without literal markers in source
# (Literal markers in .py source get corrupted by inline-relay processing)
_HASH_AUTHOR = "#" + " AUTHOR:"
_HASH_AGENT = "#" + " AGENT:"


class TestHashCommentThreads:
    """Tests for hash comment syntax (Python, Ruby, Shell, etc.)."""

    def test_find_thread_in_python_file(self, tmp_path):
        """Detect hash AUTHOR/AGENT threads in Python files."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            f"{_HASH_AUTHOR} Is this function correct?\n"
            f"{_HASH_AGENT} Yes, looks good.\n"
            f"{_HASH_AUTHOR} \n"
            "def foo():\n"
            "    pass\n"
        )
        threads = find_threads_in_file(test_file)
        assert len(threads) == 1
        assert threads[0]["thread"][0]["role"] == "author"
        assert threads[0]["thread"][0]["text"] == "Is this function correct?"
        assert threads[0]["thread"][1]["role"] == "agent"
        assert threads[0]["thread"][1]["text"] == "Yes, looks good."

    def test_find_thread_in_shell_file(self, tmp_path):
        """Detect hash AUTHOR thread in shell scripts."""
        test_file = tmp_path / "test.sh"
        test_file.write_text(
            "#!/bin/bash\n"
            f"{_HASH_AUTHOR} Should this use set -e?\n"
            "echo hello\n"
        )
        threads = find_threads_in_file(test_file)
        assert len(threads) == 1
        assert threads[0]["thread"][0]["text"] == "Should this use set -e?"

    def test_normalize_inline_python(self, tmp_path):
        """Inline hash AUTHOR is normalized in Python files."""
        test_file = tmp_path / "test.py"
        test_file.write_text(f"x = 5  {_HASH_AUTHOR} Is this right?\n")

        modified = normalize_inline_comments(test_file)

        assert modified is True
        lines = test_file.read_text().splitlines()
        assert lines[0] == f"{_HASH_AUTHOR} Is this right?"
        assert lines[1] == "x = 5"

    def test_normalize_inline_yaml(self, tmp_path):
        """Inline hash AUTHOR is normalized in YAML files."""
        test_file = tmp_path / "test.yml"
        test_file.write_text(f"key: value  {_HASH_AUTHOR} Correct key name?\n")

        modified = normalize_inline_comments(test_file)

        assert modified is True
        lines = test_file.read_text().splitlines()
        assert lines[0] == f"{_HASH_AUTHOR} Correct key name?"
        assert lines[1] == "key: value"

    def test_respond_uses_hash_prefix(self, tmp_path):
        """respond_to_thread writes hash prefix in Python files."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            f"{_HASH_AUTHOR} Fix this?\n"
            "def broken():\n"
            "    pass\n"
        )

        result = _get_threads(str(test_file))
        thread_id = result["threads"][0]["id"]

        result = _respond_to_thread(thread_id, "Fixed it.", str(test_file))
        assert result["success"] is True

        content = test_file.read_text()
        assert f"{_HASH_AGENT} Fixed it." in content
        assert f"{_HASH_AUTHOR} " in content
        # Should NOT contain // prefix
        assert "// AGENT:" not in content

    def test_get_threads_finds_hash_threads(self, tmp_path):
        """get_threads finds hash threads in Python files."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            f"{_HASH_AUTHOR} Review this\n"
            "x = 1\n"
        )

        result = _get_threads(str(test_file))
        assert result["summary"]["total"] == 1
        assert result["summary"]["awaiting_agent"] == 1

    def test_dismiss_hash_thread(self, tmp_path):
        """dismiss_thread removes hash threads."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            f"{_HASH_AUTHOR} done\n"
            "x = 1\n"
        )

        result = _get_threads(str(test_file))
        thread_id = result["threads"][0]["id"]

        result = _dismiss_thread(thread_id, str(test_file))
        assert result["success"] is True
        assert f"{_HASH_AUTHOR} " not in test_file.read_text()


class TestDashCommentThreads:
    """Tests for -- comment syntax (SQL, Lua, Haskell, etc.)."""

    def test_find_thread_in_sql_file(self, tmp_path):
        """Detect -- AUTHOR/AGENT threads in SQL files."""
        test_file = tmp_path / "query.sql"
        test_file.write_text(
            "-- AUTHOR: Should we add an index here?\n"
            "-- AGENT: Yes, add index on user_id.\n"
            "-- AUTHOR: \n"
            "SELECT * FROM users;\n"
        )
        threads = find_threads_in_file(test_file)
        assert len(threads) == 1
        assert threads[0]["thread"][0]["text"] == "Should we add an index here?"
        assert threads[0]["thread"][1]["text"] == "Yes, add index on user_id."

    def test_find_thread_in_lua_file(self, tmp_path):
        """Detect -- AUTHOR thread in Lua files."""
        test_file = tmp_path / "test.lua"
        test_file.write_text(
            "-- AUTHOR: Refactor this?\n"
            "function hello()\n"
            "  print('hello')\n"
            "end\n"
        )
        threads = find_threads_in_file(test_file)
        assert len(threads) == 1
        assert threads[0]["thread"][0]["text"] == "Refactor this?"

    def test_normalize_inline_sql(self, tmp_path):
        """Inline -- AUTHOR is normalized in SQL files."""
        test_file = tmp_path / "query.sql"
        test_file.write_text("SELECT * FROM users  -- AUTHOR: Too broad?\n")

        modified = normalize_inline_comments(test_file)

        assert modified is True
        lines = test_file.read_text().splitlines()
        assert lines[0] == "-- AUTHOR: Too broad?"
        assert lines[1] == "SELECT * FROM users"

    def test_respond_uses_dash_prefix(self, tmp_path):
        """respond_to_thread writes -- prefix in SQL files."""
        test_file = tmp_path / "query.sql"
        test_file.write_text(
            "-- AUTHOR: Optimize this query?\n"
            "SELECT * FROM orders;\n"
        )

        result = _get_threads(str(test_file))
        thread_id = result["threads"][0]["id"]

        result = _respond_to_thread(thread_id, "Added index.", str(test_file))
        assert result["success"] is True

        content = test_file.read_text()
        assert "-- AGENT: Added index." in content
        assert "-- AUTHOR: " in content
        assert "// AGENT:" not in content

    def test_get_threads_finds_dash_threads(self, tmp_path):
        """get_threads finds -- threads in SQL files."""
        test_file = tmp_path / "query.sql"
        test_file.write_text(
            "-- AUTHOR: Review this query\n"
            "SELECT 1;\n"
        )

        result = _get_threads(str(test_file))
        assert result["summary"]["total"] == 1
        assert result["summary"]["awaiting_agent"] == 1


class TestMixedSyntaxDirectory:
    """Tests for directories with mixed comment syntax files."""

    def test_get_threads_mixed_directory(self, tmp_path):
        """get_threads finds threads across different comment syntaxes."""
        swift_file = tmp_path / "app.swift"
        swift_file.write_text("// AUTHOR: Swift question\nlet x = 1\n")

        py_file = tmp_path / "script.py"
        py_file.write_text(f"{_HASH_AUTHOR} Python question\nx = 1\n")

        sql_file = tmp_path / "query.sql"
        sql_file.write_text("-- AUTHOR: SQL question\nSELECT 1;\n")

        result = _get_threads(str(tmp_path))
        assert result["summary"]["total"] == 3
        assert result["summary"]["awaiting_agent"] == 3

    def test_normalize_mixed_directory(self, tmp_path):
        """normalize handles mixed syntax inline comments."""
        swift_file = tmp_path / "app.swift"
        swift_file.write_text("let x = 5 // AUTHOR: swift q\n")

        py_file = tmp_path / "script.py"
        py_file.write_text(f"x = 5  {_HASH_AUTHOR} python q\n")

        sql_file = tmp_path / "query.sql"
        sql_file.write_text("SELECT 1  -- AUTHOR: sql q\n")

        modified = normalize_all_inline_comments(tmp_path)
        assert len(modified) == 3

        # Each file normalized with correct prefix
        assert swift_file.read_text().startswith("// AUTHOR:")
        assert py_file.read_text().startswith(f"{_HASH_AUTHOR}")
        assert sql_file.read_text().startswith("-- AUTHOR:")


class TestHookMultiSyntax:
    """Tests for hook detection of all marker syntaxes."""

    def test_edit_detects_hash_markers(self):
        """Edit guard detects hash AUTHOR markers."""
        from hooks.pre_tool_use import edit_touches_thread_markers

        assert edit_touches_thread_markers({"old_string": f"{_HASH_AUTHOR} text"}) is True
        assert edit_touches_thread_markers({"new_string": f"{_HASH_AGENT} text"}) is True

    def test_edit_detects_dash_markers(self):
        """Edit guard detects -- AUTHOR: markers."""
        from hooks.pre_tool_use import edit_touches_thread_markers

        assert edit_touches_thread_markers({"old_string": "-- AUTHOR: text"}) is True
        assert edit_touches_thread_markers({"new_string": "-- AGENT: text"}) is True

    def test_file_has_hash_markers(self, tmp_path):
        """file_has_thread_markers detects hash markers."""
        from hooks.pre_tool_use import file_has_thread_markers

        test_file = tmp_path / "test.py"
        test_file.write_text(f"{_HASH_AUTHOR} question\nx = 1\n")
        assert file_has_thread_markers(str(test_file)) is True

    def test_file_has_dash_markers(self, tmp_path):
        """file_has_thread_markers detects -- markers."""
        from hooks.pre_tool_use import file_has_thread_markers

        test_file = tmp_path / "query.sql"
        test_file.write_text("-- AUTHOR: question\nSELECT 1;\n")
        assert file_has_thread_markers(str(test_file)) is True

    def test_no_false_positive_on_clean_file(self, tmp_path):
        """Clean files don't trigger markers."""
        from hooks.pre_tool_use import file_has_thread_markers

        test_file = tmp_path / "clean.py"
        test_file.write_text("# This is a normal comment\nx = 1\n")
        assert file_has_thread_markers(str(test_file)) is False


class TestInlineAuthorPatternMultiSyntax:
    """Tests for inline AUTHOR pattern with multiple comment syntaxes."""

    def test_matches_hash_inline(self):
        """Pattern matches code with hash AUTHOR."""
        match = INLINE_AUTHOR_PATTERN.match(f"x = 5  {_HASH_AUTHOR} Is this right?")
        assert match is not None
        assert match.group(2).rstrip() == "x = 5"
        assert match.group(3) == "Is this right?"

    def test_matches_dash_inline(self):
        """Pattern matches code -- AUTHOR: text."""
        match = INLINE_AUTHOR_PATTERN.match("SELECT * FROM users  -- AUTHOR: Too broad?")
        assert match is not None
        assert match.group(2).rstrip() == "SELECT * FROM users"
        assert match.group(3) == "Too broad?"

    def test_no_match_standalone_hash(self):
        """Pattern does NOT match standalone hash AUTHOR."""
        match = INLINE_AUTHOR_PATTERN.match(f"{_HASH_AUTHOR} standalone")
        assert match is None

    def test_no_match_standalone_dash(self):
        """Pattern does NOT match standalone -- AUTHOR."""
        match = INLINE_AUTHOR_PATTERN.match("-- AUTHOR: standalone")
        assert match is None


class TestCLI:
    """Tests for the argparse CLI wrapper (JSON output, exit codes, input)."""

    def test_get_threads_prints_json_and_exits_zero(self, tmp_path, capsys):
        """get-threads prints a JSON result and returns exit code 0."""
        from inline_relay.cli import main

        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Review this?\nlet x = 1\n")

        code = main(["get-threads", str(test_file)])
        out = json.loads(capsys.readouterr().out)

        assert code == 0
        assert out["summary"]["total"] == 1
        assert out["threads"][0]["thread"][0]["text"] == "Review this?"

    def test_get_threads_missing_path_exits_one(self, tmp_path, capsys):
        """A logical error (missing path) prints JSON and returns exit code 1."""
        from inline_relay.cli import main

        code = main(["get-threads", str(tmp_path / "nope")])
        out = json.loads(capsys.readouterr().out)

        assert code == 1
        assert "error" in out

    def test_respond_via_response_file(self, tmp_path, capsys):
        """respond reads response text from --response-file."""
        from inline_relay.cli import main

        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Fix this?\nlet x = 1\n")
        thread_id = find_all_threads(test_file)[0][0]["id"]

        resp = tmp_path / "resp.txt"
        # Special characters that would break a shell argv
        resp.write_text("Fixed via `await`, cost $0, a|b handled.\n")

        code = main(["respond", "--id", thread_id, "--path", str(test_file),
                     "--response-file", str(resp)])
        out = json.loads(capsys.readouterr().out)

        assert code == 0
        assert out["success"] is True
        content = test_file.read_text()
        assert "// AGENT: Fixed via `await`, cost $0, a|b handled." in content
        # Trailing newline from the file must not split the comment
        assert "// AGENT: Fixed via `await`, cost $0, a|b handled.\n// AUTHOR: " in content

    def test_respond_via_stdin(self, tmp_path, capsys, monkeypatch):
        """respond reads from stdin when --response-file is omitted."""
        from inline_relay.cli import main

        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Fix this?\nlet x = 1\n")
        thread_id = find_all_threads(test_file)[0][0]["id"]

        monkeypatch.setattr("sys.stdin", io.StringIO("Fixed it via stdin.\n"))

        code = main(["respond", "--id", thread_id, "--path", str(test_file)])
        out = json.loads(capsys.readouterr().out)

        assert code == 0
        assert out["success"] is True
        assert "// AGENT: Fixed it via stdin." in test_file.read_text()

    def test_respond_no_input_on_tty_errors(self, tmp_path, monkeypatch):
        """respond with no response and an interactive tty raises SystemExit."""
        from inline_relay.cli import main

        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Fix this?\nlet x = 1\n")
        thread_id = find_all_threads(test_file)[0][0]["id"]

        class _Tty(io.StringIO):
            def isatty(self):
                return True

        monkeypatch.setattr("sys.stdin", _Tty(""))

        with pytest.raises(SystemExit):
            main(["respond", "--id", thread_id, "--path", str(test_file)])

    def test_dismiss_bad_id_exits_one(self, tmp_path, capsys):
        """dismiss with an unknown id returns exit code 1."""
        from inline_relay.cli import main

        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Fix this?\nlet x = 1\n")

        code = main(["dismiss", "--id", "nope", "--path", str(test_file)])
        out = json.loads(capsys.readouterr().out)

        assert code == 1
        assert out["success"] is False

    def test_clear_commit_missing_file_exits_one(self, tmp_path, capsys):
        """clear-commit on a missing file returns exit code 1."""
        from inline_relay.cli import main

        code = main(["clear-commit", "--file", str(tmp_path / "nope.swift")])
        out = json.loads(capsys.readouterr().out)

        assert code == 1
        assert out["success"] is False

    def test_process_all_no_actions(self, tmp_path, capsys):
        """process-all with no pending actions succeeds with zero executed."""
        from inline_relay.cli import main

        test_file = tmp_path / "test.swift"
        test_file.write_text("// AUTHOR: Just a question?\nlet x = 1\n")

        code = main(["process-all", str(test_file)])
        out = json.loads(capsys.readouterr().out)

        assert code == 0
        assert out["actions_executed"] == 0

    def test_no_subcommand_errors(self):
        """Invoking with no subcommand exits (argparse required=True)."""
        from inline_relay.cli import main

        with pytest.raises(SystemExit):
            main([])


class TestHookDenyContract:
    """Tests for the PreToolUse deny protocol the hook must speak.

    Claude Code only reads a hook's stdout on a zero exit, and only honors a
    `hookSpecificOutput.permissionDecision` of "deny". A regression in either
    half turns the guard into a no-op that still looks like it is working.
    """

    def _payload(self, tool_name, tool_input):
        return {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input}

    def test_marker_edit_is_denied(self, tmp_path):
        """An Edit touching a marker returns a deny decision."""
        from hooks.pre_tool_use import evaluate

        target = tmp_path / "app.py"
        target.write_text(f"{_HASH_AUTHOR} question\n")
        decision = evaluate(self._payload("Edit", {
            "file_path": str(target),
            "old_string": f"{_HASH_AGENT} old",
            "new_string": f"{_HASH_AGENT} new",
        }))

        assert decision is not None
        out = decision["hookSpecificOutput"]
        assert out["hookEventName"] == "PreToolUse"
        assert out["permissionDecision"] == "deny"
        assert "inline-relay respond" in out["permissionDecisionReason"]

    def test_write_over_thread_file_is_denied(self, tmp_path):
        """A Write to a file holding a thread returns a deny decision."""
        from hooks.pre_tool_use import evaluate

        target = tmp_path / "app.py"
        target.write_text(f"{_HASH_AUTHOR} question\nx = 1\n")
        decision = evaluate(self._payload("Write", {
            "file_path": str(target),
            "content": "x = 2\n",
        }))

        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_edit_away_from_markers_is_allowed(self, tmp_path):
        """An Edit that never mentions a marker passes through."""
        from hooks.pre_tool_use import evaluate

        target = tmp_path / "app.py"
        target.write_text(f"{_HASH_AUTHOR} question\nx = 1\n")
        decision = evaluate(self._payload("Edit", {
            "file_path": str(target),
            "old_string": "x = 1",
            "new_string": "x = 2",
        }))

        assert decision is None

    def test_unrelated_tool_is_ignored(self):
        """Tools other than Edit/Write are never denied."""
        from hooks.pre_tool_use import evaluate

        assert evaluate(self._payload("Bash", {"command": "ls"})) is None

    def test_bypass_env_var_allows_marker_edit(self, tmp_path, monkeypatch):
        """The documented escape hatch disables the guard."""
        from hooks.pre_tool_use import evaluate

        monkeypatch.setenv("INLINE_RELAY_ALLOW_DESTRUCTIVE", "1")
        target = tmp_path / "app.py"
        target.write_text(f"{_HASH_AUTHOR} question\n")
        decision = evaluate(self._payload("Edit", {
            "file_path": str(target),
            "old_string": f"{_HASH_AUTHOR} question",
            "new_string": "",
        }))

        assert decision is None

    def test_deny_exits_zero_and_prints_decision(self, tmp_path, monkeypatch, capsys):
        """main() prints the deny payload and still exits 0."""
        import json as _json

        from hooks import pre_tool_use

        target = tmp_path / "app.py"
        target.write_text(f"{_HASH_AUTHOR} question\n")
        payload = _json.dumps(self._payload("Edit", {
            "file_path": str(target),
            "old_string": f"{_HASH_AUTHOR} question",
            "new_string": "gone",
        }))
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        assert pre_tool_use.main() == 0
        emitted = _json.loads(capsys.readouterr().out)
        assert emitted["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allow_prints_nothing(self, tmp_path, monkeypatch, capsys):
        """An allowed tool call produces no stdout, so normal permissions apply."""
        import json as _json

        from hooks import pre_tool_use

        payload = _json.dumps(self._payload("Edit", {
            "file_path": str(tmp_path / "app.py"),
            "old_string": "x = 1",
            "new_string": "x = 2",
        }))
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

        assert pre_tool_use.main() == 0
        assert capsys.readouterr().out == ""


class TestHookRegistration:
    """The guard is only real if the plugin actually registers it."""

    def test_pre_tool_use_registered_for_edit_and_write(self):
        """hooks.json wires pre_tool_use.py to the Edit and Write tools."""
        import json as _json

        manifest = _json.loads((Path(__file__).parent.parent / "hooks" / "hooks.json").read_text())
        entries = manifest["hooks"]["PreToolUse"]
        matchers = [entry["matcher"] for entry in entries]
        commands = [h["command"] for entry in entries for h in entry["hooks"]]

        assert any("Edit" in m and "Write" in m for m in matchers)
        assert any("pre_tool_use.py" in c for c in commands)


class TestPluginDirectoryIsNeverRewritten:
    """A refused scan must not have touched the tree on its way to refusing.

    get_threads normalizes inline AUTHOR comments by rewriting files. Running
    that before the plugin-directory check meant `inline-relay get-threads .`
    inside the plugin -- or the test suite doing the same -- silently edited
    source files and then reported an error.
    """

    def _make_plugin_dir(self, tmp_path):
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name": "some-plugin"}')
        target = tmp_path / "script.sh"
        target.write_text(f'x=1  {_HASH_AUTHOR} inline marker in a code line\n')
        return target

    def test_get_threads_refuses_without_editing(self, tmp_path):
        """get_threads on a plugin dir errors and leaves files byte-identical."""
        target = self._make_plugin_dir(tmp_path)
        before = target.read_text()

        result = _get_threads(str(tmp_path))

        assert "Refusing to scan plugin directory" in result["error"]
        assert target.read_text() == before

    def test_process_all_refuses_without_editing(self, tmp_path):
        """process_all_actions on a plugin dir errors and edits nothing."""
        target = self._make_plugin_dir(tmp_path)
        before = target.read_text()

        result = _process_all_actions(str(tmp_path))

        assert "Refusing to scan plugin directory" in result["error"]
        assert target.read_text() == before
