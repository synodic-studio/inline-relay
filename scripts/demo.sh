#!/usr/bin/env bash
#
# inline-relay — live demo
#
# Code-review threads live inside the source file as tagged comments, versioned
# in git. A PreToolUse hook denies any Edit or Write that touches those markers,
# so a marker can only change by going through the inline-relay CLI. The hook is
# not a convenience wrapper; it is the boundary that makes the CLI the only door.
#
# BEATS
#   1  A review thread committed inside a real source file, read back by the CLI:
#      content-addressed thread id, status, file and line.
#   2  The boundary. The same edit-tool call twice — once against an ordinary
#      line (allowed, applied, diffed) and once against a thread marker (denied,
#      in the hook's own words, with the working tree left clean).
#   3  That same marker change routed through the CLI. It succeeds, and the
#      result is an ordinary git diff and an ordinary commit.
#   4  Resolution. clear-commit strips the thread out of the source and commits;
#      the conversation survives in history, not in the file.
#
# FLAGS
#   --auto      run start to finish with no keypresses (rehearsal, smoke test)
#   --cleanup   remove only the scratch repo this demo created
#   -h          print this header
#
# OFFLINE
#   Nothing here touches the network. Everything runs against a throwaway git
#   repo the demo creates under /tmp. No credentials are read or printed.
#
# WHAT IT TOUCHES
#   Creates /tmp/inline-relay-demo and nothing else. Real project files are
#   never modified. The CLI appends rows to a local sqlite event log outside the
#   scratch directory; --cleanup deliberately leaves those alone.
#
# FIRST RUN CHECKLIST
#   - `uv sync --extra dev` in the repo root, so `uv run inline-relay` resolves.
#     Without uv the demo falls back to python3 against src/ and still runs.
#   - Nothing to approve, sign into, or grant. There are no dialogs.
#
# Run it once with --auto, then --cleanup, so the first live run is not the
# first run.

# Deliberately no `set -e`. A beat that fails should print its failure and let
# the rest of the demo run; aborting the whole deck mid-sentence is worse.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd -P)"

DEMO_ROOT="/tmp/inline-relay-demo"
SENTINEL="$DEMO_ROOT/.created-by-inline-relay-demo"
RUN_DIR="$DEMO_ROOT/review-$(date +%H%M%S)"
SRC_FILE="$RUN_DIR/payments.py"
WORK="$DEMO_ROOT/work"

AUTO=""
CLEANUP=""

for arg in "$@"; do
    case "$arg" in
        --auto) AUTO=1 ;;
        --cleanup) CLEANUP=1 ;;
        -h|--help)
            awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
            exit 0 ;;
        *)
            echo "unknown argument: $arg (want --auto, --cleanup, or -h)" >&2
            exit 2 ;;
    esac
done

if [ -t 1 ]; then
    R=$'\033[0m'; B=$'\033[1m'; D=$'\033[2m'
    Y=$'\033[33m'; G=$'\033[32m'; RD=$'\033[31m'; C=$'\033[36m'
else
    R=""; B=""; D=""; Y=""; G=""; RD=""; C=""
fi

warn() { printf '%s%s%s\n' "$Y" "$1" "$R" >&2; }
die()  { printf '%s%s%s\n' "$RD" "$1" "$R" >&2; exit 1; }

beat() {
    printf '\n%s%s──── %s %s%s\n\n' "$B" "$C" "$1" "$2" "$R"
}

# Anything the operator has to do or look at, in a voice that cannot be
# mistaken for command output.
cue() {
    printf '\n%s%s   %s%s\n' "$Y" "$B" "$1" "$R"
    printf '%s%s   %s%s\n' "$Y" "$D" "$2" "$R"
}

