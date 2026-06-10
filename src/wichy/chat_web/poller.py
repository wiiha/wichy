"""Minimal background poller: polls /server/api/messages, appends everything raw."""

import logging
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


def _tick() -> bool:
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
            entry = state.create_entry("assistant", raw)
            state.append(entry)
        return True
    except Exception:
        return False
