"""Minimal background poller: polls /server/api/messages, appends everything raw."""

import logging
import re
import threading
import time

import requests

from wichy.config import settings
from . import state

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0  # seconds
_server_port: int | None = None
_poller_thread: threading.Thread | None = None
_stop_event = threading.Event()
_session = requests.Session()
_seen_verification_ids: set[str] = set()
_seen_question_ids: set[str] = set()

_RICH_TAG_RE = re.compile(
    r'\[(\w+(?:\s+\w+)*)\](.*?)\[/\1\]',
    re.DOTALL,
)

# Unwrap messages like "---\n\n### Heading\n...content...\n---"
_WRAPPER_RE = re.compile(r'^(?:---\s*\n\s*)?###\s+\w+\s*\n\s*(.*?)\n\s*---\s*$', re.DOTALL)


def get_server_port() -> int | None:
    return _server_port


def _base_url() -> str:
    return f"http://{settings.server_host}:{_server_port}"


def start_poller(port: int) -> None:
    global _server_port, _poller_thread
    if _poller_thread is not None and _poller_thread.is_alive():
        return
    _server_port = port
    _stop_event.clear()
    _poller_thread = threading.Thread(target=_run, daemon=True)
    _poller_thread.start()
    logger.info("Chat poller started on port %s", port)


def stop_poller() -> None:
    global _server_port, _poller_thread
    _stop_event.set()
    if _poller_thread is not None:
        _poller_thread.join(timeout=2.0)
    _poller_thread = None
    _server_port = None
    logger.info("Chat poller stopped")


def _unwrap(text: str) -> str | None:
    """Strip outer wrapper and heading, returning inner content if present."""
    m = _WRAPPER_RE.match(text.strip())
    if m:
        inner = m.group(1).strip()
        if inner:
            return inner
    return None


def _strip_rich_tags(text: str) -> str:
    """Remove Rich markup tags iteratively, handling nested tags."""
    while True:
        new_text = _RICH_TAG_RE.sub(lambda m: m.group(2), text)
        if new_text == text:
            break
        text = new_text
    return text


def _has_rich_tags(text: str) -> bool:
    """Return True if text contains any Rich markup tags."""
    return bool(_RICH_TAG_RE.search(text))


def _run() -> None:
    consecutive_failures = 0
    while not _stop_event.is_set():
        try:
            ok = _tick()
            if ok:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
        except Exception:
            logger.exception("Poller tick failed")
            consecutive_failures += 1
        time.sleep(_POLL_INTERVAL)


def _poll_verifications() -> bool:
    try:
        resp = _session.get(f"{_base_url()}/server/api/verifications", timeout=3.0)
        _ = resp.text
        if resp.status_code == 503:
            return True  # not configured, non-fatal
        if resp.status_code != 200:
            return False
        items = resp.json()
        if not isinstance(items, list):
            return False
        global _seen_verification_ids
        for v in items:
            vid = v.get("id")
            if vid in _seen_verification_ids:
                continue
            _seen_verification_ids.add(vid)
            entry = state.create_entry(
                "system",
                f"Verification: {v.get('label', 'Action requested')}",
                "verification",
                {"verification": v},
            )
            state.append(entry)
        return True
    except Exception:
        return False


def _poll_questions() -> bool:
    try:
        resp = _session.get(f"{_base_url()}/server/api/questions", timeout=3.0)
        _ = resp.text
        if resp.status_code == 503:
            return True  # not configured, non-fatal
        if resp.status_code != 200:
            return False
        groups = resp.json()
        if not isinstance(groups, list):
            return False
        global _seen_question_ids
        for g in groups:
            qid = g.get("id")
            if qid in _seen_question_ids:
                continue
            _seen_question_ids.add(qid)
            questions = g.get("questions", [{}])
            header = questions[0].get("header", "...") if questions else "..."
            entry = state.create_entry(
                "system",
                f"Question: {header}",
                "question",
                {"group": g},
            )
            state.append(entry)
        return True
    except Exception:
        return False


def _tick() -> bool:
    try:
        msg_ok = _poll_messages()
        ver_ok = _poll_verifications()
        q_ok = _poll_questions()
        return msg_ok  # connection status tracks messages only
    except Exception:
        return False


def _poll_messages() -> bool:
    try:
        resp = _session.get(f"{_base_url()}/server/api/messages", timeout=3.0)
        _ = resp.text  # drain
        if resp.status_code == 503:
            return False
        if resp.status_code != 200:
            return False
        msgs = resp.json()
        if not isinstance(msgs, list):
            return False
        for raw in msgs:
            if not isinstance(raw, str):
                continue
            text = raw.strip()
            # Drop standalone separator lines
            if text == "---":
                continue
            # Unwrap wrapped messages: "---\n\n### Heading\n...content...\n---"
            unwrapped = _unwrap(text)
            if unwrapped is not None:
                text = unwrapped
            # Drop wrapper-only messages like "---\n\n### Assistant" with no body content
            if re.match(r'^(?:---\s*\n\s*)?###\s+\w+\s*$', text):
                continue
            # Strip Rich tags for system messages (tool calls, LLM metadata, etc.)
            if _has_rich_tags(text):
                plain = _strip_rich_tags(text).strip()
                if plain:
                    entry = state.create_entry("system", plain)
                    state.append(entry)
                continue
            entry = state.create_entry("assistant", text)
            state.append(entry)
        return True
    except Exception:
        return False
