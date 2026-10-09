"""Browser tests for the graph editor.

vis-network renders to a canvas, so these assert the surrounding contracts: the
canvas mounts, a seeded graph auto-loads, and saving round-trips through the API.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, expect
import pytest

pytestmark = pytest.mark.e2e

GRAPH = "/tools/graph/"


@pytest.fixture
def seeded_graph(e2e_server: str) -> str:
    graph = {
        "nodes": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}],
        "edges": [{"from": "A", "to": "B"}],
    }
    path = Path(".wichy/graphs/latest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph), encoding="utf-8")
    return "latest.json"


def test_the_canvas_mounts(page: Page, e2e_server: str) -> None:
    page.goto(e2e_server + GRAPH, wait_until="domcontentloaded")
    expect(page.locator("#network canvas")).to_be_visible()


def test_a_seeded_graph_auto_loads(
    page: Page, e2e_server: str, seeded_graph: str
) -> None:
    page.goto(e2e_server + GRAPH, wait_until="domcontentloaded")
    expect(page.locator("#status")).to_have_text("Loaded latest.json")
    values = page.locator("#graph-selector option").evaluate_all(
        "els => els.map(e => e.value)"
    )
    assert seeded_graph in values


def test_saving_reports_a_new_file(
    page: Page, e2e_server: str, seeded_graph: str
) -> None:
    page.goto(e2e_server + GRAPH, wait_until="domcontentloaded")
    expect(page.locator("#status")).to_have_text("Loaded latest.json")
    page.click("#save-btn")
    expect(page.locator("#status")).to_contain_text("Saved!")
