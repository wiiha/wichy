"""Tests for chart API endpoints in the data explorer blueprint."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Blueprint, Flask, render_template


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Flask:
    """Create a Flask app with a fresh data blueprint.

    Creates a new blueprint each time to avoid Flask's "already registered"
    error with module-level singleton blueprints.
    """
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    from wichy.config.settings import settings

    monkeypatch.setattr(type(settings), "charts_dir", property(lambda self: charts_dir))

    templates_dir = str(
        Path(__file__).parent.parent / "src" / "wichy" / "tools" / "data" / "templates"
    )

    app = Flask(__name__, template_folder=templates_dir)

    fresh_bp = Blueprint(
        "data",
        __name__,
        url_prefix="/tools/data",
        template_folder=templates_dir,
    )

    from wichy.tools.data import api

    api.register_routes(fresh_bp)
    api.register_recipe_routes(fresh_bp)
    api.register_chart_routes(fresh_bp)

    @fresh_bp.route("/", methods=["GET"])
    def explorer():
        return render_template("data_explorer.html")

    app.register_blueprint(fresh_bp)
    return app


@pytest.fixture
def client(app: Flask):
    return app.test_client()


@pytest.fixture
def loaded_test_table() -> str:
    """Load test data into DuckDB."""
    from wichy.tools.duckdb_manager import DuckDBManager
    from wichy.tools.duckdb_reset import DuckDBResetTool

    DuckDBResetTool().execute()
    manager = DuckDBManager.get_instance()
    with manager.get_connection() as conn:
        conn.execute(
            "CREATE TABLE test_api_data AS "
            "SELECT range AS id, 'cat_' || (range % 3) AS category, "
            "range * 10.0 AS value FROM range(0, 20)"
        )
    return "test_api_data"


class TestChartTypesEndpoint:
    """Test GET /api/chart-types."""

    def test_returns_chart_types(self, client) -> None:
        """chart-types endpoint returns a list of chart types."""
        resp = client.get("/tools/data/api/chart-types")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "chart_types" in data
        assert isinstance(data["chart_types"], list)
        assert len(data["chart_types"]) >= 14

        # Check structure
        entry = data["chart_types"][0]
        assert "id" in entry
        assert "label" in entry
        assert "category" in entry
        assert "icon" in entry
        assert "field_roles" in entry


class TestRenderEndpoint:
    """Test POST /api/chart/render."""

    def test_render_bar_chart(self, client, loaded_test_table) -> None:
        """Render endpoint produces a chart from a table."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "bar",
                "config": {"x": "category", "y": "value", "title": "Test"},
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "chart_id" in data
        assert "url" in data
        assert data["url"].startswith("/tools/data/api/chart/")

    def test_render_missing_table(self, client) -> None:
        """Missing table returns 400."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={"chart_type": "bar", "config": {}},
        )
        assert resp.status_code == 400

    def test_render_invalid_chart_type(self, client, loaded_test_table) -> None:
        """Unknown chart type returns 400."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "nonexistent",
                "config": {},
            },
        )
        assert resp.status_code == 400

    def test_render_invalid_config(self, client, loaded_test_table) -> None:
        """Invalid config (missing required fields) returns 400."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={"table": loaded_test_table, "chart_type": "bar", "config": {}},
        )
        assert resp.status_code == 400


class TestServeChartEndpoint:
    """Test GET /api/chart/<chart_id>."""

    def test_serve_chart(self, client, loaded_test_table) -> None:
        """Serve endpoint returns a PNG image."""
        # First render a chart
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "bar",
                "config": {"x": "category", "y": "value"},
            },
        )
        chart_id = resp.get_json()["chart_id"]

        # Now serve it
        resp = client.get(f"/tools/data/api/chart/{chart_id}")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"

    def test_serve_nonexistent(self, client) -> None:
        """Serving nonexistent chart returns 404."""
        resp = client.get("/tools/data/api/chart/" + "a" * 32)
        assert resp.status_code == 404

    def test_serve_invalid_id(self, client) -> None:
        """Invalid chart ID (not a UUID) returns 404."""
        resp = client.get("/tools/data/api/chart/../../../etc/passwd")
        assert resp.status_code == 404


class TestDownloadEndpoint:
    """Test GET /api/chart/<chart_id>/download."""

    def test_download_chart(self, client, loaded_test_table) -> None:
        """Download endpoint returns a PNG with attachment header."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "bar",
                "config": {"x": "category", "y": "value"},
            },
        )
        chart_id = resp.get_json()["chart_id"]

        resp = client.get(f"/tools/data/api/chart/{chart_id}/download")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"
        assert "attachment" in resp.headers.get("Content-Disposition", "")


