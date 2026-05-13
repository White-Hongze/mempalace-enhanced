#!/bin/bash
# MEMPALACE SESSION-START HOOK — Autosave previous session when a new window starts

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

STATE_DIR="$HOME/.mempalace/hook_state"
mkdir -p "$STATE_DIR"

PYTHON_CMD=""
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON_CMD="$REPO_ROOT/.venv/bin/python"
elif [ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]; then
    PYTHON_CMD="$REPO_ROOT/.venv/Scripts/python.exe"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [ -z "$PYTHON_CMD" ]; then
    echo '{"systemMessage":"SessionStart hook skipped: python not found."}'
    exit 0
fi

INPUT=$(cat)
INPUT_B64=$(printf '%s' "$INPUT" | base64 | tr -d '\n')

PARSED_VARS=$(HOOK_INPUT_B64="$INPUT_B64" "$PYTHON_CMD" - <<'PYEOF'
import base64
import json
import os
import re

blob = base64.b64decode(os.environ.get("HOOK_INPUT_B64", ""))
try:
    raw = blob.decode("utf-8")
except UnicodeDecodeError:
    raw = blob.decode("gbk", errors="replace")
data = json.loads(raw or "{}")
sid = data.get("session_id") or data.get("sessionId", "unknown")
tp = (data.get("transcript_path") or data.get("transcriptPath", "")).replace("\\", "/")

safe = lambda s: re.sub(r"[^a-zA-Z0-9_/:.\-~]", "", str(s))

print(f'CURRENT_SESSION_ID="{safe(sid)}"')
print(f'CURRENT_TRANSCRIPT_PATH="{safe(tp)}"')
PYEOF
2>/dev/null)
eval "$PARSED_VARS"

CURRENT_TRANSCRIPT_PATH="${CURRENT_TRANSCRIPT_PATH/#\~/$HOME}"
LAST_META_FILE="$STATE_DIR/last_session_meta"

MESSAGE=""

if [ -f "$LAST_META_FILE" ]; then
    LAST_META=$(cat "$LAST_META_FILE")
    LAST_SESSION_ID="${LAST_META%%|*}"
    LAST_TRANSCRIPT_PATH="${LAST_META#*|}"

    if [ -n "$LAST_SESSION_ID" ] && [ "$LAST_SESSION_ID" != "$CURRENT_SESSION_ID" ]; then
        if [ -f "$LAST_TRANSCRIPT_PATH" ]; then
            USER_COUNT=$("$PYTHON_CMD" - "$LAST_TRANSCRIPT_PATH" <<'PYEOF'
import json
import sys

count = 0
with open(sys.argv[1], encoding="utf-8", errors="ignore") as f:
    for line in f:
        try:
            entry = json.loads(line)
            # VS Code transcript format
            if entry.get("type") == "user.message":
                count += 1
                continue

            # Legacy Claude/Codex transcript format
            msg = entry.get("message", {})
            if isinstance(msg, dict) and msg.get("role") in ("user", "human"):
                count += 1
        except Exception:
            pass
print(count)
PYEOF
2>/dev/null)

            if [ "${USER_COUNT:-0}" -gt 0 ]; then
                STAGE_DIR="$STATE_DIR/sessionstart_stage/$LAST_SESSION_ID"
                mkdir -p "$STAGE_DIR"
                STAGE_FILE="$STAGE_DIR/$(basename "$LAST_TRANSCRIPT_PATH")"
                cp "$LAST_TRANSCRIPT_PATH" "$STAGE_FILE" 2>/dev/null

                if [ -n "$PYTHON_CMD" ] && (cd "$REPO_ROOT" && PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "$PYTHON_CMD" -m mempalace mine "$STAGE_DIR" --mode convos >> "$STATE_DIR/hook.log" 2>&1); then
                    MESSAGE="SessionStart autosaved previous session $LAST_SESSION_ID from transcript file"
                    echo "[$(date '+%H:%M:%S')] $MESSAGE" >> "$STATE_DIR/hook.log"
                else
                    MESSAGE="SessionStart failed to autosave previous session $LAST_SESSION_ID from transcript file"
                    echo "[$(date '+%H:%M:%S')] $MESSAGE" >> "$STATE_DIR/hook.log"
                fi
            else
                MESSAGE="SessionStart skipped autosave for previous session $LAST_SESSION_ID (no user messages)"
                echo "[$(date '+%H:%M:%S')] $MESSAGE" >> "$STATE_DIR/hook.log"
            fi
        else
            MESSAGE="SessionStart skipped autosave for previous session $LAST_SESSION_ID (transcript missing)"
            echo "[$(date '+%H:%M:%S')] $MESSAGE" >> "$STATE_DIR/hook.log"
        fi
    fi
fi

echo "$CURRENT_SESSION_ID|$CURRENT_TRANSCRIPT_PATH" > "$LAST_META_FILE"

if [ -n "$MESSAGE" ]; then
    "$PYTHON_CMD" - <<'PYEOF' "$MESSAGE"
import json
import sys

print(json.dumps({"systemMessage": sys.argv[1]}))
PYEOF
else
    echo "{}"
fi
