"""Core logic for inline-dialogue thread parsing and manipulation."""

import hashlib
import json
import os
import random
import re
import sqlite3
import string
import subprocess
from datetime import datetime
from pathlib import Path

# Comment prefix alternation for pattern matching (matches //, #, or --)
_COMMENT_PREFIX = r"(?://|#|--)"

AUTHOR_PATTERN = re.compile(rf"^(\s*){_COMMENT_PREFIX}\s*AUTHOR:\s*(.*)$")
AGENT_PATTERN = re.compile(rf"^(\s*){_COMMENT_PREFIX}\s*AGENT:\s*(.*)$")
SALT_PATTERN = re.compile(r"\[salt:[a-z0-9]+\]$")
# Matches inline AUTHOR comments: code <prefix> AUTHOR: text (where code has non-whitespace)
INLINE_AUTHOR_PATTERN = re.compile(rf"^(\s*)(\S.*){_COMMENT_PREFIX}\s*AUTHOR:\s*(.*)$")

# File extensions grouped by comment syntax
SLASH_COMMENT_EXTENSIONS = frozenset({
    ".swift", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".kt", ".kts", ".scala", ".m", ".mm", ".dart", ".groovy",
    ".gradle", ".json", ".jsonc",
})

HASH_COMMENT_EXTENSIONS = frozenset({
    ".py", ".rb", ".sh", ".bash", ".zsh", ".yml", ".yaml", ".toml", ".pl", ".pm",
    ".r", ".jl", ".tcl", ".conf", ".cfg", ".ini", ".cmake", ".tf", ".hcl",
    ".dockerfile", ".gitignore", ".env",
})

DASH_COMMENT_EXTENSIONS = frozenset({
    ".sql", ".lua", ".hs", ".lhs", ".elm", ".ada", ".adb", ".ads", ".vhdl", ".vhd",
})

# Mapping from extension set to prefix string
_EXTENSION_TO_PREFIX = {
    **{ext: "//" for ext in SLASH_COMMENT_EXTENSIONS},
    **{ext: "#" for ext in HASH_COMMENT_EXTENSIONS},
    **{ext: "--" for ext in DASH_COMMENT_EXTENSIONS},
}

# Exact command patterns that require immediate action (no response)
ACTION_COMMANDS = {
    "done": ("dismiss_thread", "Thread complete. Call dismiss_thread(thread_id, path) immediately."),
    "commit": ("clear_and_commit", "Commit requested. Call clear_and_commit(file) immediately."),
    "commit file": ("clear_and_commit", "Commit requested. Call clear_and_commit(file) immediately."),
    "reset": ("dismiss_thread", "Reset requested. Call dismiss_thread(thread_id, path) immediately."),
}


def is_plugin_directory(path: Path) -> bool:
    """Check if path is within a Claude Code plugin directory.

    Detects plugin directories by looking for .claude-plugin/plugin.json
    in the path itself or any parent directory.

    Args:
        path: Path to check.

    Returns:
        True if path is within a plugin directory.
    """
    check_path = path.resolve()
    # Check path and all parents up to filesystem root
    for parent in [check_path, *check_path.parents]:
        if (parent / ".claude-plugin" / "plugin.json").exists():
            return True
    return False


def find_git_root(path: Path) -> Path | None:
    """Find the git repository root from the given path."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            cwd=path if path.is_dir() else path.parent,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        return None


def generate_salt(length: int = 4) -> str:
    """Generate a random salt string."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def get_comment_prefix(file_path: Path) -> str | None:
    """Get the comment prefix for a file based on its extension.

    Returns:
        '//', '#', or '--' for known extensions, None for unknown.
    """
    return _EXTENSION_TO_PREFIX.get(file_path.suffix.lower())


def extract_prefix_from_line(line: str) -> str:
    """Extract the comment prefix from a thread marker line.

    Looks at the first non-whitespace characters to determine
    which comment syntax is used.

    Returns:
        '//', '#', or '--'. Falls back to '//' if unrecognized.
    """
    stripped = line.lstrip()
    if stripped.startswith("//"):
        return "//"
    if stripped.startswith("#"):
        return "#"
    if stripped.startswith("--"):
        return "--"
    return "//"


def uses_slash_comments(file_path: Path) -> bool:
    """Check if file uses // for comments based on extension."""
    return file_path.suffix.lower() in SLASH_COMMENT_EXTENSIONS


