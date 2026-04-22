# Bash Exit Status

## Overview

BashTool.execute() currently returns only `result.stdout`, discarding the `returncode`. This means failed commands silently appear successful, and successful commands with no output produce an empty string with no confirmation.

## File to Modify

**`src/wichy/tools/bash.py`**

## Change Details

**Location:** Line 326 (the `return result.stdout` line at the end of `execute()`)

**Current code:**
```python
return result.stdout
```

**Replace with:**
```python
output = result.stdout
if result.returncode != 0:
    # Command failed — always show exit code
    if output:
        return output + f"\n[exit code: {result.returncode}]"
    else:
        return f"[exit code: {result.returncode}]"
else:
    # Command succeeded — show exit code only if no output
    if output:
        return output
    else:
        return f"[exit code: 0]"
```

## Behavior

| Return code | Has output | Result |
|-------------|-----------|--------|
| 0 (success) | Yes | Just the output (no exit code shown) |
| 0 (success) | No | `[exit code: 0]` |
| Non-zero (failure) | Yes | Output + `[exit code: N]` appended |
| Non-zero (failure) | No | `[exit code: N]` |

## Size

Trivial — ~8 lines replacing 1 line.