# Every prompt says what pressing the key does, so nobody has to guess whether
# it advances the demo or ends their turn.
advance() {
    [ -n "$AUTO" ] && return 0
    printf '\n%s   [ %s ]%s' "$D" "$1" "$R"
    read -n 1 -s -r _ <&3
    printf '\r%*s\r' $((${#1} + 12)) ""
}

# Echo a command the way the audience would type it, then run the real thing.
show() { printf '%s$ %s%s\n' "$G" "$1" "$R"; }

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

if [ -n "$CLEANUP" ]; then
    case "$DEMO_ROOT" in
        /tmp/inline-relay-demo) ;;
        *) die "refusing to clean up '$DEMO_ROOT' — not the path this demo creates" ;;
    esac
    if [ ! -d "$DEMO_ROOT" ]; then
        echo "nothing to clean up; $DEMO_ROOT does not exist"
        exit 0
    fi
    # Only ever removes a directory this script stamped as its own.
    if [ ! -f "$SENTINEL" ]; then
        die "refusing: $DEMO_ROOT exists but this demo did not create it"
    fi
    rm -rf "$DEMO_ROOT"
    echo "removed $DEMO_ROOT"
    exit 0
fi

# ---------------------------------------------------------------------------
# How the CLI gets invoked
# ---------------------------------------------------------------------------

if command -v uv >/dev/null 2>&1; then
    RELAY_CMD=(uv run --quiet --project "$REPO" inline-relay)
    RELAY_HOW="uv run --project <repo> inline-relay"
else
    RELAY_CMD=(env "PYTHONPATH=$REPO/src" python3 -m inline_relay)
    RELAY_HOW="python3 -m inline_relay (no uv on PATH)"
fi

relay() {
    show "inline-relay $*"
    "${RELAY_CMD[@]}" "$@"
}

relay_quiet() { "${RELAY_CMD[@]}" "$@"; }

HOOK="$REPO/hooks/pre_tool_use.py"

# The edit tool, as Claude Code runs it: hook first, tool second. If the hook
# denies, the edit never happens. This is the whole mechanism in one function.
edit_tool() {
    local payload="$1"
    local decision
    decision="$(python3 "$HOOK" < "$payload" 2>/dev/null)"

    if [ -n "$decision" ]; then
        printf '%shook stdout:%s\n' "$D" "$R"
        printf '%s\n' "$decision"
        printf '\n%s%sedit refused — the tool never ran%s\n' "$RD" "$B" "$R"
        return 1
    fi

    printf '%shook stdout: (empty — nothing to deny)%s\n' "$D" "$R"
    python3 - "$payload" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
ti = payload["tool_input"]
path = ti["file_path"]
text = open(path).read()
if ti["old_string"] not in text:
    sys.exit(f"old_string not present in {path}")
open(path, "w").write(text.replace(ti["old_string"], ti["new_string"], 1))
print(f"edit applied to {path}")
PY
}

# Write a PreToolUse payload in the shape Claude Code sends on stdin.
make_payload() {
    local out="$1" tool="$2" file="$3" old="$4" new="$5"
    OUT="$out" TOOL="$tool" FILE="$file" OLD="$old" NEW="$new" python3 - <<'PY'
import json, os
payload = {
    "session_id": "demo",
    "hook_event_name": "PreToolUse",
    "cwd": os.path.dirname(os.environ["FILE"]),
    "tool_name": os.environ["TOOL"],
    "tool_input": {
        "file_path": os.environ["FILE"],
        "old_string": os.environ["OLD"],
        "new_string": os.environ["NEW"],
    },
}
with open(os.environ["OUT"], "w") as fh:
    json.dump(payload, fh, indent=2)
PY
}

# ---------------------------------------------------------------------------
# Terminal guard
# ---------------------------------------------------------------------------

# Without a tty every read returns instantly and the whole demo scrolls past in
# one second, which looks exactly like a crash. Test in a subshell first: a
# failed exec prints its own error before a 2>/dev/null on the same line can
# suppress it.
if [ -z "$AUTO" ]; then
    if (exec 3</dev/tty) 2>/dev/null; then
        exec 3</dev/tty
    else
        warn "No terminal to read keypresses from, so every beat would run at once."
        warn "Run it from a terminal, or use --auto to run the whole thing."
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

beat "preflight" ""

command -v git >/dev/null 2>&1 || die "git not found; every beat needs it."
command -v python3 >/dev/null 2>&1 || die "python3 not found; the hook is a python script."
[ -f "$HOOK" ] || die "hook not found at $HOOK — wrong repo?"

SUBCOMMANDS="$(relay_quiet --help 2>&1 | sed -n 's/.*{\([a-z,-]*\)}.*/\1/p' | head -1 | tr ',' ' ')"
if [ -z "$SUBCOMMANDS" ]; then
    warn "the inline-relay CLI did not answer, running: $RELAY_HOW"
    die "Run 'uv sync --extra dev' in $REPO (the package needs Python 3.13+)."
fi

# The hook fails open: if it crashes, Claude Code reads the non-zero exit as a
# hook error and runs the edit anyway. So prove here, under the same python3
# beat 2 will use, that a marker edit actually comes back denied.
# AUTHOR: " \
SELFTEST="$(printf '{"hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"/nonexistent/x.py","old_string":"%s q","new_string":""}}' "
    | python3 "$HOOK" 2>&1 | tr -d ' \n')"
case "$SELFTEST" in
    *'"permissionDecision":"deny"'*) GUARD_LIVE="deny (as expected)" ;;
    "") GUARD_LIVE="" ;;
    *) GUARD_LIVE="" ;;
esac

GUARD="$(python3 - "$REPO/hooks/hooks.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
for entry in manifest.get("hooks", {}).get("PreToolUse", []):
    for hook in entry.get("hooks", []):
        if "pre_tool_use" in hook.get("command", ""):
            print(f'matcher "{entry.get("matcher")}"  ->  {hook["command"].split("/")[-1]}')
