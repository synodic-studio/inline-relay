"""inline-dialogue MCP server for managing AUTHOR/AGENT code review threads."""

import re
import subprocess
from pathlib import Path

from fastmcp import Context, FastMCP

from .core import (
    AGENT_PATTERN,
    AUTHOR_PATTERN,
    find_all_threads,
    find_git_root,
    find_thread_by_id,
    find_thread_location,
    log_thread_event,
    normalize_all_inline_comments,
    salt_duplicate_threads,
)

mcp = FastMCP("inline-dialogue")


async def resolve_path(path: str, ctx: Context) -> Path:
    """Resolve path against client's root if relative."""
    search_path = Path(path)
    if not search_path.is_absolute():
        try:
            roots = await ctx.list_roots()
            if roots:
                base = str(roots[0].uri).replace("file://", "")
                search_path = Path(base) / path
        except Exception:
            pass
    if not search_path.is_absolute():
        search_path = search_path.resolve()
    return search_path


@mcp.tool()
async def get_threads(path: str, ctx: Context) -> dict:
    """Find all AUTHOR/AGENT threads in the codebase.

    CRITICAL: Threads are READ-ONLY in the file. Never edit thread markers
    directly. Use respond_to_thread to add responses. Use Edit tool only
    for code changes, preserving all thread markers exactly.

    If a thread has `action_required`, execute that action immediately
    without calling respond_to_thread.

    Args:
        path: Path to directory or file to search. Use "." for current project.

    Returns:
        Dictionary with threads list and summary counts.
    """
    search_path = await resolve_path(path, ctx)
    if not search_path.exists():
        return {"error": f"Path not found: {path}"}

    # Normalize inline AUTHOR comments (move to own line)
    normalized_files = normalize_all_inline_comments(search_path)

    try:
        threads, warnings = find_all_threads(search_path)
    except ValueError as e:
        # Plugin directory protection - return clear error
        return {"error": str(e)}
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

    awaiting_agent = sum(1 for t in threads if t["status"] == "awaiting_agent")
    awaiting_author = sum(1 for t in threads if t["status"] == "awaiting_author")

    result = {
        "threads": threads,
        "summary": {
            "total": len(threads),
            "awaiting_agent": awaiting_agent,
            "awaiting_author": awaiting_author,
        },
    }

    if normalized_files:
        result["normalized_files"] = normalized_files

    if salted_files:
        result["salted_files"] = salted_files

    # Warnings should be empty after salting, but include if any remain
    if warnings:
        result["warnings"] = warnings

    # Check for threads elsewhere in the repo (read-only, no modifications)
    git_root = find_git_root(search_path)
    if git_root and git_root.resolve() != search_path.resolve():
        try:
            repo_threads, _ = find_all_threads(git_root)
            # Find threads not in the scanned path
            scanned_thread_ids = {t["id"] for t in threads}
            other_threads = [t for t in repo_threads if t["id"] not in scanned_thread_ids]

            if other_threads:
                # Group by directory for helpful context
                other_dirs = {}
                for t in other_threads:
                    rel_path = Path(t["file"]).relative_to(git_root)
                    dir_name = str(rel_path.parent) if rel_path.parent != Path(".") else "(root)"
                    other_dirs[dir_name] = other_dirs.get(dir_name, 0) + 1

                dir_summary = ", ".join(f"{d}: {c}" for d, c in sorted(other_dirs.items()))
                other_awaiting_agent = sum(1 for t in other_threads if t["status"] == "awaiting_agent")

                result["other_threads"] = {
                    "count": len(other_threads),
                    "awaiting_agent": other_awaiting_agent,
                    "by_directory": other_dirs,
                    "note": f"{len(other_threads)} other thread(s) exist elsewhere in repo ({dir_summary}). "
                            f"{other_awaiting_agent} awaiting agent response. "
                            "Run get_threads on repo root to see all.",
                }
        except Exception:
            pass  # Silently skip if repo-wide scan fails

    return result


