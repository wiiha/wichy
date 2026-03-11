skill_template = """---
name: {skill_name}
description: <Short description of what this skill enables the agent to do>
tags: <comma-separated tags for searching>
safe_scripts: <comma-separated script names that can run without verification, or leave empty>
---

# {skill_name}

<Describe the skill, its purpose, and how the agent should use it. Include any important context about the environment or tools this skill relates to.>

## Available Scripts

<List and describe each script in the scripts/ directory. Include what the script does, its arguments, and example usage.>

### script_name.sh

**Description:** <What this script does>

**Usage:**
```bash
./scripts/script_name.sh [args]
```

**Arguments:**
- `arg1`: <description>
- `arg2`: <description>

**Example:**
```bash
./scripts/script_name.sh --help
```

## Notes

<Any additional notes, caveats, or best practices for using this skill.>
"""