def normalize_inline_comments(file_path: Path) -> bool:
    """Move inline AUTHOR comments to their own line above the code.

    Transforms: `code <prefix> AUTHOR: text`
    Into:
        `<prefix> AUTHOR: text`
        `code`

    Only processes files with a known comment syntax, and only matches the
    native comment prefix for that file type (e.g., # for .py, -- for .sql).

    Args:
        file_path: Path to the file to normalize.

    Returns:
        True if the file was modified, False otherwise.
    """
    prefix = get_comment_prefix(file_path)
    if prefix is None:
        return False

    # Build file-specific inline pattern matching only the native prefix
    escaped_prefix = re.escape(prefix)
    inline_pattern = re.compile(rf"^(\s*)(\S.*){escaped_prefix}\s*AUTHOR:\s*(.*)$")

    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError):
        return False

    lines = content.splitlines()
    new_lines = []
    modified = False

    for line in lines:
        match = inline_pattern.match(line)
        if match:
            indent = match.group(1)
            code = match.group(2).rstrip()
            author_text = match.group(3)
            # Insert AUTHOR comment on its own line (no indent), then the code
            new_lines.append(f"{prefix} AUTHOR: {author_text}")
            new_lines.append(f"{indent}{code}")
            modified = True
        else:
            new_lines.append(line)

    if modified:
        try:
            file_path.write_text("\n".join(new_lines) + "\n")
        except OSError:
            return False

    return modified


def normalize_all_inline_comments(search_path: Path) -> list[str]:
    """Normalize inline AUTHOR comments in all files under search_path.

    Processes files with any known comment syntax (//, #, --).

    Args:
        search_path: File or directory to process.

    Returns:
        List of file paths that were modified.
    """
    modified_files = []

    if search_path.is_file():
        if normalize_inline_comments(search_path):
            modified_files.append(str(search_path))
        return modified_files

    for root, _dirs, files in os.walk(search_path):
        root_path = Path(root)
        if ".git" in root_path.parts:
            continue
        for filename in files:
            if filename.startswith("."):
                continue
            file_path = root_path / filename
            if normalize_inline_comments(file_path):
                modified_files.append(str(file_path))

    return modified_files


def compute_thread_id(file_path: str, first_author_text: str) -> str:
    """Compute stable thread ID from file path and first AUTHOR text."""
    content = f"{file_path}:{first_author_text}"
    return hashlib.sha256(content.encode()).hexdigest()[:8]


def strip_empty_trailing_author(thread_entries: list[dict]) -> list[dict]:
    """Remove trailing empty author entry from thread.

    The empty // AUTHOR: line is a cursor placeholder in the file,
    not actual conversation content. Don't include it in the JSON response.

    Args:
        thread_entries: List of thread entries with role/line/text.

    Returns:
        Thread entries with trailing empty author removed.
    """
    if not thread_entries:
        return thread_entries
    last = thread_entries[-1]
    if last["role"] == "author" and not last["text"]:
        return thread_entries[:-1]
    return thread_entries


def detect_action_command(last_author_text: str) -> dict | None:
    """Detect if AUTHOR text is a command requiring immediate action.

    Only exact matches trigger actions. Partial matches like "done with refactoring"
    are treated as normal conversation.

    Args:
        last_author_text: The text of the last AUTHOR line.

    Returns:
        Action info dict if command detected, None otherwise.
    """
    text = last_author_text.strip().lower()

    if text in ACTION_COMMANDS:
        action, note = ACTION_COMMANDS[text]
        return {
            "action": action,
            "text": last_author_text.strip(),
            "note": f"COMMAND detected: '{text}'. Do NOT respond. {note}",
        }

    return None


