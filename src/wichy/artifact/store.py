from typing import List, Optional, Tuple

from rapidfuzz.distance.DamerauLevenshtein import (
    normalized_similarity as rapidfuzz_normalized_similarity,
)

from .artifact import Artifact, ArtifactReference
from .helpers import (
    artifact_list_to_prompt_format,
    console,
    find_missing_artifact_id,
    select_artifacts_by_query,
    select_artifacts_for_prompt,
    select_candidate_for_artifact,
)
from .store_backend import StoreBackendSQLite


class ArtifactStore:
    """
    Storage for artifacts with versioning.

    Features:
    - Immutable artifacts with supersession chains
    - LLM-based deduplication for similar artifacts
    - Version management (latest vs specific)
    - Lightweight references for discovery
    """

    def __init__(self, session_id: str):
        """
        Initialize artifact store.
        """
        self.session_id = session_id

    def add(self, a: Artifact):
        # call _find_possible_previous_version and see if there is any matches
        a_str = ArtifactReference.from_artifact(a).format_for_prompt()
        console.log(f"new artifact:\n{a_str}")
        possible = self._find_possible_previous_version(a)

        if len(possible) == 0:
            # no candidates, just add
            with StoreBackendSQLite() as store:
                if not store.create(artifact=a, session_id=self.session_id):
                    raise Exception("failed to add artifact")
                return

        candidates: list[Artifact] = []
        for cid in possible:
            candidate = self.get(cid)
            candidates.append(candidate)

        try:
            del candidate
        except:
            pass
        try:
            del cid
        except:
            pass

        cid, confidence, motivation = select_candidate_for_artifact(
            a=a, candidates=candidates
        )
        if cid == "":
            # LLM deemed no candidates were viable, just add
            with StoreBackendSQLite() as store:
                if not store.create(artifact=a, session_id=self.session_id):
                    raise Exception("failed to add artifact")
                return

        sel_c = self.get(cid)

        if sel_c is None:
            console.log(
                f"[yellow]warning[/yellow] LLM returned artifact id [green]{cid}[/green], which did not have a match, this is unexpected. Will add artifact as new artifact, but there might be a previous version.",
                log_locals=True,
            )
            with StoreBackendSQLite() as store:
                if not store.create(artifact=a, session_id=self.session_id):
                    raise Exception("failed to add artifact")
                return

        c_str = ArtifactReference.from_artifact(sel_c).format_for_prompt()

        console.log(
            f"artifact\n{c_str}\n[green]replaced by[/green]\n{a_str}\n---\n[green]reson:[/green] {motivation}\n[green]confidence:[/green] {confidence}."
        )

        # candidate selected by LLM and it was a valid ID.

        new_a = a.model_copy(update={"version": sel_c.version + 1})
        updated_sel_c = sel_c.model_copy(update={"replaced_by": a.id})
        with StoreBackendSQLite() as store:
            if not store.create(artifact=new_a, session_id=self.session_id):
                raise Exception("failed to add artifact")
            if not store.update_by_id(updated_sel_c):
                raise Exception(
                    "new artifact added, failed to mark old version as replaced"
                )

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
        with StoreBackendSQLite() as store:
            return store.get_by_id(id)

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

        with StoreBackendSQLite() as store:
            latest = store.find_where_replaced_by_is_null(session_id=self.session_id)
            if len(latest) < 1:
                return ars

            for i in latest:
                artifact = i.get("artifact")
                ars.append(artifact)

        return ars

    def all_latest_prompt_formatted(self):
        """
        Fetches the latest version of each artifact and returns a string representation
        that can be injected into a prompt.

        :returns: String representing latest version of each artifact.
        :rtype: str
        """
        ars = self.all_latest()
        return artifact_list_to_prompt_format(artifact_list=ars)

    def artifacts_for_prompt(
        self, prompt: str, intended_recipient: str = ""
    ) -> list[Artifact]:
        """
        Uses the provided prompt to identify artifacts of possible relevance for the context.

        :param prompt: The prompt that will be passed to an LLM model. Will be used as reference for choosing relevant artifacts.
        :type prompt: str
        :param intended_recipient: The name of the recipient (usually an agent) that will receive the prompt.
        :type intended_recipient: str
        :returns: List of artifacts that are deemed relevant.
        :rtype: list[Artifact]
        :raises ValueError: If prompt is empty or whitespace-only.
        """
        if prompt.strip() == "":
            raise ValueError(
                "cannot have an empty prompt as basis for artifact selection"
            )

        cids = select_artifacts_for_prompt(
            prompt=prompt, recipient=intended_recipient, candidates=self.all_latest()
        )

        return self._resolve_artifact_ids(cids)

    def artifacts_for_query(
        self, query: str, intended_recipient: str = ""
    ) -> list[Artifact]:
        """
        Uses the provided query to identify artifacts containing relevant information.

        :param query: The query (question, subject, or topic) used as reference for choosing relevant artifacts.
        :type query: str
        :param intended_recipient: The name of the recipient (usually an agent) that will receive the query results.
        :type intended_recipient: str
        :returns: List of artifacts that are deemed relevant.
        :rtype: list[Artifact]
        :raises ValueError: If query is empty or whitespace-only.
        """
        if query.strip() == "":
            raise ValueError(
                "cannot have an empty query as basis for artifact selection"
            )

        cids = select_artifacts_by_query(
            query=query, recipient=intended_recipient, candidates=self.all_latest()
        )

        return self._resolve_artifact_ids(cids)

    def _resolve_artifact_ids(self, artifact_ids: list[str]) -> list[Artifact]:
        """
        Helper method to resolve artifact IDs to actual Artifact objects.

        :param artifact_ids: List of artifact IDs to resolve.
        :type artifact_ids: list[str]
        :returns: List of resolved Artifact objects (skips any IDs that can't be found).
        :rtype: list[Artifact]
        """
        artifacts: list[Artifact] = []
        for aid in artifact_ids:
            artifact = self.get(aid)
            if artifact is None:
                artifact = self._resolve_failed_artifact_id(artifact_id=aid)
                if artifact is None:
                    console.log(
                        f"[yellow]warning[/yellow] got id {aid} from LLM, expected match, got None."
                    )
                    continue
            artifacts.append(artifact)

        return artifacts

    def _resolve_failed_artifact_id(
        self, artifact_id: str, tries=0, max_tries=3
    ) -> Optional[Artifact]:

        # exit condition
        if tries >= max_tries:
            return None

        candidates = self.get_latest()
        new_suggestion = find_missing_artifact_id(
            artifact_id=artifact_id, candidates=candidates
        )

        new_a = self.get(new_suggestion)

        # exit condition
        if new_a != None:
            return new_a

        # still haven't found and we have more tries, call it again
        return self._resolve_failed_artifact_id(
            artifact_id=artifact_id, tries=tries + 1, max_tries=max_tries
        )
