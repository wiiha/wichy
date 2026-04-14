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
# Last 20 lines of pane
tmux -S /workspace/tmux.sock capture-pane -t shared -p | tail -20

# Entire scrollback
tmux -S /workspace/tmux.sock capture-pane -t shared -p -S -

# Specific pane in window
tmux -S /workspace/tmux.sock capture-pane -t shared:0.0 -p
```

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

For interactive TUIs, split text and Enter into separate sends to avoid paste/multiline edge cases:

```bash
tmux -S /workspace/tmux.sock send-keys -t shared -l -- "long text here"
sleep 0.1 && tmux -S /workspace/tmux.sock send-keys -t shared Enter
```

## Interactive Application Patterns

### Check if Application Needs Input

```bash
tmux -S /workspace/tmux.sock capture-pane -t task-3 -p | tail -10 | grep -E "\\[y/n\\]|proceed|permission"
```

### Respond to Prompts

```bash
tmux -S /workspace/tmux.sock send-keys -t task-3 'y' Enter
```

### Check All Sessions Status

```bash
for s in shared task-2 task-3 task-4; do
  echo "=== $s ==="
  tmux -S /workspace/tmux.sock capture-pane -t $s -p 2>/dev/null | tail -5
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

### Example Session Names

| Session           | Purpose                   |
| ----------------- | ------------------------- |
| `shared`          | Collaborative session     |
| `task-2`-`task-N` | Parallel background tasks |
| `repl`            | Interactive REPL session  |

## Notes

- Use `capture-pane -p` to print to stdout (essential for scripting)
- `-S -` captures entire scrollback history
- Target format: `session:window.pane` (e.g., `shared:0.0`)
- Sessions persist across SSH disconnects