def find_threads_in_file(file_path: Path) -> list[dict]:
    """Find all AUTHOR/AGENT threads in a file."""
    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    lines = content.splitlines()
    threads = []
    current_thread = None

    for i, line in enumerate(lines):
        line_num = i + 1
        author_match = AUTHOR_PATTERN.match(line)
        agent_match = AGENT_PATTERN.match(line)

        if author_match:
            text = author_match.group(2).strip()
            if current_thread is None:
                current_thread = {
                    "file": str(file_path),
                    "start_line": line_num,
                    "thread": [],
                    "first_author_text": text,
                }
            current_thread["thread"].append({
                "role": "author",
                "line": line_num,
                "text": text,
            })
        elif agent_match:
            if current_thread is not None:
                text = agent_match.group(2).strip()
                current_thread["thread"].append({
                    "role": "agent",
                    "line": line_num,
                    "text": text,
                })
        elif current_thread is not None:
            thread_id = compute_thread_id(
                current_thread["file"],
                current_thread["first_author_text"],
            )
            last_entry = current_thread["thread"][-1]
            # Empty AUTHOR line is a placeholder for author's next response
            if last_entry["role"] == "author" and last_entry["text"]:
                status = "awaiting_agent"
            else:
                status = "awaiting_author"

            # Strip empty trailing author (it's a placeholder, not content)
            display_thread = strip_empty_trailing_author(current_thread["thread"])

            thread_data = {
                "id": thread_id,
                "file": current_thread["file"],
                "start_line": current_thread["start_line"],
                "thread": display_thread,
                "status": status,
            }

            # Add status guidance
            if status == "awaiting_author":
                thread_data["status_note"] = (
                    "Waiting for human response. Do NOT take action on this thread. "
                    "The empty // AUTHOR: line is a placeholder for the human's next input."
                )

            # Check for action commands in the last author message
            if status == "awaiting_agent" and last_entry["text"]:
                action = detect_action_command(last_entry["text"])
                if action:
                    thread_data["action_required"] = action
                else:
                    # Add guidance for regular responses (not commands)
                    thread_data["response_note"] = (
                        "Complete requested code changes BEFORE calling respond_to_thread. "
                        "Your response is a receipt for completed work. Use past tense."
                    )

            threads.append(thread_data)
            current_thread = None

    if current_thread is not None:
        thread_id = compute_thread_id(
            current_thread["file"],
            current_thread["first_author_text"],
        )
        last_entry = current_thread["thread"][-1]
        if last_entry["role"] == "author":
            status = "awaiting_agent"
        else:
            status = "awaiting_author"

        # Strip empty trailing author (it's a placeholder, not content)
        display_thread = strip_empty_trailing_author(current_thread["thread"])

        thread_data = {
            "id": thread_id,
            "file": current_thread["file"],
            "start_line": current_thread["start_line"],
            "thread": display_thread,
            "status": status,
        }

        # Add status guidance
        if status == "awaiting_author":
            thread_data["status_note"] = (
                "Waiting for human response. Do NOT take action on this thread. "
                "The empty // AUTHOR: line is a placeholder for the human's next input."
            )

        # Check for action commands in the last author message
        if status == "awaiting_agent" and last_entry["text"]:
            action = detect_action_command(last_entry["text"])
            if action:
                thread_data["action_required"] = action
            else:
                # Add guidance for regular responses (not commands)
                thread_data["response_note"] = (
                    "Complete requested code changes BEFORE calling respond_to_thread. "
                    "Your response is a receipt for completed work. Use past tense."
                )

        threads.append(thread_data)

    return threads


def find_all_threads(search_path: Path) -> tuple[list[dict], list[dict]]:
    """Find all threads in a directory or file.

    Returns:
        Tuple of (threads, warnings) where warnings contains duplicate ID info.

    Raises:
        ValueError: If search_path is within a plugin directory.
    """
    # Early exit if scanning within a plugin directory
    if is_plugin_directory(search_path):
        raise ValueError(
            f"Refusing to scan plugin directory: {search_path}. "
            "Thread scanning should target project files, not plugin internals."
        )

    threads = []
    warnings = []
    seen_ids: dict[str, dict] = {}

    def collect_threads(file_threads: list[dict]) -> None:
        for thread in file_threads:
            thread_id = thread["id"]
            if thread_id in seen_ids:
                existing = seen_ids[thread_id]
                warnings.append({
                    "type": "duplicate_thread_id",
                    "thread_id": thread_id,
                    "first_occurrence": {
                        "file": existing["file"],
                        "line": existing["start_line"],
                        "text": existing["thread"][0]["text"],
                    },
                    "duplicate": {
                        "file": thread["file"],
                        "line": thread["start_line"],
                        "text": thread["thread"][0]["text"],
                    },
                    "message": (
                        f"Duplicate thread ID '{thread_id}': same first AUTHOR text "
                        f"appears in {existing['file']}:{existing['start_line']} and "
                        f"{thread['file']}:{thread['start_line']}. "
                        "Reply operations may target the wrong thread."
                    ),
                })
            else:
                seen_ids[thread_id] = thread
            threads.append(thread)

    if search_path.is_file():
        collect_threads(find_threads_in_file(search_path))
        return threads, warnings

    for root, _dirs, files in os.walk(search_path):
        root_path = Path(root)
        if ".git" in root_path.parts:
            continue
        for filename in files:
            if filename.startswith("."):
                continue
            file_path = root_path / filename
            collect_threads(find_threads_in_file(file_path))

    return threads, warnings


