"""Chart metadata management for ``.wichy/charts/`` directory.

Manages PNG files and sidecar ``.meta.json`` files. Each chart has:
- ``<uuid>.png`` — the rendered chart image
- ``<uuid>.meta.json`` — metadata (chart type, table, title, favorite, etc.)

The metadata file is written at render time and updated when favorite is
toggled (INV-021). No database — purely filesystem-based.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from wichy.config.settings import settings


def get_charts_dir() -> Path:
    """Return the charts directory path, creating it if it does not exist.

    Returns:
        Path to ``.wichy/charts/`` as an **absolute** path (INV-006).
        Absolute is required for ``send_from_directory`` to work correctly
        regardless of the Flask app's working directory.
    """
    charts_dir = settings.charts_dir.resolve()
    charts_dir.mkdir(parents=True, exist_ok=True)
    return charts_dir


def generate_chart_id() -> str:
    """Generate a unique chart ID (UUID4 hex without dashes)."""
    return uuid.uuid4().hex


def _meta_path(charts_dir: Path, chart_id: str) -> Path:
    """Return the sidecar metadata file path for a chart ID."""
    return charts_dir / f"{chart_id}.meta.json"


def _png_path(charts_dir: Path, chart_id: str) -> Path:
    """Return the PNG file path for a chart ID."""
    return charts_dir / f"{chart_id}.png"


def save_meta(
    chart_id: str,
    chart_type: str,
    table: str,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
) -> None:
    """Write the sidecar metadata file for a chart.

    Called at render time. Creates ``<chart_id>.meta.json`` alongside the PNG.

    Args:
        chart_id: Unique chart ID.
        chart_type: Chart type id (e.g. ``"bar"``).
        table: Source table name or SQL description.
        title: Optional chart title.
        subtitle: Optional chart subtitle.
    """
    charts_dir = get_charts_dir()
    meta: dict[str, Any] = {
        "chart_id": chart_id,
        "chart_type": chart_type,
        "table": table,
        "title": title or "Untitled",
        "subtitle": subtitle or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "favorite": False,
    }
    path = _meta_path(charts_dir, chart_id)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_meta(chart_id: str) -> Optional[dict[str, Any]]:
    """Load sidecar metadata for a chart.

    Returns ``None`` if the metadata file does not exist. If the PNG exists
    but the meta file is missing, returns a default metadata dict (INV-021
    risk mitigation: "Sidecar .meta.json files get out of sync").

    Args:
        chart_id: Unique chart ID.

    Returns:
        Metadata dict or ``None`` if neither PNG nor meta file exist.
    """
    charts_dir = get_charts_dir()
    meta_file = _meta_path(charts_dir, chart_id)
    png_file = _png_path(charts_dir, chart_id)

    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Meta file missing but PNG exists — return defaults
    if png_file.exists():
        return {
            "chart_id": chart_id,
            "chart_type": "unknown",
            "table": "",
            "title": "Untitled",
            "subtitle": "",
            "created_at": datetime.fromtimestamp(
                png_file.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "favorite": False,
        }

    return None


def set_favorite(chart_id: str, favorite: bool) -> bool:
    """Toggle the favorite state of a chart.

    Updates the sidecar ``.meta.json`` file. Returns ``True`` if the chart
    was found and updated, ``False`` otherwise.

    Args:
        chart_id: Unique chart ID.
        favorite: New favorite state.
    """
    meta = load_meta(chart_id)
    if meta is None:
        return False

    meta["favorite"] = favorite
    charts_dir = get_charts_dir()
    path = _meta_path(charts_dir, chart_id)
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return True


def list_charts(favorites_only: bool = False) -> list[dict[str, Any]]:
    """List all charts in the charts directory.

    Reads metadata fresh on each call (no caching). Returns a list of dicts
    with: ``chart_id``, ``filename``, ``created_at``, ``favorite``,
    ``chart_type``, ``table``, ``title``, ``subtitle``.

    Args:
        favorites_only: If ``True``, return only favorited charts.

    Returns:
        List of chart metadata dicts, sorted by creation time descending.
    """
    charts_dir = get_charts_dir()
    charts: list[dict[str, Any]] = []

    for png_file in sorted(charts_dir.glob("*.png")):
        chart_id = png_file.stem
        meta = load_meta(chart_id)
        if meta is None:
            continue

        if favorites_only and not meta.get("favorite", False):
            continue

        charts.append(
            {
                "chart_id": chart_id,
                "filename": png_file.name,
                "created_at": meta.get("created_at", ""),
                "favorite": meta.get("favorite", False),
                "chart_type": meta.get("chart_type", "unknown"),
                "table": meta.get("table", ""),
                "title": meta.get("title", "Untitled"),
                "subtitle": meta.get("subtitle", ""),
            }
        )

    # Sort by creation time descending (newest first)
    charts.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    return charts


def get_chart_info(chart_id: str) -> Optional[dict[str, Any]]:
    """Get metadata for a single chart.

    Returns ``None`` if the chart does not exist.
    """
    meta = load_meta(chart_id)
    if meta is None:
        return None

    charts_dir = get_charts_dir()
    png_file = _png_path(charts_dir, chart_id)
    return {
        "chart_id": chart_id,
        "filename": png_file.name,
        "created_at": meta.get("created_at", ""),
        "favorite": meta.get("favorite", False),
        "chart_type": meta.get("chart_type", "unknown"),
        "table": meta.get("table", ""),
        "title": meta.get("title", "Untitled"),
        "subtitle": meta.get("subtitle", ""),
    }


def delete_chart(chart_id: str) -> bool:
    """Delete a chart's PNG and sidecar metadata file.

    Returns ``True`` if the chart was found and deleted, ``False`` otherwise.
    """
    charts_dir = get_charts_dir()
    png_file = _png_path(charts_dir, chart_id)
    meta_file = _meta_path(charts_dir, chart_id)

    deleted = False
    if png_file.exists():
        png_file.unlink()
        deleted = True
    if meta_file.exists():
        meta_file.unlink()
        deleted = True

    return deleted


def get_png_path(chart_id: str) -> Optional[Path]:
    """Return the PNG file path for a chart if it exists, else ``None``."""
    charts_dir = get_charts_dir()
    png_file = _png_path(charts_dir, chart_id)
    if png_file.exists():
        return png_file
    return None