PY
)"

printf '  %-14s%s\n' "python3" "$(python3 --version 2>&1 | awk '{print $2}')"
printf '  %-14s%s\n' "git" "$(git --version | awk '{print $3}')"
printf '  %-14s%s\n' "cli" "$RELAY_HOW"
printf '  %-14s%s\n' "subcommands" "$SUBCOMMANDS"

if [ -n "$GUARD_LIVE" ]; then
    printf '  %-14s%s%s%s\n' "selftest" "$G" "marker edit -> $GUARD_LIVE" "$R"
else
    printf '  %-14s%s%s%s\n' "selftest" "$RD" "the hook did not deny a marker edit" "$R"
    warn ""
    warn "The guard is not denying. Beat 2's payoff will not land — check that"
    warn "python3 can run $HOOK before going live."
fi

if [ -n "$GUARD" ]; then
    printf '  %-14s%sPreToolUse  %s%s\n' "guard" "$G" "$GUARD" "$R"
else
    printf '  %-14s%s%s%s\n' "guard" "$RD" "NOT REGISTERED in hooks/hooks.json" "$R"
    warn ""
    warn "The PreToolUse guard is not registered, so Claude Code would never run it."
    warn "Beat 2 will still show what the hook decides, but say out loud that the"
    warn "plugin is not wiring it up."
fi

# A leftover scratch repo from the last run silently ruins beat 1 and beat 4.
# Catch it here, not on the last slide.
if [ -d "$DEMO_ROOT" ]; then
    die "$DEMO_ROOT already exists from an earlier run. Clear it: $0 --cleanup"
fi

mkdir -p "$RUN_DIR" "$WORK" || die "could not create $RUN_DIR"
touch "$SENTINEL"
printf '  %-14s%s\n' "scratch" "$RUN_DIR"

advance "press when you are ready to start"

# ---------------------------------------------------------------------------
# Beat 1 — the thread lives in the file
# ---------------------------------------------------------------------------

beat "1" "a review thread inside the source file"

cat > "$SRC_FILE" <<'PY'
def charge(customer_id, amount_cents):
    """Charge a customer. Returns the gateway's charge record."""
    resp = gateway.post(
        "/charges",
        {"customer": customer_id, "amount": amount_cents},
        idempotency_key=f"chg-{customer_id}-{amount_cents}",
    )
    # AUTHOR: Should this retry on 500s, or is that the caller's job?
    if resp.status_code >= 500:
        raise GatewayError(resp.text)
    return resp.json()
PY

(
    cd "$RUN_DIR" || exit 1
    git init -q .
    git config user.name "demo"
    git config user.email "demo@example.invalid"
    git add payments.py
    git commit -q -m "payments: add charge()"
)

show "cat payments.py"
cat "$SRC_FILE"
echo
show "git log --oneline --stat -1"
git -C "$RUN_DIR" --no-pager log --oneline --stat -1
echo

relay get-threads "$RUN_DIR"

THREAD_ID="$(relay_quiet get-threads "$RUN_DIR" | python3 -c 'import json,sys; t=json.load(sys.stdin)["threads"]; print(t[0]["id"] if t else "")')"
THREAD_STATUS="$(relay_quiet get-threads "$RUN_DIR" | python3 -c 'import json,sys; t=json.load(sys.stdin)["threads"]; print(t[0]["status"] if t else "none")')"
THREAD_LINE="$(relay_quiet get-threads "$RUN_DIR" | python3 -c 'import json,sys; t=json.load(sys.stdin)["threads"]; print(t[0]["start_line"] if t else 0)')"

if [ -z "$THREAD_ID" ]; then
    warn "no thread was found — the rest of the demo has nothing to work on"
else
    printf '\n  %sthread %s  %s  payments.py:%s%s\n' "$B" "$THREAD_ID" "$THREAD_STATUS" "$THREAD_LINE" "$R"
    printf '  %sid = sha256(file path + question)[:8], so it moves with edits above it%s\n' "$D" "$R"
    printf '  %sand it is different on every run of this demo%s\n' "$D" "$R"
fi

cue "Point at the id." "Run the demo twice and it changes, because the path changes."

advance "press when you have finished on the thread and the id"

# ---------------------------------------------------------------------------
# Beat 2 — the hook refuses
# ---------------------------------------------------------------------------

beat "2" "the edit path that does not exist"

show "cat hooks/hooks.json   # the registration"
python3 - "$REPO/hooks/hooks.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
print(json.dumps({"PreToolUse": manifest.get("hooks", {}).get("PreToolUse", [])}, indent=2))
PY
echo
printf '  %sBelow, the hook runs on the real payload and the edit only happens if the%s\n' "$D" "$R"
printf '  %shook stays quiet. That is Claude Code'"'"'s own order of operations.%s\n' "$D" "$R"

