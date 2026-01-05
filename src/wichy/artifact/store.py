from .artifact import Artifact, ArtifactReference
from .helpers import (
    artifact_list_to_prompt_format,
    score_candidate_for_artifact,
    select_candidate_for_artifact,
    console,
)
from typing import Optional, List, Tuple
from datetime import datetime
from collections import Counter
from rapidfuzz.distance.DamerauLevenshtein import (
    normalized_similarity as rapidfuzz_normalized_similarity,
)


class ArtifactStore:
    """
    Storage for artifacts with versioning.

    Features:
    - Immutable artifacts with supersession chains
    - LLM-based deduplication for similar artifacts
    - Version management (latest vs specific)
    - Lightweight references for discovery
    """

    def __init__(self):
        """
        Initialize artifact store.
        """
        self.artifacts: dict[str, Artifact] = {}

    def add(self, a: Artifact):
        # call _find_possible_previous_version and see if there is any matches
        print(f"-> {a.id} {a.title}: {a.description}")
        possible = self._find_possible_previous_version(a)

        if len(possible) == 0:
            # no candidates, just add
            self.artifacts[a.id] = a
            return

        candidates: list[Artifact] = []
        for cid in possible:
            candidate = self.get(cid)
            candidates.append(candidate)
        del candidate
        del cid

        cid, confidence, motivation = select_candidate_for_artifact(
            a=a, candidates=candidates
        )
        if cid == "":
            # LLM deemed no candidates were viable, just add
            self.artifacts[a.id] = a
            return
        sel_c = self.get(cid)

        if sel_c is None:
            console.log(
                f"[yellow]warning[/yellow] LLM returned artifact id [green]{cid}[/green], which did not have a match, this is unexpected. Will add artifact as new artifact, but there might be a previous version.",
                log_locals=True,
            )
            self.artifacts[a.id] = a
            return

        a_str = ArtifactReference.from_artifact(a).format_for_prompt()
        c_str = ArtifactReference.from_artifact(sel_c).format_for_prompt()

        console.log(
            f"Updating artifact\n{c_str}\nreplaced by\n{a_str}\n---\n[green]reson:[/green] {motivation}\n[green]confidence:[/green] {confidence}."
        )

        # candidate selected by LLM and it was a valid ID.

        self.artifacts[a.id] = a.model_copy(update={"version": sel_c.version + 1})
        self.artifacts[sel_c.id] = sel_c.model_copy(update={"replaced_by": a.id})
        return

    def _normalize_text(self, s: str) -> str:
        s = s or ""
        s = s.strip()
        s = " ".join(s.split())  # collapse whitespace
        return s.lower()

    def _comparison_string(self, a: Artifact) -> str:
        # return self._normalize_text(f"{a.title}\n\n{a.description}\n\n{a.content}")
        return self._normalize_text(f"{a.title}\n\n{a.description}")

    def _levenshtein_ratio(self, a: str, b: str) -> float:
        """Return normalized similarity in [0,1]. Prefer rapidfuzz if available."""
        if not a and not b:
            return 1.0
        return rapidfuzz_normalized_similarity(a, b)

    def _jaccard_tokens(self, a: str, b: str) -> float:
        """Jaccard similarity over whitespace tokens after simple normalization."""
        ta = set(self._normalize_text(a).split())
        tb = set(self._normalize_text(b).split())
        if not ta and not tb:
            return 1.0
        if not ta or not tb:
            return 0.0
        inter = ta.intersection(tb)
        union = ta.union(tb)
        return len(inter) / len(union)

    def _find_possible_previous_version(self, a: Artifact) -> list[str]:
        """
        Identify possible previous version(s) for artifact `a`
        using similarity measures. For now Levenshtein and Jaccard tokens.

        :param a: artifact for which to find candidates for previous version.
        :type a: Artifact
        :returns: List of string ids for candidates. Empty list means no candidates were found.
        :rtype: list[str]
        """
        s_in = self._comparison_string(a)
        all_latest = self.all_latest()
        if not all_latest:
            return []

        candidates: List[Tuple[str, float]] = []

        # Weights and thresholds (tune as needed)
        LEV_WEIGHT = 0.7
        JACCARD_WEIGHT = 0.3
        MIN_LEV_SKIP = 0.20  # skip if very low similarity
        MIN_JACCARD_SKIP = 0.10
        CLEAR_SCORE_SINGLE = 0.92
        CLEAR_SCORE_GAP = 0.12

        for candidate in all_latest:
            if candidate.id == a.id:
                continue

            s_cand = self._comparison_string(candidate)

            lev = self._levenshtein_ratio(s_in, s_cand)  # 0..1
            jac = self._jaccard_tokens(s_in, s_cand)  # 0..1

            # skip clearly unrelated items
            if lev < MIN_LEV_SKIP and jac < MIN_JACCARD_SKIP:
                continue

            score = (LEV_WEIGHT * lev) + (JACCARD_WEIGHT * jac)
            candidates.append((candidate.id, score))

        if len(candidates) < 1:
            return []

        # sort descending
        candidates.sort(key=lambda x: x[1], reverse=True)

        top_id, top_score = candidates[0]
        if len(candidates) == 1:
            return [top_id]

        second_score = candidates[1][1]
        if (
            top_score >= CLEAR_SCORE_SINGLE
            or (top_score - second_score) >= CLEAR_SCORE_GAP
        ):
            return [top_id]

        # return top N (limit to 5) for LLM decision
        top_n = [cid for cid, _ in candidates[:5]]
        return top_n

    def get(self, id: str):
        """
        get artifact by id will fetch the artifact with that exact id.

        :param id: id of artifact to retrieve.
        :type id: str
        :returns: Artifact object or None if not found.
        :rtype: Optional[Artifact]
        """
        return self.artifacts.get(id, None)

    def get_latest(self, id: str):
        """
        Fetches the latest version of an artifact.

        :param id: Id for one artifact in the chain of which the latest version will be fetched.
        :type id: str
        :returns: Artifact object or None if not found.
        :rtype: Optional[Artifact]
        """
        a = self.get(id)
        if a is None:
            return None

        while a.replaced_by != None:
            a = self.get(a.replaced_by)

        return a

    def all_latest(self):
        """
        Fetches the latest version of each artifact.

        :returns: List of Artifact objects.
        :rtype: list[Artifact]
        """
        ars: list[Artifact] = []
        for _, v in self.artifacts.items():
            if v.replaced_by != None:
                continue
            ars.append(v)
        return ars

    def all_latest_prompt_formatted(self):
        ars = self.all_latest()
        return artifact_list_to_prompt_format(artifact_list=ars)
