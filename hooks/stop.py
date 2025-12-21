#!/usr/bin/env python3
"""
Stop hook for inline-dialogue plugin.

Commits the threads.db database on stop. Push happens at session end
via synodic-kit's stop.py which handles the whole claude-session-db repo.
"""

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DB_REPO = Path.home() / "Developer" / "claude-session-db"
DB_FILE = DB_REPO / "threads.db"


def get_db_stats() -> dict:
    """Get stats from threads.db for commit message."""
    stats = {"total": 0, "size_kb": 0}

    try:
        if DB_FILE.exists():
            stats["size_kb"] = DB_FILE.stat().st_size / 1024
            conn = sqlite3.connect(str(DB_FILE))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM threads")
            stats["total"] = cursor.fetchone()[0]
            conn.close()
    except Exception:
        pass

    return stats


def commit_threads_db() -> None:
    """Commit the threads.db database (no push - that happens via synodic-kit)."""
    if not DB_REPO.exists():
        return

    if not DB_FILE.exists():
        return

    try:
        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "status", "--porcelain", "threads.db"],
            capture_output=True,
            text=True,
            cwd=str(DB_REPO),
            timeout=10,
        )

        if not result.stdout.strip():
            return

        stats = get_db_stats()

        subprocess.run(
            ["git", "add", "threads.db"],
            cwd=str(DB_REPO),
            timeout=10,
            check=True,
        )

        commit_msg = (
            f"Update thread log - {stats['total']} events\n\n"
            f"Size: {stats['size_kb']:.1f} KB\n"
            f"Timestamp: {datetime.now().isoformat()}"
        )

        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(DB_REPO),
            capture_output=True,
            timeout=30,
            check=True,
        )

        print(f"✅ Thread log committed ({stats['total']} events)", file=sys.stderr)

    except subprocess.CalledProcessError as e:
        print(f"⚠️  Thread log commit failed: {e}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️  Thread log error: {e}", file=sys.stderr)


def main():
    """Main entry point for Stop hook."""
    commit_threads_db()
    sys.exit(0)


if __name__ == "__main__":
    main()
