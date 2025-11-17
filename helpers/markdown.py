from typing import Dict, Tuple


def read_markdown_with_frontmatter(markdown_string: str) -> Tuple[Dict[str, str], str]:
    """
    Read a markdown string and extract frontmatter and content.
    This function does not parse the actual markdown content but
    instead returns it as is.

    Args:
        markdown_string: a string containing markdown text.

    Returns:
        Tuple of (frontmatter_dict, markdown_content)
    """
    content = markdown_string.strip()

    # Check if file starts with frontmatter delimiter
    if not content.startswith("---"):
        return {}, content

    # Split on the closing delimiter
    parts = content.split("---", 2)

    if len(parts) < 3:
        return {}, content

    # Parse frontmatter (YAML-style key: value pairs)
    frontmatter = {}
    frontmatter_text = parts[1].strip()

    for line in frontmatter_text.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()

    # Get the markdown content (everything after second ---)
    markdown_content = parts[2].strip()

    return frontmatter, markdown_content


# Example usage
if __name__ == "__main__":
    # Example: Create a sample markdown file
    sample_content = """---
title: My Blog Post
author: John Doe
date: 2024-01-15
tags: python, markdown
---

# Hello World

This is the actual markdown content.

## Section 1
Some text here."""

    # Read it back
    frontmatter, content = read_markdown_with_frontmatter(sample_content)

    print("Frontmatter:")
    for key, value in frontmatter.items():
        print(f"  {key}: {value}")

    print("\nMarkdown Content:")
    print(content)
