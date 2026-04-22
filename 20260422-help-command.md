# `/help` Slash Command

## Overview

There are 8 existing slash commands but no `/help` command. Add `/help` that displays a Rich Table listing all commands with descriptions, or details for a specific command when given an argument (e.g., `/help /reset`).

## File to Modify

**`src/wichy/slash_commands.py`**

## Change Details

### 1. Add `handle_help` closure in `__init__()` (after `handle_hooks` around line 156)

```python
def handle_help(line: str) -> str | None:
    """Handle /help - show available commands."""
    from rich.table import Table

    target = line.strip().split(maxsplit=1)
    if len(target) > 1 and target[1].startswith("/"):
        # Specific command help: /help /reset
        cmd = target[1].lower()
        desc = self._descriptions.get(cmd, "No description available.")
        return f"[bold]{cmd}[/bold]: {desc}"

    table = Table(title="Wichy Commands", show_header=True, header_style="bold cyan")
    table.add_column("Command", style="cyan")
    table.add_column("Description")

    for cmd, desc in sorted(self._descriptions.items()):
        table.add_row(cmd, desc)

    return table
```

### 2. Add `_descriptions` dict (after the `_handlers` dict around line 167)

```python
self._descriptions: dict[str, str] = {
    "/btw": "One-shot sandboxed question (carries recent context)",
    "/exit": "Exit the REPL",
    "/logging": "Toggle logging on/off (or show current state)",
    "/reset": "Nuke the entire conversation context",
    "/compact": "Summarize and compact the conversation context",
    "/drop": "Drop the last context entry",
    "/status": "Show current token count and auto-compact threshold",
    "/hooks": "Reload and list all registered hooks",
    "/help": "Show this help message",
}
```

### 3. Add to `_handlers` dict (line ~166)

```python
"/help": handle_help,
```

### 4. Add to `NestedCompleter.from_nested_dict` dict (around line 182)

```python
"/help": None,
```

## Compatibility Notes

- The handler returns a Rich `Table` (for general `/help`) or a `str` (for `/help /command`). Both are already handled by `repl.py` lines 67-69 which use `user_console.print()` — it can print both Rich Table and strings.
- The `/hooks` command already returns a Rich Table, proving this pattern works.
- `_descriptions` is a separate dict from `_handlers` — commands self-document explicitly, not by introspecting handler closures.

## Size

Small — 1 file: `slash_commands.py`