def salt_duplicate_threads(warnings: list[dict]) -> list[dict]:
    """Add salt to duplicate threads to make them unique.

    Modifies files in place to add [salt:xxxx] suffix to duplicate AUTHOR lines.

    Args:
        warnings: List of duplicate_thread_id warnings from find_all_threads.

    Returns:
        List of files that were modified.
    """
    modified_files = []

    for warning in warnings:
        if warning["type"] != "duplicate_thread_id":
            continue

        dup = warning["duplicate"]
        file_path = Path(dup["file"])
        line_num = dup["line"]  # 1-indexed

        try:
            content = file_path.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        if line_num < 1 or line_num > len(lines):
            continue

        line = lines[line_num - 1]
        match = AUTHOR_PATTERN.match(line)
        if not match:
            continue

        # Don't add salt if already salted
        text = match.group(2).strip()
        if SALT_PATTERN.search(text):
            continue

        # Add salt to the line, preserving the original comment prefix
        salt = generate_salt()
        indent = match.group(1)
        prefix = extract_prefix_from_line(line)
        new_line = f"{indent}{prefix} AUTHOR: {text} [salt:{salt}]"
        lines[line_num - 1] = new_line

        try:
            file_path.write_text("\n".join(lines) + "\n")
            modified_files.append(str(file_path))
        except OSError:
            continue

    return modified_files


def find_thread_by_id(search_path: Path, thread_id: str) -> dict | None:
    """Find a specific thread by ID."""
    all_threads, _ = find_all_threads(search_path)
    for thread in all_threads:
        if thread["id"] == thread_id:
            return thread
    return None


def find_thread_location(file_path: Path, first_author_text: str) -> int | None:
    """Find the line number where a thread starts by searching for content."""
    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError):
        return None

    lines = content.splitlines()
    for i, line in enumerate(lines):
        match = AUTHOR_PATTERN.match(line)
        if match and match.group(2).strip() == first_author_text:
            return i + 1

    return None


# Thread logging to SQLite database
THREADS_DB_PATH = Path.home() / "Developer" / "claude-session-db" / "threads.db"


def _create_threads_schema(conn: sqlite3.Connection) -> None:
    """Create threads table schema if it doesn't exist."""
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS threads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        repo TEXT NOT NULL,
        branch TEXT,
        file_path TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        first_author_text TEXT,
        thread_json TEXT,
        agent_response TEXT,
        status TEXT
    )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_threads_timestamp ON threads(timestamp)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_threads_thread_id ON threads(thread_id)"
    )
    conn.commit()


def _get_git_info(file_path: str) -> tuple[str, str]:
    """Get git repo path and branch for a file."""
    try:
        file_dir = Path(file_path).parent
        if not file_dir.exists():
            file_dir = Path.cwd()

        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(file_dir),
            timeout=5,
        )
        repo_path = result.stdout.strip() if result.returncode == 0 else str(file_dir)

        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(file_dir),
            timeout=5,
        )
        branch = result.stdout.strip() if result.returncode == 0 else ""

        return repo_path, branch
    except Exception:
        return str(Path(file_path).parent), ""


def log_thread_event(
    file_path: str,
    thread_id: str,
    event_type: str,
    first_author_text: str = "",
    thread_content: list | None = None,
    agent_response: str = "",
    status: str = "",
) -> bool:
    """Log a thread event to SQLite database.

    Args:
        file_path: Path to file containing the thread
        thread_id: The inline-dialogue thread hash ID
        event_type: Type of event (respond, dismiss, commit)
        first_author_text: Original AUTHOR request that started thread
        thread_content: Full thread array (will be JSON serialized)
        agent_response: The response being added (for respond events)
        status: Thread status at time of event

    Returns:
        True if logged successfully, False otherwise
    """
    try:
        THREADS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(THREADS_DB_PATH))
        _create_threads_schema(conn)

        repo_path, branch = _get_git_info(file_path)
        thread_json = json.dumps(thread_content) if thread_content else None

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO threads (
                timestamp, repo, branch, file_path, thread_id, event_type,
                first_author_text, thread_json, agent_response, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                repo_path,
                branch,
                file_path,
                thread_id,
                event_type,
                first_author_text,
                thread_json,
                agent_response,
                status,
            ),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        # Silent failure - don't disrupt MCP tool operation
        return False
