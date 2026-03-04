import re


def estimate_tokens(text: str) -> int:
    # Count punctuation/symbols (usually 1 token each)
    symbols = len(re.findall(r"[{}()\[\];,<>|&^%$#@!=+\-*/\\:`~]", text))

    # Count whitespace-delimited words (rough word token estimate)
    words = text.split()
    word_tokens = sum(1 if len(w) <= 4 else 2 if len(w) <= 10 else 3 for w in words)

    # Paths and slashes add overhead
    path_overhead = len(re.findall(r"[/\\.]", text))

    return int((word_tokens + symbols + path_overhead) * 0.8)
