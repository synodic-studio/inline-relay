"""Tests for inline-dialogue MCP server thread handling."""

from pathlib import Path

import pytest

from inline_dialogue_mcp.server import (
    SALT_PATTERN,
    compute_thread_id,
    find_all_threads,
    find_thread_by_id,
    find_thread_location,
    find_threads_in_file,
    generate_salt,
    get_threads,
    respond_to_thread,
    salt_duplicate_threads,
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
        assert threads[0]["status"] == "pending"

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
        assert threads[0]["status"] == "awaiting_user"

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

    def test_response_to_nonexistent_thread(self, tmp_path):
        """Returns error for missing thread ID."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass\n")

        result = _respond_to_thread("nonexistent", "Response", str(test_file))

        assert result["success"] is False
        assert "not found" in result["error"]

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
