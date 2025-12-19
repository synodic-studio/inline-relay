"""inline-dialogue MCP server for managing AUTHOR/AGENT code review threads."""

import hashlib
import os
import random
import re
import string
import subprocess
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("inline-dialogue")

AUTHOR_PATTERN = re.compile(r"^(\s*)//\s*AUTHOR:\s*(.*)$")
AGENT_PATTERN = re.compile(r"^(\s*)//\s*AGENT:\s*(.*)$")
SALT_PATTERN = re.compile(r"\[salt:[a-z0-9]+\]$")
# Matches inline AUTHOR comments: code // AUTHOR: text (where code has non-whitespace)
INLINE_AUTHOR_PATTERN = re.compile(r"^(\s*)(\S.*)//\s*AUTHOR:\s*(.*)$")

# File extensions where // is the comment syntax (safe to normalize inline comments)
SLASH_COMMENT_EXTENSIONS = frozenset({
    ".swift", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".kt", ".kts", ".scala", ".m", ".mm", ".dart", ".groovy",
    ".gradle", ".json", ".jsonc",
})


def generate_salt(length: int = 4) -> str:
    """Generate a random salt string."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def uses_slash_comments(file_path: Path) -> bool:
    """Check if file uses // for comments based on extension."""
    return file_path.suffix.lower() in SLASH_COMMENT_EXTENSIONS


def normalize_inline_comments(file_path: Path) -> bool:
    """Move inline AUTHOR comments to their own line above the code.

    Transforms: `code // AUTHOR: text`
    Into:
        `// AUTHOR: text`
        `code`

    Only processes files where // is the comment syntax.

    Args:
        file_path: Path to the file to normalize.

    Returns:
        True if the file was modified, False otherwise.
    """
    if not uses_slash_comments(file_path):
        return False

    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError):
        return False

    lines = content.splitlines()
    new_lines = []
    modified = False

    for line in lines:
        match = INLINE_AUTHOR_PATTERN.match(line)
        if match:
            indent = match.group(1)
            code = match.group(2).rstrip()
            author_text = match.group(3)
            # Insert AUTHOR comment on its own line, then the code
            new_lines.append(f"{indent}// AUTHOR: {author_text}")
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

    Only processes files where // is the comment syntax (skips .py, .rb, etc.).

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
            if last_entry["role"] == "author" and last_entry["text"] == "":
                status = "awaiting_user"
            elif last_entry["role"] == "author":
                status = "pending"
            else:
                status = "pending"

            threads.append({
                "id": thread_id,
                "file": current_thread["file"],
                "start_line": current_thread["start_line"],
                "thread": current_thread["thread"],
                "status": status,
            })
            current_thread = None

    if current_thread is not None:
        thread_id = compute_thread_id(
            current_thread["file"],
            current_thread["first_author_text"],
        )
        last_entry = current_thread["thread"][-1]
        if last_entry["role"] == "author" and last_entry["text"] == "":
            status = "awaiting_user"
        elif last_entry["role"] == "author":
            status = "pending"
        else:
            status = "pending"

        threads.append({
            "id": thread_id,
            "file": current_thread["file"],
            "start_line": current_thread["start_line"],
            "thread": current_thread["thread"],
            "status": status,
        })

    return threads


def find_all_threads(search_path: Path) -> tuple[list[dict], list[dict]]:
    """Find all threads in a directory or file.

    Returns:
        Tuple of (threads, warnings) where warnings contains duplicate ID info.
    """
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

        # Add salt to the line
        salt = generate_salt()
        indent = match.group(1)
        new_line = f"{indent}// AUTHOR: {text} [salt:{salt}]"
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


@mcp.tool()
def get_threads(path: str) -> dict:
    """Find all AUTHOR/AGENT threads in the codebase.

    Automatically normalizes inline AUTHOR comments (e.g., `code // AUTHOR: text`)
    by moving them to their own line above the code. Only processes files where
    // is the comment syntax (Swift, JS, TS, C, etc. - not Python).

    Automatically adds [salt:xxxx] suffix to duplicate threads to ensure
    unique IDs. Modified files are reported in the response.

    Args:
        path: Absolute path to directory or file to search (required).
              Claude Code should pass its current working directory.

    Returns:
        Dictionary with threads list and summary counts.
    """
    search_path = Path(path).resolve()
    if not search_path.exists():
        return {"error": f"Path not found: {path}"}

    # Normalize inline AUTHOR comments (move to own line)
    normalized_files = normalize_all_inline_comments(search_path)

    try:
        threads, warnings = find_all_threads(search_path)
    except Exception as e:
        return {
            "parse_error": True,
            "file": str(search_path),
            "raw_content": str(e),
        }

    # Auto-salt duplicates and re-scan if any were found
    salted_files = []
    if warnings:
        salted_files = salt_duplicate_threads(warnings)
        if salted_files:
            # Re-scan to get updated threads with unique IDs
            threads, warnings = find_all_threads(search_path)

    pending = sum(1 for t in threads if t["status"] == "pending")
    awaiting = sum(1 for t in threads if t["status"] == "awaiting_user")

    result = {
        "threads": threads,
        "summary": {
            "total": len(threads),
            "pending": pending,
            "awaiting_user": awaiting,
        },
    }

    if normalized_files:
        result["normalized_files"] = normalized_files

    if salted_files:
        result["salted_files"] = salted_files

    # Warnings should be empty after salting, but include if any remain
    if warnings:
        result["warnings"] = warnings

    return result


