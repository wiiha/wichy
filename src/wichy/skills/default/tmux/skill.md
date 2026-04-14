---
name: tmux
description: Remote-control tmux sessions for interactive CLIs and background processes by sending keystrokes and scraping pane output. Activate this skill whenever you need to run something in the background — using `&` for backgrounding processes in bash won't work in this environment.
metadata:
  tags: [tmux, shell, background, workflow]
---

# tmux Session Control

Control tmux sessions by sending keystrokes and reading output. Essential for managing interactive terminal applications and long-running background processes.

**When you need to run a process in the background, always use tmux.** The standard shell approach of appending `&` to a command does not work reliably in this environment — tmux sessions persist and can be interacted with.

## Named Sockets (Required)

**Always use named sockets with `-S <socket_path>`.** This ensures predictable access and avoids conflicts.

When running inside a Docker container, place the socket file in `/workspace`:

```bash
# Create session with named socket
tmux -S /workspace/tmux.sock new-session -d -s mysession

# All subsequent commands use the same socket
tmux -S /workspace/tmux.sock send-keys -t mysession "command" Enter
tmux -S /workspace/tmux.sock capture-pane -t mysession -p
```

When not in a docker container, then the default socket location can be used, i.e. you can omit the -S flag for specifying socket.

## When to Use

✅ **USE this skill when:**

- Running processes in the background that need to persist
- Sending input to interactive terminal applications
- Scraping output from long-running processes
- Navigating tmux panes/windows programmatically
- Checking on background work in existing sessions
- Managing multiple parallel tasks

❌ **DON'T use this skill when:**

- Running one-off shell commands → use `bash` tool directly
- Non-interactive scripts that complete quickly → use `bash` tool

## Common Commands

### List Sessions

```bash
tmux -S /workspace/tmux.sock list-sessions
tmux -S /workspace/tmux.sock ls
```

### Capture Output

```bash
# Last 20 lines of pane (may show mostly empty lines)
tmux -S /workspace/tmux.sock capture-pane -t shared -p | tail -20

# Entire scrollback (useful when pane has lots of blank lines)
tmux -S /workspace/tmux.sock capture-pane -t shared -p -S -

# Specific pane in window
tmux -S /workspace/tmux.sock capture-pane -t shared:0.0 -p

# Find specific content using grep (avoids empty-line noise)
tmux -S /workspace/tmux.sock capture-pane -t shared -p -S - | grep "pattern"
tmux -S /workspace/tmux.sock capture-pane -t shared -p -S - | grep -A2 "pattern"
```

**Tip:** `capture-pane -p` often returns lots of empty lines at the bottom. Use `-S -` for full scrollback and pipe through `grep` to find what you need.

### Send Keys

```bash
# Send text (doesn't press Enter)
tmux -S /workspace/tmux.sock send-keys -t shared "hello"

# Send text + Enter
tmux -S /workspace/tmux.sock send-keys -t shared "y" Enter

# Send special keys
tmux -S /workspace/tmux.sock send-keys -t shared C-c    # Ctrl+C
tmux -S /workspace/tmux.sock send-keys -t shared C-d    # Ctrl+D (EOF)
```

### Session Management

```bash
# Create new session
tmux -S /workspace/tmux.sock new-session -d -s mysession

# Kill session
tmux -S /workspace/tmux.sock kill-session -t mysession

# Rename session
tmux -S /workspace/tmux.sock rename-session -t old new
```

## Sending Input Safely

### Use Literal Mode for Commands with Special Characters

The `-l` (literal) flag sends text exactly as-is, avoiding tmux's interpretation of special characters. **Always use `-l --` when sending shell commands:**

```bash
# CORRECT: Use literal mode for shell commands
tmux -S /workspace/tmux.sock send-keys -t shared -l -- 'echo "Hello World"'
sleep 0.1 && tmux -S /workspace/tmux.sock send-keys -t shared Enter

# WRONG: Without -l, special characters may cause issues
tmux -S /workspace/tmux.sock send-keys -t shared 'echo "Hello!"' Enter  # ! triggers history expansion
```

### Why Literal Mode Matters

1. **Special characters** like `!` are interpreted by shells (history expansion)
2. **Literal mode** bypasses tmux's own key interpretation
3. **Predictable behavior** — what you send is exactly what gets typed

### Always Use Straight Quotes

When writing commands with quotes, ensure you're using **straight ASCII quotes** (0x22), not curly/smart quotes:

| Character | ASCII/Unicode  | Works in shell?                 |
| --------- | -------------- | ------------------------------- |
| `"`       | ASCII 0x22     | ✅ Yes                          |
| `"`       | Unicode U+201C | ❌ No (causes `dquote>` prompt) |
| `"`       | Unicode U+201D | ❌ No                           |

**Common pitfall:** Copying commands from chat interfaces may convert straight quotes to curly "smart quotes" for typography. This breaks shell commands silently — the shell sees an unclosed string.

**Do instead:**

- Type quotes manually when copying from chat
- Paste into a text editor first to verify quotes are straight
- Use the `-l` flag which helps but doesn't fix curly quotes in the original text

## Interactive Application Patterns

### Check if Application Needs Input

```bash
tmux -S /workspace/tmux.sock capture-pane -t task-3 -p | grep -E "\\[y/n\\]|proceed|permission"
```

### Respond to Prompts

```bash
# Simple response
tmux -S /workspace/tmux.sock send-keys -t task-3 'y' Enter

# With literal mode for safety
tmux -S /workspace/tmux.sock send-keys -t task-3 -l -- 'yes'
sleep 0.1 && tmux -S /workspace/tmux.sock send-keys -t task-3 Enter
```

### Check All Sessions Status

```bash
for s in shared task-2 task-3 task-4; do
  echo "=== $s ==="
  tmux -S /workspace/tmux.sock capture-pane -t $s -p -S - 2>/dev/null | grep -v "^$" | tail -5
done
```

## Multiplaying with the User

Tmux enables collaborative sessions where both you and the user can see and interact with the same terminal. This is useful for:

- Walking through something together in real-time
- Handing off interactive tasks to the user
- Debugging collaboratively with shared visibility

### Setup

```bash
# Create an attachable session
tmux -S /workspace/tmux.sock new-session -d -s collab

# User can attach from their terminal:
# tmux -S /workspace/tmux.sock attach -t collab
```

### Workflow

1. You run commands via `send-keys` while the user watches in their attached terminal
2. The user can type directly in their attached session
3. Both see the same output in real-time
4. To hand control back to user: just stop sending keys — they can interact directly

### Effective Pane Reading

When reading output in a multiplayer session, `capture-pane` often returns mostly blank lines. Use these patterns:

```bash
# Get everything and search for what matters
tmux capture-pane -t collab -p -S - | grep "search term"

# Get non-empty lines only
tmux capture-pane -t collab -p -S - | grep -v "^$" | tail -20

# Find context around a match
tmux capture-pane -t collab -p -S - | grep -B2 -A5 "error"
```

### Example Session Names

| Session           | Purpose                   |
| ----------------- | ------------------------- |
| `shared`          | Collaborative session     |
| `task-2`-`task-N` | Parallel background tasks |
| `repl`            | Interactive REPL session  |

## Notes

- Use `capture-pane -p` to print to stdout (essential for scripting)
- `-S -` captures entire scrollback history (useful when pane has blank lines)
- Pipe through `grep` to filter out empty lines and find specific content
- Target format: `session:window.pane` (e.g., `shared:0.0`)
- Sessions persist across SSH disconnects
- Always use `-l --` with `send-keys` for shell commands
- Verify quotes are straight ASCII before sending commands
