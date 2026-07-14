"""SQLite storage: schema, idempotent upsert with dedupe, run log, meta kv.

Single-track (research) — simpler than the two-track parent project. Dedupe id
is sha1 of a stable seed: the DOI if present, else the normalized URL. Scores
are write-once: an item that already has a score is never re-scored.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta

from .net import normalize_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id             TEXT PRIMARY KEY,
  source         TEXT, source_type TEXT,
  title          TEXT, url TEXT, doi TEXT,
  published_at   TEXT, fetched_at TEXT,
  summary        TEXT,
  prefilter_hits INTEGER,
  score          INTEGER, tag TEXT, rationale TEXT,
  scored_at      TEXT, scorer TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_at TEXT, source TEXT, fetched INTEGER, new INTEGER, ok INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_score ON items(score);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
"""

_COLUMNS = (
    "id", "source", "source_type", "title", "url", "doi", "published_at",
    "fetched_at", "summary", "prefilter_hits", "score", "tag", "rationale",
    "scored_at", "scorer",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def compute_id(item: dict) -> str:
    if item.get("doi"):
        seed = "doi:" + str(item["doi"]).strip().lower()
    else:
        seed = "url:" + normalize_url(item.get("url", ""))
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def upsert_items(conn, items: list[dict]) -> int:
    """Insert unseen items; returns count of new rows. Existing rows untouched."""
    new = 0
    fetched_at = now_iso()
    for item in items:
        item = dict(item)
        item["id"] = compute_id(item)
        if conn.execute("SELECT 1 FROM items WHERE id=?", (item["id"],)).fetchone():
            continue
        item.setdefault("fetched_at", fetched_at)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        conn.execute(
            f"INSERT INTO items ({', '.join(_COLUMNS)}) VALUES ({placeholders})",
            tuple(item.get(c) for c in _COLUMNS))
        new += 1
    conn.commit()
    return new


def record_run(conn, source: str, fetched: int, new: int, ok: bool, note: str = "") -> None:
    conn.execute(
        "INSERT INTO runs(run_at, source, fetched, new, ok, note) VALUES(?,?,?,?,?,?)",
        (now_iso(), source, fetched, new, 1 if ok else 0, note))
    conn.commit()


def unscored(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE score IS NULL").fetchall()


def apply_score(conn, item_id: str, score: int, tag: str, rationale: str,
                scorer: str, prefilter_hits: int | None = None) -> None:
    fields = "score=?, tag=?, rationale=?, scored_at=?, scorer=?"
    args = [score, tag, rationale, now_iso(), scorer]
    if prefilter_hits is not None:
        fields += ", prefilter_hits=?"
        args.append(prefilter_hits)
    args.append(item_id)
    conn.execute(f"UPDATE items SET {fields} WHERE id=? AND score IS NULL", tuple(args))
    conn.commit()


def recent_source_failures(conn, streak: int = 3) -> list[str]:
    sources = [r["source"] for r in conn.execute("SELECT DISTINCT source FROM runs").fetchall()]
    flagged = []
    for src in sources:
        rows = conn.execute(
            "SELECT ok FROM runs WHERE source=? ORDER BY run_at DESC LIMIT ?",
            (src, streak)).fetchall()
        if len(rows) >= streak and all(r["ok"] == 0 for r in rows):
            flagged.append(src)
    return flagged


def prune(conn, threshold: int, keep_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "DELETE FROM items WHERE (score IS NULL OR score < ?) "
        "AND COALESCE(published_at, fetched_at) < ?", (threshold, cutoff))
    conn.commit()
    return cur.rowcount


def vacuum(conn) -> None:
    conn.execute("VACUUM")
    conn.commit()
