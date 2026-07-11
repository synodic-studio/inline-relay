"""Command-line interface for inline-relay thread operations.

Each subcommand prints a JSON result to stdout and exits non-zero when the
result reports a failure (an ``error`` key, or ``success`` false), so callers
can branch on either the exit code or the parsed JSON.

Subcommands:
    get-threads [PATH]                       Find all threads (default ".")
    respond --id ID [--path PATH] [--response-file FILE]
                                             Add an AGENT response. Response text
                                             comes from --response-file, or stdin
                                             if the flag is omitted.
    dismiss --id ID [--path PATH]            Remove a thread (done/reset only)
    clear-commit --file FILE [--message MSG] Clear a file's markers and commit it
    process-all [PATH]                       Execute all pending done/reset/commit
"""

import argparse
import json
import sys

from . import actions


def _emit(result: dict) -> int:
    """Print a result as JSON and return the appropriate exit code."""
    print(json.dumps(result, indent=2))
    if result.get("error") is not None or result.get("success") is False:
        return 1
    return 0


def _read_response(args: argparse.Namespace) -> str:
    """Read AGENT response text from --response-file or stdin.

    Using a file (or piped stdin) instead of a shell argument avoids any
    quoting or injection issues with multi-line responses containing quotes,
    ``$``, ``|``, or backticks.
    """
    if args.response_file:
        raw = sys.stdin.read() if args.response_file == "-" else _read_file(args.response_file)
    elif sys.stdin.isatty():
        raise SystemExit(
            "error: no response provided. Pass --response-file PATH or pipe the "
            "response on stdin."
        )
    else:
        raw = sys.stdin.read()
    # AGENT markers are single-line comments; strip the trailing newline a file
    # or pipe leaves behind so it doesn't split the comment across lines.
    return raw.strip()


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inline-relay",
        description="Manage AUTHOR/AGENT inline code review threads.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get-threads", help="Find all AUTHOR/AGENT threads.")
    p_get.add_argument("path", nargs="?", default=".", help='Path to scan (default ".").')

    p_respond = sub.add_parser("respond", help="Add an AGENT response to a thread.")
    p_respond.add_argument("--id", dest="thread_id", required=True, help="Thread ID from get-threads.")
    p_respond.add_argument("--path", default=".", help='Path the thread lives under (default ".").')
    p_respond.add_argument(
        "--response-file",
        help='File containing the response text ("-" for stdin). If omitted, reads stdin.',
    )

    p_dismiss = sub.add_parser("dismiss", help="Remove a thread (done/reset only).")
    p_dismiss.add_argument("--id", dest="thread_id", required=True, help="Thread ID from get-threads.")
    p_dismiss.add_argument("--path", default=".", help='Path the thread lives under (default ".").')

    p_commit = sub.add_parser("clear-commit", help="Clear a file's markers and commit it.")
    p_commit.add_argument("--file", required=True, help="Path to the file to clear and commit.")
    p_commit.add_argument("--message", default="", help="Commit message (auto-generated if omitted).")

    p_process = sub.add_parser("process-all", help="Execute all pending done/reset/commit actions.")
    p_process.add_argument("path", nargs="?", default=".", help='Path to scan (default ".").')

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "get-threads":
        return _emit(actions.get_threads(args.path))
    if args.command == "respond":
        response = _read_response(args)
        return _emit(actions.respond_to_thread(args.thread_id, response, args.path))
    if args.command == "dismiss":
        return _emit(actions.dismiss_thread(args.thread_id, args.path))
    if args.command == "clear-commit":
        return _emit(actions.clear_and_commit(args.file, args.message))
    if args.command == "process-all":
        return _emit(actions.process_all_actions(args.path))

    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return 2


if __name__ == "__main__":
    sys.exit(main())
