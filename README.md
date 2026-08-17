# inline-relay

A Claude Code plugin for code review conversations that live inside the source files themselves. You leave a question as a tagged comment. Claude responds in another tagged comment, indented underneath. The whole thread is plain text in the file, versioned with git, visible in diffs.

> Source: [github.com/synodic-studio/inline-relay](https://github.com/synodic-studio/inline-relay). MIT-licensed.

## Why this exists

Most code review tools assume the conversation lives somewhere other than the code. GitHub PRs, Linear comments, Slack threads, Google Docs. The discussion is separated from the artifact, and the artifact is what you actually need to change. This plugin moves the conversation into the file.

You write a comment like:

```python
# AUTHOR: Should this retry on 500s, or is that the caller's job?
#
```

Claude reads that, decides what to do, and edits the file to add a response:

```python
# AUTHOR: Should this retry on 500s, or is that the caller's job?
# AGENT: Caller's job. This function is a thin wrapper around the SDK
#        and the SDK already has its own retry policy. Adding a second
#        retry layer here would compound delays.
#
```

The trailing empty comment is a cursor for your next reply. When you write text in it, the thread becomes `awaiting_agent` again.

## What's in the plugin

- **One command:** `/inline-relay:process` scans for threads in a path and works through them until none are awaiting the agent.
- **One skill** that loads contextually when threads are involved and gives the agent behavioral guidance.
- **A CLI** (`inline-relay`) with five subcommands — `get-threads`, `respond`, `dismiss`, `clear-commit`, `process-all` — that does all the deterministic thread parsing and marker manipulation. The skill drives it via Bash; no server process required.
- **Pre-tool-use hooks** that block normal Edit/Write calls from accidentally trampling thread markers. Marker changes go exclusively through the CLI.

## Install

Add to your Claude Code plugins via the synodic-studio marketplace, or run `claude plugins add` against this repo.

## See it work

```bash
./scripts/demo.sh            # four beats, one keypress between each
./scripts/demo.sh --auto     # start to finish, no interaction
./scripts/demo.sh --cleanup  # remove the scratch repo it created
```

It builds a throwaway git repo under `/tmp`, puts a real review thread in a source file, feeds the hook the same PreToolUse payload Claude Code would send, and shows it deny a marker edit and then allow the same change through the CLI. `-h` prints the beat list.

## Notes

Thread IDs are content-addressable (`SHA256(file_path + first_comment_text)[:8]`), so threads survive line drift from edits above them. Future-tense responses ("I will fix this") are blocked at format-check time, because the agent should do the work first and respond with what it actually did.

## Companion piece

[synodic.co/inline-relay](https://synodic.co/inline-relay/) walks through the design in more detail, including the variant of this same idea that uses GitHub draft PRs as the review surface instead of inline source comments.
