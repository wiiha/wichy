skill_template = """---
name: {skill_name}
description: >
  What it does + when to use it + key capabilities. Include trigger phrases.
  Example: "Analyzes Figma files and generates handoff docs. Use when user
  uploads .fig files, asks for 'design specs' or 'design-to-code handoff'."
# safe_scripts: (optional) comma-separated script names that can run without verification
metadata:
  tags: [<comma-separated tags for searching>]
---

# {skill_name}

<Brief description of the skill's purpose and how the agent should use it. Keep concise — move detailed reference material to references/.>

## Workflow

<Step-by-step workflow with dependencies.>

### Step 1: <First Major Step>

<Explanation and example command.>

```bash
./scripts/example.sh --arg value
```

Expected output: <what success looks like>

### Step 2: <Second Major Step>

<Continue for each step...>

## Examples

### Example 1: <Common scenario>

**User says:** "<trigger phrase>"

**Actions:**
1. <First action>
2. <Second action>

**Result:** <What the user gets>

## Available Scripts

<Describe each script in scripts/ directory.>

### script_name.sh

**Description:** <What it does>

**Usage:** `./scripts/script_name.sh [args]`

**Arguments:**
- `arg1`: <description>

## Notes

> CRITICAL: <Non-negotiable requirements or validations.>

<Additional notes. For detailed reference material, place in references/ and link here.>

<!-- Optional: Add safe_scripts to frontmatter if scripts should run without verification:
safe_scripts:
  - script1.sh
  - script2.sh
-->
"""
