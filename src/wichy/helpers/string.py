def remove_tagged_content(text, tags):
    """
    Remove content between start and end tags from a string.

    Args:
        text (str): The input string to process
        tags (list): List of tuples (start_tag, end_tag) where each tuple contains
                     the opening and closing tags to search for

    Returns:
        str: The text with all tagged content removed

    Examples:
        >>> remove_tagged_content("Hello <div>unwanted</div> World", [("<div>", "</div>")])
        'Hello  World'

        >>> remove_tagged_content("Keep [remove] this", [("[", "]")])
        'Keep  this'
    """
    result = text

    for start_tag, end_tag in tags:
        while True:
            # Find the first occurrence of start tag
            start_idx = result.find(start_tag)

            # If start tag not found, move to next tag pair
            if start_idx == -1:
                break

            # Find the first occurrence of end tag after the start tag
            end_idx = result.find(end_tag, start_idx + len(start_tag))

            # If end tag not found, move to next tag pair
            if end_idx == -1:
                break

            # Remove everything from start_tag to end_tag (inclusive)
            result = result[:start_idx] + result[end_idx + len(end_tag) :]

    return result


UNWANTED_TAGS = [("<think>", "</think>")]


def strip_thinking_content(content: str):
    return str(remove_tagged_content(content, UNWANTED_TAGS)).strip()


def truncate_to_len(text: str, new_len=50, suffix="...(truncated)") -> str:
    if len(text) < new_len:
        return text

    return text[:new_len] + suffix


# Example usage and tests
if __name__ == "__main__":
    # Test 1: Simple HTML-style tags
    text1 = "Hello <div>unwanted content</div> World"
    print(f"Test 1: {remove_tagged_content(text1, [('<div>', '</div>')])}")

    # Test 2: Multiple different tag pairs
    text2 = "Keep this [remove this] and this {also remove} but keep this"
    print(f"Test 2: {remove_tagged_content(text2, [('[', ']'), ('{', '}')])}")

    # Test 3: Multiple occurrences of the same tag pair
    text3 = "Start <!-- comment 1 --> middle <!-- comment 2 --> end"
    print(f"Test 3: {remove_tagged_content(text3, [('<!--', '-->')])}")

    # Test 4: Nested tags
    text4 = "Before <outer>text <inner>nested</inner> more</outer> after"
    print(f"Test 4: {remove_tagged_content(text4, [('<outer>', '</outer>')])}")

    # Test 5: Tag pair not found (unclosed tag)
    text5 = "Hello <div>unclosed tag World"
    print(f"Test 5: {remove_tagged_content(text5, [('<div>', '</div>')])}")

    # Test 6: Multiple tag pairs, some present, some not
    text6 = "Text [remove] with {keep this} various tags"
    print(f"Test 6: {remove_tagged_content(text6, [('[', ']'), ('(', ')')])}")
