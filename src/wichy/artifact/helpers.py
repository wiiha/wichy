from .artifact import Artifact, ArtifactReference
from wichy.helpers.context import ContextHandler
from wichy.llm_backend import call as call_llm
from rich.console import Console


console = Console(quiet=True)


INSTRUCTION_COMPARE_LONG = """You are a classification assistant that compares two artifact descriptions and decides whether the first could be a later version of the second.

- Output exactly one line containing two fields separated by a single pipe character: a verdict and a confidence score. Format: VERDICT|CONFIDENCE
  - VERDICT must be exactly `yes` or `no` (lowercase).
  - CONFIDENCE must be a decimal number between 0.00 and 1.00 with exactly two digits after the decimal (e.g., 0.85).
  - Example valid outputs: `yes|0.92` or `yes|0.30` or `no|0.40` or `no|0.85`.
- Base your judgment only on the textual information provided (title, short description, content summary, version numbers if present, and any other supplied fields). Ignore timestamps and IDs unless they appear informative in the text itself.
- Answer `yes` when the first artifact plausibly represents a revision, update, or superseding iteration of the second (updated scope, clarified wording, added/corrected details, same topic/purpose).
- Answer `no` when the artifacts are unrelated in topic, clearly different deliverables, or when differences indicate they are not iterative.
- Choose confidence reflecting how strongly the text supports the verdict: low (0.00-0.39), medium (0.40-0.74), high (0.75-1.00).
- If ambiguous but reasonably could be a later version, prefer `yes` only when there is clear topical or purpose overlap; otherwise prefer `no`.
- Do not output any extra text, punctuation, explanations, or newlines beyond the single VERDICT|CONFIDENCE line.
"""

INSTRUCTION_COMPARE_SHORT = """You are a classifier comparing two artifact descriptions to decide if the first could be a later version of the second.

- Output exactly one line: VERDICT|CONFIDENCE
  - VERDICT: `yes` or `no` (lowercase)
  - CONFIDENCE: decimal 0.00-1.00 with two digits (e.g., 0.85)
- Base judgment on the provided text (title, description, content summary, version if present); ignore timestamps/IDs unless informative.
- `yes` = plausible revision/update/superseding iteration; `no` = unrelated or different deliverable.
- Confidence bands: low 0.00-0.39, medium 0.40-0.74, high 0.75-1.00.
- If ambiguous, prefer `yes` only with clear topical/purpose overlap; otherwise `no`.
- Do not add any other text, punctuation, or newlines."""


INSTRUCTION_COMPARE_MULTIPLE = """You are a classifier comparing one new artifact to multiple candidate earlier artifacts to pick the single best candidate that the new artifact could be a later version of.

- Output exactly one line: CANDIDATE_ID|CONFIDENCE
  - CANDIDATE_ID: the id of the chosen candidate (as given in the prompt). If no candidate is a plausible earlier version, output exactly `no_match`. An ID has format `artifact_xxxx`.
  - CONFIDENCE: decimal between 0.00 and 1.00 with two digits (e.g., 0.85).
- Base your judgment only on the textual information provided (title, short description, content summary, version if present). Ignore timestamps and raw IDs except that candidate IDs must be returned verbatim when selected.
- Choose the candidate that is most plausibly an earlier iteration (revision, update, or superseding iteration). If multiple candidates are similar, pick the single best match.
- Confidence bands: low 0.00-0.39, medium 0.40-0.74, high 0.75-1.00. Reflect how strongly the text supports the chosen candidate.
- If none are plausible, return `no_match|CONFIDENCE` where CONFIDENCE reflects how sure you are that there is no match.
- Do not output any other text, punctuation, explanations, or newlines beyond the single CANDIDATE_ID|CONFIDENCE line."""

INSTRUCTION_COMPARE_MULTIPLE_WITH_MOTIVATION = """You are a classifier comparing one new artifact to multiple candidate earlier artifacts to pick the single best candidate that the new artifact could be a later version of.

- Output exactly one line: CANDIDATE_ID|CONFIDENCE|ONE_LINE_MOTIVATION
  - CANDIDATE_ID: the id of the chosen candidate (as given in the prompt). If no candidate is a plausible earlier version, output exactly `no_match`. An ID has format `artifact_xxxx`.
  - CONFIDENCE: decimal between 0.00 and 1.00 with two digits (e.g., 0.85).
  - ONE_LINE_MOTIVATION: a single short phrase (max 10 words) explaining the main reason for the choice (topic overlap, clarified scope, added detail, corrected error, etc.). No punctuation that contains the pipe character.
- Base your judgment only on the textual information provided (title, short description, content summary, version if present). Ignore timestamps and raw IDs except that candidate IDs must be returned verbatim when selected.
- Choose the candidate that is most plausibly an earlier iteration (revision, update, or superseding iteration). If multiple candidates are similar, pick the single best match.
- Confidence bands: low 0.00-0.39, medium 0.40-0.74, high 0.75-1.00. Reflect how strongly the text supports the chosen candidate.
- If none are plausible, return `no_match|CONFIDENCE|REASON` where REASON is a one-line explanation such as `different topic` or `insufficient overlap`.
- Do not output any other text, punctuation, or newlines beyond the single CANDIDATE_ID|CONFIDENCE|ONE_LINE_MOTIVATION line."""


