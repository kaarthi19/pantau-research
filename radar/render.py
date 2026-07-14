"""Render the dashboard (docs/index.html) — a self-contained, theme-aware page."""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import store

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]))


def _get(r, key, default=None):
    try:
        val = r[key]
        return val if val is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def build_context(conn, cfg: dict) -> dict:
    show = cfg["show_threshold"]
    highlight = cfg["highlight_threshold"]
    render_days = cfg.get("render_days", 14)
    tags = cfg["tags"]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=render_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    top_cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

    listing = conn.execute(
        "SELECT * FROM items WHERE score >= ? AND COALESCE(published_at, fetched_at) >= ? "
        "ORDER BY COALESCE(published_at, fetched_at) DESC, score DESC LIMIT 300",
        (show, cutoff)).fetchall()
    top = conn.execute(
        "SELECT * FROM items WHERE score >= ? AND COALESCE(published_at, fetched_at) >= ? "
        "ORDER BY score DESC, COALESCE(published_at, fetched_at) DESC LIMIT 8",
        (highlight, top_cutoff)).fetchall()

    def shape(r):
        tag = _get(r, "tag", "none")
        meta = tags.get(tag, {"label": tag, "color": "#888888"})
        return {
            "title": _get(r, "title", ""), "url": _get(r, "url", ""),
            "source": _get(r, "source", ""), "score": _get(r, "score", 0),
            "rationale": _get(r, "rationale", ""),
            "date": _get(r, "published_at") or _get(r, "fetched_at", ""),
            "tag_label": meta["label"], "tag_color": meta["color"],
        }

    return {
        "title": cfg.get("title", "Research Radar"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "top_picks": [shape(r) for r in top],
        "items": [shape(r) for r in listing],
        "total": len(listing),
        "scorer_mode": _latest_scorer(conn),
        "failures": store.recent_source_failures(conn, streak=3),
        "workstreams": [{"label": m["label"], "color": m["color"]} for m in tags.values()],
    }


def _latest_scorer(conn) -> str:
    row = conn.execute(
        "SELECT scorer FROM items WHERE scorer IS NOT NULL AND scorer != 'prefilter' "
        "ORDER BY scored_at DESC LIMIT 1").fetchone()
    return row["scorer"] if row else "keyword"


def render(conn, cfg: dict, out_path: str = "docs/index.html") -> str:
    context = build_context(conn, cfg)
    html = _env().get_template("dashboard.html.j2").render(**context)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