class TestChartInfoEndpoint:
    """Test GET /api/chart/<chart_id>/info."""

    def test_get_info(self, client, loaded_test_table) -> None:
        """Info endpoint returns chart metadata."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "bar",
                "config": {"x": "category", "y": "value", "title": "My Chart"},
            },
        )
        chart_id = resp.get_json()["chart_id"]

        resp = client.get(f"/tools/data/api/chart/{chart_id}/info")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["chart_id"] == chart_id
        assert data["chart_type"] == "bar"
        assert data["table"] == loaded_test_table
        assert data["title"] == "My Chart"
        assert data["favorite"] is False


class TestFavoriteEndpoint:
    """Test PATCH /api/chart/<chart_id>/favorite."""

    def test_toggle_favorite(self, client, loaded_test_table) -> None:
        """Favorite endpoint toggles the favorite state."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "bar",
                "config": {"x": "category", "y": "value"},
            },
        )
        chart_id = resp.get_json()["chart_id"]

        # Set favorite to True
        resp = client.patch(
            f"/tools/data/api/chart/{chart_id}/favorite",
            json={"favorite": True},
        )
        assert resp.status_code == 200
        assert resp.get_json()["favorite"] is True

        # Verify via info
        resp = client.get(f"/tools/data/api/chart/{chart_id}/info")
        assert resp.get_json()["favorite"] is True

        # Set back to False
        resp = client.patch(
            f"/tools/data/api/chart/{chart_id}/favorite",
            json={"favorite": False},
        )
        assert resp.status_code == 200
        assert resp.get_json()["favorite"] is False


class TestListChartsEndpoint:
    """Test GET /api/charts and GET /api/charts/favorites."""

    def test_list_charts(self, client, loaded_test_table) -> None:
        """List endpoint returns all charts."""
        # Create two charts
        for chart_type in ["bar", "scatter"]:
            client.post(
                "/tools/data/api/chart/render",
                json={
                    "table": loaded_test_table,
                    "chart_type": chart_type,
                    "config": (
                        {"x": "category", "y": "value"}
                        if chart_type == "bar"
                        else {"x": "id", "y": "value"}
                    ),
                },
            )

        resp = client.get("/tools/data/api/charts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "charts" in data
        assert len(data["charts"]) >= 2

    def test_list_favorites_only(self, client, loaded_test_table) -> None:
        """Favorites endpoint returns only favorited charts."""
        # Create two charts
        chart_ids = []
        for _ in range(2):
            resp = client.post(
                "/tools/data/api/chart/render",
                json={
                    "table": loaded_test_table,
                    "chart_type": "bar",
                    "config": {"x": "category", "y": "value"},
                },
            )
            chart_ids.append(resp.get_json()["chart_id"])

        # Favorite one
        client.patch(
            f"/tools/data/api/chart/{chart_ids[0]}/favorite",
            json={"favorite": True},
        )

        # Get favorites
        resp = client.get("/tools/data/api/charts/favorites")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["charts"]) == 1
        assert data["charts"][0]["chart_id"] == chart_ids[0]


class TestDeleteChartEndpoint:
    """Test DELETE /api/chart/<chart_id>."""

    def test_delete_chart(self, client, loaded_test_table) -> None:
        """Delete endpoint removes the chart."""
        resp = client.post(
            "/tools/data/api/chart/render",
            json={
                "table": loaded_test_table,
                "chart_type": "bar",
                "config": {"x": "category", "y": "value"},
            },
        )
        chart_id = resp.get_json()["chart_id"]

        resp = client.delete(f"/tools/data/api/chart/{chart_id}")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

        # Verify it's gone
        resp = client.get(f"/tools/data/api/chart/{chart_id}/info")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client) -> None:
        """Deleting nonexistent chart returns 404."""
        resp = client.delete("/tools/data/api/chart/" + "b" * 32)
        assert resp.status_code == 404
