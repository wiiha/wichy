import re
from typing import Dict, List

from wichy.helpers.gen_id import gen_id

TAG_PAIRS = [
    ("<conditional>", "</conditional>"),
    ("<condition>", "</condition>"),
]


def _process_conditional(conditional: str, verify_against: Dict[str, List[str]]):

    if len(verify_against) == 0:
        return ""

    c_start = conditional.find(TAG_PAIRS[1][0])
    c_end = conditional.find(TAG_PAIRS[1][1])
    if c_start == -1 or c_end == -1:
        raise ValueError("could not find condition tag within conditional")

    c_start = c_start + len(TAG_PAIRS[1][0])

    x = conditional[c_start:c_end]
    if x.strip() == "":
        raise ValueError("condition block is empty")

    pattern = re.compile(r"<([a-zA-Z0-9_-]+)>\s*(.*?)\s*<\/\1>", re.DOTALL)
    m = pattern.search(x)
    if m is None:
        raise ValueError("could not find tag in condition block")
    tag_name = m.group(1)
    tag_value = m.group(2)

    if not verify_against.get(tag_name, None):
        tag_name += "s"

    if not verify_against.get(tag_name, None):
        return ""

    if tag_value not in verify_against.get(tag_name, []):
        return ""

    # we know that the condition is met, let us extract the information to return.

    x = (
        conditional[c_end + len(TAG_PAIRS[1][1]) :]
        .removesuffix(TAG_PAIRS[0][1])
        .strip()
    )
    return x


def preprocess_prompt(prompt: str, verify_against: Dict[str, List[str]]) -> str:
    """
    Process a prompt containing conditional blocks and include/exclude content based on conditions.

    Conditional blocks are defined as:
    <conditional>
    <condition>
    <tag>value</tag>
    </condition>
    content to include if condition is met
    <conditional>

    :param prompt: Prompt that may contain <conditional> tags
    :type prompt: str
    :param verify_against: Dict containing present conditions that can be matched.
                           Key is tag name, value List is allowed content for that tag.
    :type verify_against: Dict[str, List[str]]
    :return: prompt where conditional parts are included or left out based on the condition.
    :rtype: str
    """

    for pair in TAG_PAIRS:
        start = pair[0]
        end = pair[1]
        if prompt.count(start) != prompt.count(end):
            raise ValueError(f"count mismatch for {pair}")

    conditionals: Dict[str, str] = {}

    six = 0
    eix = 0
    modified_prompt = prompt
    start = TAG_PAIRS[0][0]
    end = TAG_PAIRS[0][1]

    while True:
        six = modified_prompt.find(start)
        if six == -1:
            break

        eix = modified_prompt.find(end, six)
        if eix == -1:
            raise ValueError(f"expected to find {end}, found nothing.")

        eix = eix + len(end)

        if eix < len(modified_prompt) and modified_prompt[eix] == "\n":
            eix += 1

        conditional = modified_prompt[six:eix]
        conditional = conditional.strip()
        cid = "##" + gen_id() + "##"
        conditionals[cid] = conditional
        modified_prompt = modified_prompt[:six] + cid + modified_prompt[eix:]

    for cid in conditionals:

        conditional = conditionals[cid]

        result = _process_conditional(
            conditional=conditional, verify_against=verify_against
        )
        ix = modified_prompt.find(cid)
        modified_prompt = (
            modified_prompt[:ix] + result + modified_prompt[ix + len(cid) :]
        )

    return modified_prompt