@mcp.tool()
async def respond_to_thread(thread_id: str, response: str, path: str, ctx: Context) -> dict:
    """Add an AGENT response to a thread. Enforces formatting mechanically.

    This is the ONLY way to add AGENT responses. NEVER use Edit tool to add
    // AGENT: lines - it will corrupt thread formatting. This tool appends
    correctly and adds the trailing // AUTHOR: placeholder.

    Args:
        thread_id: ID from get_threads.
        response: The response text (without // AGENT: prefix).
        path: Directory or file to search for the thread. Use "." for current project.

    Returns:
        Success status and file modified.
    """
    search_path = await resolve_path(path, ctx)
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

    # Detect existing thread indentation from first line
    first_line = lines[start_line - 1]
    indent_match = AUTHOR_PATTERN.match(first_line)
    indent = indent_match.group(1) if indent_match else ""

    thread_end = start_line - 1
    last_match = None
    for i in range(start_line - 1, len(lines)):
        line = lines[i]
        author_match = AUTHOR_PATTERN.match(line)
        agent_match = AGENT_PATTERN.match(line)
        if author_match or agent_match:
            thread_end = i
            last_match = ("author", author_match) if author_match else ("agent", agent_match)
        else:
            break

    # Check for duplicate or additional response when thread awaiting user
    append_to_existing = False
    prev_agent_line_idx = None
    if last_match and last_match[0] == "author":
        author_text = last_match[1].group(2).strip()
        if not author_text:
            # Thread ends with empty AUTHOR - check if this is a duplicate
            # Look for the previous AGENT response
            prev_agent_text = None
            for j in range(thread_end - 1, start_line - 2, -1):
                prev_line = lines[j]
                prev_agent_match = AGENT_PATTERN.match(prev_line)
                if prev_agent_match:
                    prev_agent_text = prev_agent_match.group(2).strip()
                    prev_agent_line_idx = j
                    break

            if prev_agent_text == response:
                return {
                    "success": False,
                    "error": "Duplicate response blocked (identical to previous AGENT response)",
                }
            else:
                # Different response - append to existing AGENT line instead of adding new
                append_to_existing = True

    if append_to_existing and prev_agent_line_idx is not None:
        # Append to existing AGENT line, don't add new lines
        prev_line = lines[prev_agent_line_idx]
        prev_match = AGENT_PATTERN.match(prev_line)
        prev_indent = prev_match.group(1) if prev_match else ""
        prev_text = prev_match.group(2).strip() if prev_match else ""
        lines[prev_agent_line_idx] = f"{prev_indent}// AGENT: {prev_text} | {response}"
    else:
        new_lines = [
            f"{indent}// AGENT: {response}",
            f"{indent}// AUTHOR: ",
        ]
        lines = lines[: thread_end + 1] + new_lines + lines[thread_end + 1:]

    try:
        file_path.write_text("\n".join(lines) + "\n")
    except OSError as e:
        return {"success": False, "error": str(e)}

    result = {"success": True, "file": str(file_path)}
    if append_to_existing:
        result["appended"] = True
        result["note"] = "Appended to existing AGENT response (thread was awaiting user)"

    # Warn about future-tense language (suggests action wasn't completed first)
    future_patterns = [
        r"\bwill\s+(?:add|remove|create|update|fix|change|refactor|move)",
        r"\bi'll\s+(?:add|remove|create|update|fix|change|refactor|move)",
        r"\bgoing to\s+(?:add|remove|create|update|fix|change)",
    ]
    response_lower = response.lower()
    for pattern in future_patterns:
        if re.search(pattern, response_lower):
            existing_warning = result.get("warning", "")
            future_warning = (
                "Response contains future-tense language. "
                "Did you complete the action first? Responses should describe completed work."
            )
            result["warning"] = f"{existing_warning}; {future_warning}" if existing_warning else future_warning
            break

    # Log the respond event to SQLite
    log_thread_event(
        file_path=str(file_path),
        thread_id=thread_id,
        event_type="respond",
        first_author_text=first_author_text,
        thread_content=thread["thread"],
        agent_response=response,
        status="awaiting_author",
    )

    return result