def score_candidate_for_artifact(a: Artifact, candidate: Artifact):
    ctx = ContextHandler(custom_suffix="compare_one_to_one", sub_dir="artifact_store")
    ctx.add(
        role="system",
        content=(INSTRUCTION_COMPARE_LONG.strip()),
    )

    a_str = ArtifactReference.from_artifact(a).format_for_prompt()
    candidate_str = ArtifactReference.from_artifact(candidate).format_for_prompt()

    user_msg = (
        "# new version\n\n"
        + a_str
        + "\n\n===\n\n# possibly earlier version\n\n"
        + candidate_str
        + "\n\n===\n\nyes or no?"
    )

    ctx.add(role="user", content=user_msg)

    model_str = "ollama/hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"

    res = call_llm(context=ctx(), model_str=model_str)
    ctx.append({"role": "assistant", "content": res.content})

    c = res.content
    c = c.strip().lower()
    ps = c.split("|")
    answer = False
    if "yes" in ps[0]:
        answer = True
    confidence = float(ps[1])

    print(f"{c} \n\t{a_str}\n\t{candidate_str}")

    return (candidate.id, answer, confidence)


def select_candidate_for_artifact(a: Artifact, candidates: list[Artifact]):
    ctx = ContextHandler(custom_suffix="compare_one_to_many", sub_dir="artifact_store")
    ctx.add(
        role="system",
        content=(INSTRUCTION_COMPARE_MULTIPLE_WITH_MOTIVATION.strip()),
    )

    a_str = ArtifactReference.from_artifact(a).format_for_prompt()

    user_msg = (
        "# new artifact\n\n"
        + a_str
        + "\n\n===\n\n# candidates for earlier versions\n\n"
    )

    for candidate in candidates:
        c_str = ArtifactReference.from_artifact(candidate).format_for_prompt()
        user_msg += c_str + "\n"

    ctx.add(role="user", content=user_msg)

    model_str = "ollama/hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"

    res = call_llm(context=ctx(), model_str=model_str, temperature=0.0)
    ctx.append({"role": "assistant", "content": res.content})

    c = res.content
    # expect response to have format <ID>|<CONFIDENCE>
    c = c.strip().lower()
    ps = c.split("|")
    winner_cid = ps[0]
    confidence = float(ps[1])
    motivation = ps[2]

    if winner_cid == "no_match":
        winner_cid = ""

    return (winner_cid, confidence, motivation)


def artifact_list_to_prompt_format(artifact_list: list[Artifact]) -> str:
    out_str = ""
    for a in artifact_list:
        s = ArtifactReference.from_artifact(a).format_for_prompt()
        out_str += s + "\n"

    return out_str


INSTRUCTION_PROMPT_TO_ARTIFACTS = """You are a selector that chooses zero or more artifact IDs from the supplied candidate list that are relevant to the given user prompt. Relevance means an artifact would help an agent produce a correct, complete, or contextually appropriate response to the prompt.

- Output exactly one line containing zero or more candidate IDs separated by a single pipe character: ID|ID|ID...
  - IDs must be returned verbatim as they appear in the candidate list. Format for an ID is `artifact_xxx`.
  - If no candidates are relevant, output exactly `no_match`.
- Base relevance only on the textual information provided for each artifact (title, short description, content summary, version if present) and the prompt text. Ignore timestamps and internal storage metadata.
- Consider the prompt recipient (if supplied) when judging relevance (e.g., different audience or role may change which artifacts are useful).
- Prefer precision: include an artifact only if it meaningfully helps address the prompt (background, plan, data, or prior decisions). Do not include duplicates or near-duplicates that add no new value.
- Order IDs by descending relevance (most relevant first).
- If multiple artifacts together are needed (complementary pieces), include all that are useful.
- Do not output any extra text, punctuation, newline, or commentary—only the single pipe-separated line (or `no_match`)."""


def select_artifacts_for_prompt(
    prompt: str, recipient: str, candidates: list[Artifact]
) -> list[str]:
    ctx = ContextHandler(custom_suffix="prompt_to_artifacts", sub_dir="artifact_store")
    ctx.add(
        role="system",
        content=(INSTRUCTION_PROMPT_TO_ARTIFACTS.strip()),
    )

    user_msg = ""

    if recipient.strip() != "":
        user_msg += "PROMPT RECIPIENT: " + recipient + "\n"

    user_msg += "### PROMPT START\n\n"
    user_msg += prompt.strip() + "\n\n"
    user_msg += "### PROMPT END\n\n"

    user_msg += "### ARTIFACT CANDIDATES START\n\n"
    for candidate in candidates:
        c_str = ArtifactReference.from_artifact(candidate).format_for_prompt()
        user_msg += c_str + "\n"
    user_msg += "\n\n### ARTIFACT CANDIDATES END"

    ctx.add(role="user", content=user_msg)

    model_str = "ollama/hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"

    res = call_llm(context=ctx(), model_str=model_str)
    ctx.append({"role": "assistant", "content": res.content})

    c = res.content
    # expect response to have format <ID>|<ID>|<ID>...
    c = c.strip().lower()
    if c == "no_match":
        return []

    ps = c.split("|")

    return ps