@mcp.tool()
def respond_to_thread(thread_id: str, response: str, path: str) -> dict:
    """Add an AGENT response to a thread. Enforces formatting mechanically.

    Args:
        thread_id: ID from get_threads.
        response: The response text (without // AGENT: prefix).
        path: Absolute path (required). Use same path as get_threads.

    Returns:
        Success status and file modified.
    """
    search_path = Path(path).resolve()
    thread = find_thread_by_id(search_path, thread_id)
    if thread is None:
        return {"success": False, "error": f"Thread not found: {thread_id}"}

    file_path = Path(thread["file"])
    first_author_text = thread["thread"][0]["text"]

    start_line = find_thread_location(file_path, first_author_text)
    if start_line is None:
        return {"success": False, "error": "Could not locate thread in file"}

    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return {"success": False, "error": str(e)}

    lines = content.splitlines()

    thread_end = start_line - 1
    for i in range(start_line - 1, len(lines)):
        line = lines[i]
        if AUTHOR_PATTERN.match(line) or AGENT_PATTERN.match(line):
            thread_end = i
        else:
            break

    new_lines = [
        f"// AGENT: {response}",
        "// AUTHOR: ",
    ]

    lines = lines[: thread_end + 1] + new_lines + lines[thread_end + 1:]

    try:
        file_path.write_text("\n".join(lines) + "\n")
    except OSError as e:
        return {"success": False, "error": str(e)}

    return {"success": True, "file": str(file_path)}


@mcp.tool()
def clear_and_commit(file: str, message: str = "") -> dict:
    """Clear ALL thread markers from a file and commit that file only.

    Args:
        file: Path to the file.
        message: Commit message. Auto-generates if not provided.

    Returns:
        Success status, lines removed, commit hash, and other staged files.
    """
    file_path = Path(file).resolve()
    if not file_path.exists():
        return {"success": False, "error": f"File not found: {file}"}

    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return {"success": False, "error": str(e)}

    lines = content.splitlines()
    original_count = len(lines)

    filtered_lines = [
        line
        for line in lines
        if not AUTHOR_PATTERN.match(line) and not AGENT_PATTERN.match(line)
    ]

    lines_removed = original_count - len(filtered_lines)

    try:
        file_path.write_text("\n".join(filtered_lines) + "\n")
    except OSError as e:
        return {"success": False, "error": str(e)}

    if not message:
        message = f"Clear review threads from {file_path.name}"

    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
            cwd=file_path.parent,
        )
        other_staged = [
            f.strip()
            for f in result.stdout.splitlines()
            if f.strip() and f.strip() != file_path.name
        ]
    except subprocess.CalledProcessError:
        other_staged = []

    try:
        subprocess.run(
            ["git", "add", str(file_path)],
            check=True,
            cwd=file_path.parent,
        )
        result = subprocess.run(
            ["git", "commit", "-m", message, "--", str(file_path)],
            capture_output=True,
            text=True,
            check=True,
            cwd=file_path.parent,
        )
        hash_match = re.search(r"\[[\w-]+\s+([a-f0-9]+)\]", result.stdout)
        commit_hash = hash_match.group(1) if hash_match else "unknown"
    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "error": f"Git commit failed: {e.stderr}",
            "lines_removed": lines_removed,
        }

    return {
        "success": True,
        "file": str(file_path),
        "lines_removed": lines_removed,
        "commit_hash": commit_hash,
        "other_staged_files": other_staged,
    }


@mcp.tool()
def dismiss_thread(thread_id: str, path: str) -> dict:
    """Remove a single thread without committing.

    Args:
        thread_id: ID from get_threads.
        path: Absolute path (required). Use same path as get_threads.

    Returns:
        Success status, file modified, and lines removed.
    """
    search_path = Path(path).resolve()
    thread = find_thread_by_id(search_path, thread_id)
    if thread is None:
        return {"success": False, "error": f"Thread not found: {thread_id}"}

    file_path = Path(thread["file"])
    first_author_text = thread["thread"][0]["text"]

    start_line = find_thread_location(file_path, first_author_text)
    if start_line is None:
        return {"success": False, "error": "Could not locate thread in file"}

    try:
        content = file_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        return {"success": False, "error": str(e)}

    lines = content.splitlines()

    thread_start = start_line - 1
    thread_end = start_line - 1
    for i in range(start_line - 1, len(lines)):
        line = lines[i]
        if AUTHOR_PATTERN.match(line) or AGENT_PATTERN.match(line):
            thread_end = i
        else:
            break

    lines_removed = thread_end - thread_start + 1
    lines = lines[:thread_start] + lines[thread_end + 1:]

    try:
        file_path.write_text("\n".join(lines) + "\n")
    except OSError as e:
        return {"success": False, "error": str(e)}

    return {
        "success": True,
        "file": str(file_path),
        "lines_removed": lines_removed,
    }


if __name__ == "__main__":
    mcp.run()