@mcp.tool()
def clear_and_commit(file: str, message: str = "") -> dict:
    """Clear ALL thread markers from a file and commit that file only.

    Only call when action_required says "commit" or "commit file".
    Removes all // AUTHOR: and // AGENT: lines permanently, then commits.

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

    # Log the commit event to SQLite (all threads cleared from file)
    log_thread_event(
        file_path=str(file_path),
        thread_id="all",
        event_type="commit",
        first_author_text=f"Cleared {lines_removed} lines, committed as {commit_hash}",
        status="committed",
    )

    return {
        "success": True,
        "file": str(file_path),
        "lines_removed": lines_removed,
        "commit_hash": commit_hash,
        "other_staged_files": other_staged,
    }


@mcp.tool()
async def dismiss_thread(thread_id: str, path: str, ctx: Context) -> dict:
    """Remove a single thread without committing.

    Only call when action_required says "done" or "reset".
    Permanently removes the thread markers from the file.

    Args:
        thread_id: ID from get_threads.
        path: Directory or file to search for the thread. Use "." for current project.

    Returns:
        Success status, file modified, and lines removed.
    """
    search_path = await resolve_path(path, ctx)
    thread = find_thread_by_id(search_path, thread_id)
    if thread is None:
        return {"success": False, "error": f"Thread not found: {thread_id}"}

    # Block dismiss unless thread has action_required with dismiss action
    action = thread.get("action_required", {})
    if action.get("action") != "dismiss_thread":
        status = thread.get("status", "unknown")
        if status == "awaiting_author":
            return {
                "success": False,
                "error": "Cannot dismiss: thread is awaiting human response. "
                         "The empty // AUTHOR: line is a placeholder for the human's next input.",
            }
        else:
            return {
                "success": False,
                "error": "Cannot dismiss: thread does not have a dismiss command. "
                         "Only dismiss when AUTHOR writes 'done' or 'reset'.",
            }

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

    # Log the dismiss event to SQLite
    log_thread_event(
        file_path=str(file_path),
        thread_id=thread_id,
        event_type="dismiss",
        first_author_text=first_author_text,
        thread_content=thread["thread"],
        status="dismissed",
    )

    return {
        "success": True,
        "file": str(file_path),
        "lines_removed": lines_removed,
    }


@mcp.tool()
async def process_all_actions(path: str, ctx: Context) -> dict:
    """Execute all pending termination commands (done/reset/commit) in one call.

    Scans for threads where AUTHOR wrote a termination command and executes
    each action automatically:
    - done/reset: thread markers removed (same as dismiss_thread)
    - commit/commit file: all markers cleared and file committed (same as clear_and_commit)

    Commits are executed before dismissals. If a file has both commit and
    dismiss actions, the commit takes priority (it clears everything).

    Args:
        path: Path to directory or file to search. Use "." for current project.

    Returns:
        Summary of actions executed with per-action results.
    """
    search_path = await resolve_path(path, ctx)
    if not search_path.exists():
        return {"error": f"Path not found: {path}"}

    normalize_all_inline_comments(search_path)

    try:
        threads, warnings = find_all_threads(search_path)
    except (ValueError, Exception) as e:
        return {"error": str(e)}

    if warnings:
        salt_duplicate_threads(warnings)
        threads, warnings = find_all_threads(search_path)

    actionable = [t for t in threads if "action_required" in t]

    if not actionable:
        return {"success": True, "actions_executed": 0, "note": "No pending actions found."}

    # Group: files needing commit vs threads needing dismiss
    commit_files = {}
    dismiss_threads = []
    for thread in actionable:
        action = thread["action_required"]["action"]
        if action == "clear_and_commit":
            commit_files[thread["file"]] = thread
        elif action == "dismiss_thread":
            dismiss_threads.append(thread)

    # Skip dismiss for files that will be committed (commit clears everything)
    dismiss_threads = [t for t in dismiss_threads if t["file"] not in commit_files]

    results = []
    errors = []

    # Execute commits first (clear_and_commit handles whole files)
    for file_path_str in commit_files:
        result = clear_and_commit.fn(file_path_str)
        if result.get("success"):
            results.append({
                "action": "clear_and_commit",
                "file": file_path_str,
                "lines_removed": result.get("lines_removed", 0),
                "commit_hash": result.get("commit_hash", "unknown"),
            })
        else:
            errors.append({
                "action": "clear_and_commit",
                "file": file_path_str,
                "error": result.get("error", "Unknown error"),
            })

    # Execute dismissals (re-finds thread each time since file may have changed)
    for thread in dismiss_threads:
        result = await dismiss_thread.fn(thread["id"], path, ctx)
        if result.get("success"):
            results.append({
                "action": "dismiss_thread",
                "thread_id": thread["id"],
                "file": thread["file"],
                "lines_removed": result.get("lines_removed", 0),
            })
        else:
            errors.append({
                "action": "dismiss_thread",
                "thread_id": thread["id"],
                "file": thread["file"],
                "error": result.get("error", "Unknown error"),
            })

    summary = {
        "success": len(errors) == 0,
        "actions_executed": len(results),
        "results": results,
    }

    if errors:
        summary["errors"] = errors

    return summary


if __name__ == "__main__":
    mcp.run()