echo
printf '%s%s(a) an ordinary line — nothing to do with the thread%s\n' "$B" "$C" "$R"
make_payload "$WORK/edit-a.json" "Edit" "$SRC_FILE" \
    '        raise GatewayError(resp.text)' \
    '        raise GatewayError(resp.text, status=resp.status_code)'
show "cat edit-a.json | python3 hooks/pre_tool_use.py"
edit_tool "$WORK/edit-a.json"
echo
show "git diff --stat"
git -C "$RUN_DIR" --no-pager diff --stat
(cd "$RUN_DIR" && git add payments.py && git commit -q -m "payments: carry the status code on GatewayError")
ALLOWED_SHA="$(git -C "$RUN_DIR" rev-parse --short HEAD)"
printf '  %sallowed edit landed as %s%s\n' "$D" "$ALLOWED_SHA" "$R"

advance "press when you have made the point that ordinary edits are untouched"

echo
printf '%s%s(b) the same tool, the same file, aimed at the thread marker%s\n' "$B" "$C" "$R"
make_payload "$WORK/edit-b.json" "Edit" "$SRC_FILE" \
# AUTHOR: Should this retry on 500s, or is that the caller'"'"'s job?' \
    '
    '    # AGENT: Yes, added a retry.'
show "cat edit-b.json"
cat "$WORK/edit-b.json"
echo
show "cat edit-b.json | python3 hooks/pre_tool_use.py"
edit_tool "$WORK/edit-b.json"

echo
show "git status --short   # the file the hook just protected"
git -C "$RUN_DIR" status --short
printf '  %s(no output above means the working tree is clean — the edit never touched it)%s\n' "$D" "$R"

cue "Read the reason aloud from the screen." "It names the CLI subcommands, so the refusal is also the instruction."

advance "press when you have finished with the refusal"

# ---------------------------------------------------------------------------
# Beat 3 — the same change, through the CLI
# ---------------------------------------------------------------------------

beat "3" "the same change, through the only door that opens"

RESPONSE="Caller's job -- the gateway SDK already retries 5xx with backoff. Carried the status code out on GatewayError instead."

show "inline-relay respond --id $THREAD_ID --path $RUN_DIR   # response on stdin"
printf '%s' "$RESPONSE" | relay_quiet respond --id "$THREAD_ID" --path "$RUN_DIR"
RESPOND_RC=$?
echo

if [ "$RESPOND_RC" -ne 0 ]; then
    warn "respond failed (exit $RESPOND_RC) — beat 4 will have nothing to clear"
fi

show "git diff"
git -C "$RUN_DIR" --no-pager diff
echo

(cd "$RUN_DIR" && git add payments.py && git commit -q -m "payments: answer the retry thread")
REPLY_SHA="$(git -C "$RUN_DIR" rev-parse --short HEAD)"

show "inline-relay get-threads $RUN_DIR   # the thread, re-read from the file"
relay_quiet get-threads "$RUN_DIR" > "$WORK/threads.json"
python3 - "$WORK/threads.json" <<'PY'
import json, sys
for t in json.load(open(sys.argv[1])).get("threads", []):
    print(f'  thread {t["id"]}  {t["status"]}')
    for entry in t["thread"]:
        print(f'    {entry["role"]:>6}: {entry["text"][:88]}')
PY

printf '\n  %sanswered in %s — the review turn is an ordinary commit%s\n' "$B" "$REPLY_SHA" "$R"

advance "press when you have finished on the diff and the commit"

# ---------------------------------------------------------------------------
# Beat 4 — resolution leaves the file
# ---------------------------------------------------------------------------

beat "4" "resolved threads leave the file, not the history"

show "inline-relay clear-commit --file payments.py --message 'payments: resolve retry thread'"
relay_quiet clear-commit --file "$SRC_FILE" --message "payments: resolve retry thread"
echo

show "grep -c AUTHOR: payments.py"
MARKERS="$(grep -c 'AUTHOR:' "$SRC_FILE" 2>/dev/null)"
[ -z "$MARKERS" ] && MARKERS="?"
echo "$MARKERS"
echo
show "cat payments.py"
cat "$SRC_FILE"
echo
show "git log --oneline"
git -C "$RUN_DIR" --no-pager log --oneline
echo

printf '  %s%s markers left in the file, %s commits holding the conversation%s\n' \
    "$B" "$MARKERS" "$(git -C "$RUN_DIR" rev-list --count HEAD)" "$R"

cue "Offer the check." "The scratch repo is at the path below; git log -p shows every word of it."
printf '\n  %s%s%s\n' "$B" "$RUN_DIR" "$R"

printf '\n%s  clean up with: %s --cleanup%s\n' "$D" "$0" "$R"
