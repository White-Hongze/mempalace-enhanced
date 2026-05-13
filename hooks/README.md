# MemPalace Hooks — SessionStart Autosave

This folder now uses a single hook: `SessionStart`.

When a new chat session starts, the hook tries to ingest the previous session transcript automatically.

## Files

- `mempal_sessionstart_hook.sh`: Main hook logic (Linux/macOS + Git Bash on Windows)
- `mempal_sessionstart_hook.ps1`: Windows wrapper that locates Git Bash and forwards stdin
- `mempalace.windows.json`: VS Code Copilot template for Windows
- `mempalace.linux.json`: VS Code Copilot template for Linux
- `mempalace.macos.json`: VS Code Copilot template for macOS

## Setup

1. Pick the template for your OS.
2. Edit its `command` path to match your local repo location.
3. Register it in your Copilot hook config.

Template note:

- Current template commands use example absolute paths (for example `~/dev/mempalace/...` or `D:\\dev\\mempalace\\...`). Update them for your machine.

Linux/macOS permission:

```bash
chmod +x hooks/mempal_sessionstart_hook.sh
```

## How It Works

1. Reads current hook payload from stdin (session id + transcript path).
2. Loads `~/.mempalace/hook_state/last_session_meta`.
3. If it detects a different previous session with a valid transcript file, it:
   - copies the transcript into a staging folder,
   - runs `mempalace mine <stage_dir> --mode convos`.
4. Writes current session metadata back to `last_session_meta`.
5. Returns a short JSON `systemMessage` with result (`autosaved`, `skipped`, or `failed`).

## Runtime Requirements

- Python available (`.venv` preferred, then `python3`/`python` fallback)
- On Windows: Git Bash available (`C:\Program Files\Git\bin\bash.exe` preferred)

If runtime requirements are missing, the hook exits gracefully with a skip message.

## Debugging

Check log file:

```bash
cat ~/.mempalace/hook_state/hook.log
```

Useful state files:

- `~/.mempalace/hook_state/last_session_meta`
- `~/.mempalace/hook_state/ingest_stage/`
