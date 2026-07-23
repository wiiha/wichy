"""Tests for chart metadata management (sidecar .meta.json files)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wichy.tools.viz.metadata import (
    delete_chart,
    generate_chart_id,
    get_chart_info,
    get_charts_dir,
    get_png_path,
    list_charts,
    load_meta,
    save_meta,
    set_favorite,
)


@pytest.fixture
def isolated_charts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated charts directory for each test."""
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    # Patch settings.charts_dir to return our temp dir
    from wichy.config.settings import settings

    monkeypatch.setattr(type(settings), "charts_dir", property(lambda self: charts_dir))
    return charts_dir


@pytest.fixture
def chart_with_meta(isolated_charts_dir: Path) -> str:
    """Create a chart with a PNG and metadata file."""
    chart_id = generate_chart_id()
    # Create a dummy PNG file
    png_file = isolated_charts_dir / f"{chart_id}.png"
    png_file.write_bytes(b"fake PNG content")
    # Save metadata
    save_meta(
        chart_id=chart_id,
        chart_type="bar",
        table="test_table",
        title="Test Chart",
        subtitle="A subtitle",
    )
    return chart_id


class TestGetChartsDir:
    """Tests for get_charts_dir."""

    def test_creates_directory(self, isolated_charts_dir: Path) -> None:
        """get_charts_dir creates the directory if it doesn't exist."""
        # isolated_charts_dir already created it, so let's test a subdirectory
        result = get_charts_dir()
        assert result.exists()
        assert result.is_dir()


class TestGenerateChartId:
    """Tests for generate_chart_id."""

    def test_generates_unique_ids(self) -> None:
        """Generated IDs are unique."""
        ids = {generate_chart_id() for _ in range(100)}
        assert len(ids) == 100

    def test_generates_hex_string(self) -> None:
        """Generated ID is a hex string (no dashes)."""
        chart_id = generate_chart_id()
        assert all(c in "0123456789abcdef" for c in chart_id)
        assert "-" not in chart_id


class TestSaveLoadMeta:
    """Tests for save_meta and load_meta."""

    def test_save_and_load(self, isolated_charts_dir: Path) -> None:
        """Saving metadata then loading it returns the same data."""
        chart_id = generate_chart_id()
        save_meta(
            chart_id=chart_id,
            chart_type="scatter",
            table="my_table",
            title="My Scatter",
            subtitle="Test subtitle",
        )
        meta = load_meta(chart_id)
        assert meta is not None
        assert meta["chart_id"] == chart_id
        assert meta["chart_type"] == "scatter"
        assert meta["table"] == "my_table"
        assert meta["title"] == "My Scatter"
        assert meta["subtitle"] == "Test subtitle"
        assert meta["favorite"] is False
        assert "created_at" in meta

    def test_load_nonexistent(self, isolated_charts_dir: Path) -> None:
        """load_meta returns None for a chart that doesn't exist."""
        result = load_meta("nonexistent_id")
        assert result is None

    def test_load_with_missing_meta_but_png_exists(
        self, isolated_charts_dir: Path
    ) -> None:
        """load_meta returns defaults when PNG exists but meta is missing."""
        chart_id = generate_chart_id()
        png_file = isolated_charts_dir / f"{chart_id}.png"
        png_file.write_bytes(b"fake PNG")
        meta = load_meta(chart_id)
        assert meta is not None
        assert meta["chart_id"] == chart_id
        assert meta["favorite"] is False
        assert meta["title"] == "Untitled"
        assert meta["chart_type"] == "unknown"

    def test_save_default_title(self, isolated_charts_dir: Path) -> None:
        """save_meta uses 'Untitled' when title is None."""
        chart_id = generate_chart_id()
        save_meta(chart_id=chart_id, chart_type="bar", table="t")
        meta = load_meta(chart_id)
        assert meta is not None
        assert meta["title"] == "Untitled"
        assert meta["subtitle"] == ""


class TestSetFavorite:
    """Tests for set_favorite."""

    def test_set_favorite_true(self, chart_with_meta: str) -> None:
        """set_favorite(True) updates the metadata."""
        result = set_favorite(chart_with_meta, True)
        assert result is True
        meta = load_meta(chart_with_meta)
        assert meta is not None
        assert meta["favorite"] is True

    def test_set_favorite_false(self, chart_with_meta: str) -> None:
        """set_favorite(False) updates the metadata."""
        set_favorite(chart_with_meta, True)  # First set to True
        result = set_favorite(chart_with_meta, False)
        assert result is True
        meta = load_meta(chart_with_meta)
        assert meta is not None
        assert meta["favorite"] is False

    def test_set_favorite_nonexistent(self, isolated_charts_dir: Path) -> None:
        """set_favorite returns False for a nonexistent chart."""
        result = set_favorite("nonexistent_id", True)
        assert result is False


