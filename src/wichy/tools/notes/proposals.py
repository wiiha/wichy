"""Proposal store: agent edits awaiting the user's accept or reject.

A proposal is recorded beside its document as ``<slug>.proposals.json``, a JSON
object keyed by proposal id. That file is the sole authority: every read goes to
disk, so a pending proposal survives a restart and no in-memory copy can
disagree with what GET returns.

The module is deliberately unaware of how a proposal is APPLIED. It knows how to
hash a block and how to keep at most one pending proposal per target; the lock
and the mutation live in the API, against a live document.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from wichy.tools.file_safety import atomic_write
from wichy.tools.notes.blocks import notes_dir
from wichy.tools.notes.models import Block, Proposal


def proposals_path(slug: str) -> Path:
    """The proposal file for ``slug``, beside its document."""
    return notes_dir() / f"{slug}.proposals.json"


def fingerprint_block(block: Block) -> str:
    """A stable hash of a block's type and data, ignoring its meta.

    ``meta.updated`` and ``meta.touched_by`` are rewritten by any unrelated
    re-save, so hashing them would make every proposal look stale.
    """
    canonical = json.dumps(
        {"type": block.type, "data": block.data},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _target_key(proposal: Proposal) -> tuple[str, str | None]:
    """What a proposal targets, namespaced by kind to avoid collisions."""
    if proposal.kind == "insert":
        return ("insert", proposal.anchor_id)
    return ("block", proposal.block_id)


def load_proposals(slug: str) -> dict[str, Proposal]:
    """Every proposal recorded for ``slug``, or {} when there is none to read."""
    try:
        raw = proposals_path(slug).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        return {
            proposal_id: Proposal.model_validate(item)
            for proposal_id, item in payload.items()
        }
    except (ValueError, TypeError):
        # A corrupt file is treated as empty rather than raised: a proposal the
        # user cannot review is a lost edit, not a reason to fail the read.
        return {}


def save_proposals(slug: str, proposals: Mapping[str, Proposal]) -> None:
    """Write ``proposals`` atomically, or remove the file when there are none."""
    path = proposals_path(slug)
    if not proposals:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    payload = {
        proposal_id: proposal.model_dump()
        for proposal_id, proposal in proposals.items()
    }
    atomic_write(
        str(path),
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def create_or_supersede(slug: str, proposal: Proposal) -> Proposal:
    """Store ``proposal``, replacing any pending proposal for the same target.

    Last-wins per target: the newest proposal for a target is the one the user
    reviews. Other proposals keep their first-touch order.
    """
    proposals = load_proposals(slug)
    key = _target_key(proposal)
    for existing_id, existing in list(proposals.items()):
        if not existing.resolved and _target_key(existing) == key:
            del proposals[existing_id]
    proposals[proposal.id] = proposal
    save_proposals(slug, proposals)
    return proposal


def resolve_proposal(slug: str, proposal_id: str) -> bool:
    """Mark ``proposal_id`` resolved and save; False when it is unknown."""
    proposals = load_proposals(slug)
    proposal = proposals.get(proposal_id)
    if proposal is None:
        return False
    proposal.resolved = True
    save_proposals(slug, proposals)
    return True


def forget_proposals(slug: str) -> None:
    """Remove ``slug``'s proposal file."""
    try:
        proposals_path(slug).unlink()
    except FileNotFoundError:
        pass