class TestListCharts:
    """Tests for list_charts."""

    def test_empty_directory(self, isolated_charts_dir: Path) -> None:
        """list_charts returns empty list for empty directory."""
        result = list_charts()
        assert result == []

    def test_lists_charts(self, isolated_charts_dir: Path) -> None:
        """list_charts returns all charts with metadata."""
        # Create two charts
        for i in range(2):
            chart_id = generate_chart_id()
            png_file = isolated_charts_dir / f"{chart_id}.png"
            png_file.write_bytes(b"fake PNG")
            save_meta(
                chart_id=chart_id,
                chart_type="bar",
                table=f"table_{i}",
                title=f"Chart {i}",
            )

        charts = list_charts()
        assert len(charts) == 2
        for c in charts:
            assert "chart_id" in c
            assert "filename" in c
            assert "created_at" in c
            assert "favorite" in c
            assert "chart_type" in c
            assert "table" in c
            assert "title" in c

    def test_favorites_only(self, isolated_charts_dir: Path) -> None:
        """list_charts with favorites_only returns only favorited charts."""
        # Create 3 charts, favorite 1
        chart_ids = []
        for i in range(3):
            chart_id = generate_chart_id()
            chart_ids.append(chart_id)
            png_file = isolated_charts_dir / f"{chart_id}.png"
            png_file.write_bytes(b"fake PNG")
            save_meta(
                chart_id=chart_id,
                chart_type="bar",
                table=f"table_{i}",
                title=f"Chart {i}",
            )

        set_favorite(chart_ids[1], True)

        all_charts = list_charts()
        assert len(all_charts) == 3

        fav_charts = list_charts(favorites_only=True)
        assert len(fav_charts) == 1
        assert fav_charts[0]["chart_id"] == chart_ids[1]

    def test_sorted_newest_first(self, isolated_charts_dir: Path) -> None:
        """list_charts sorts by creation time descending."""
        chart_ids = []
        for i in range(3):
            chart_id = generate_chart_id()
            chart_ids.append(chart_id)
            png_file = isolated_charts_dir / f"{chart_id}.png"
            png_file.write_bytes(b"fake PNG")
            save_meta(
                chart_id=chart_id,
                chart_type="bar",
                table="t",
                title=f"Chart {i}",
            )

        charts = list_charts()
        # Should be sorted by created_at descending
        timestamps = [c["created_at"] for c in charts]
        assert timestamps == sorted(timestamps, reverse=True)


class TestGetChartInfo:
    """Tests for get_chart_info."""

    def test_get_info(self, chart_with_meta: str) -> None:
        """get_chart_info returns full metadata for a chart."""
        info = get_chart_info(chart_with_meta)
        assert info is not None
        assert info["chart_id"] == chart_with_meta
        assert info["chart_type"] == "bar"
        assert info["table"] == "test_table"
        assert info["title"] == "Test Chart"
        assert info["favorite"] is False
        assert info["filename"] == f"{chart_with_meta}.png"

    def test_get_info_nonexistent(self, isolated_charts_dir: Path) -> None:
        """get_chart_info returns None for nonexistent chart."""
        result = get_chart_info("nonexistent_id")
        assert result is None


class TestDeleteChart:
    """Tests for delete_chart."""

    def test_delete_existing(self, chart_with_meta, isolated_charts_dir: Path) -> None:
        """delete_chart removes both PNG and metadata."""
        chart_id = chart_with_meta
        png_file = isolated_charts_dir / f"{chart_id}.png"
        meta_file = isolated_charts_dir / f"{chart_id}.meta.json"
        assert png_file.exists()
        assert meta_file.exists()

        result = delete_chart(chart_id)
        assert result is True
        assert not png_file.exists()
        assert not meta_file.exists()

    def test_delete_nonexistent(self, isolated_charts_dir: Path) -> None:
        """delete_chart returns False for nonexistent chart."""
        result = delete_chart("nonexistent_id")
        assert result is False


class TestGetPngPath:
    """Tests for get_png_path."""

    def test_existing_png(self, chart_with_meta: str) -> None:
        """get_png_path returns the path for an existing chart."""
        path = get_png_path(chart_with_meta)
        assert path is not None
        assert path.exists()
        assert path.suffix == ".png"

    def test_nonexistent_png(self, isolated_charts_dir: Path) -> None:
        """get_png_path returns None for a nonexistent chart."""
        result = get_png_path("nonexistent_id")
        assert result is None
