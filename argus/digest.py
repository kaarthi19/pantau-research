"""Daily email digest, grouped by workstream.

Trigger: once per UTC day, on the first run at/after send_hour_utc, tracked in
meta.last_digest_sent_at. Zero items -> skip. A digest failure never fails the run.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import store, mailer, providers

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _env():
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]))


def is_due(conn, send_hour_utc: int, force: bool = False) -> bool:
    if force:
        return True
    now = datetime.now(timezone.utc)
    last = store.get_meta(conn, "last_digest_sent_at")
    if last and last[:10] == now.strftime("%Y-%m-%d"):
        return False
    return now.hour >= send_hour_utc


def _since(conn) -> str:
    last = store.get_meta(conn, "last_digest_sent_at")
    floor = (datetime.now(timezone.utc) - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return last if (last and last > floor) else floor


def _floor(cfg: dict) -> int:
    """Score an item must clear to be emailed. Defaults to the dashboard's
    `show_threshold`; `digest.min_score` raises it so the daily email can be
    stricter than the browsable dashboard."""
    return int(cfg.get("digest", {}).get("min_score") or cfg["show_threshold"])


def build(conn, cfg: dict) -> dict | None:
    since = _since(conn)
    show = _floor(cfg)
    # Regular research since the last digest (library-sourced rows are handled
    # separately below so they don't appear twice).
    rows = conn.execute(
        "SELECT * FROM items WHERE score >= ? AND fetched_at >= ? "
        "AND (source IS NULL OR source NOT LIKE 'library%') ORDER BY score DESC",
        (show, since)).fetchall()

    library = _library_shortlist(conn, cfg)

    if not rows and not library:
        return None

    tags = cfg["tags"]
    groups = []
    for tag, meta in tags.items():
        items = [_shape(r) for r in rows if (r["tag"] or "none") == tag]
        if items:
            groups.append({"label": meta["label"], "color": meta["color"], "entries": items})
    other = [_shape(r) for r in rows if (r["tag"] or "none") not in tags]
    if other:
        groups.append({"label": "Other", "color": "#888888", "entries": other})

    total = len(rows) + len(library)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "title": cfg.get("title", "ARGUS"),
        "date": date, "n": total, "groups": groups,
        "library": library,
        "library_label": f"From your library · top {len(library)}" if library else "",
        "synthesis": _maybe_synthesis(cfg, rows),
        "failures": store.recent_source_failures(conn, streak=3),
        "subject": f"{cfg.get('title', 'ARGUS')} — {total} items · {date}",
    }


def _library_shortlist(conn, cfg: dict) -> list[dict]:
    """Top-N library-sourced papers from the most recent weekly sweep."""
    lib_cfg = cfg.get("library", {})
    if not lib_cfg.get("enabled"):
        return []
    top_n = int(lib_cfg.get("top_n", 30))
    window_days = int(lib_cfg.get("every_days", 7)) + 3
    since = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT * FROM items WHERE source LIKE 'library%' AND score >= ? "
        "AND fetched_at >= ? ORDER BY score DESC LIMIT ?",
        (_floor(cfg), since, top_n)).fetchall()
    return [_shape(r) for r in rows]


def _shape(r) -> dict:
    return {"title": r["title"] or "", "url": r["url"] or "",
            "source": r["source"] or "", "score": r["score"], "why": r["rationale"] or ""}


def _maybe_synthesis(cfg, rows) -> str | None:
    """Optional one-paragraph intro. Runs on the same provider as scoring unless
    `digest.synthesis_provider` overrides it; silently skipped if unavailable."""
    dcfg = cfg.get("digest", {})
    if str(dcfg.get("synthesis", "none")).lower() in ("none", "", "false"):
        return None

    scoring = dict(cfg.get("scoring", {}))
    if dcfg.get("synthesis_provider"):
        scoring["provider"] = dcfg["synthesis_provider"]
        scoring.pop("base_url", None)
        scoring.pop("api_key_env", None)
    if dcfg.get("synthesis_model"):
        scoring["model"] = dcfg["synthesis_model"]

    spec = providers.resolve(scoring)
    if not spec.get("model") or not providers.has_credentials(spec):
        return None

    lines = [f"- {r['title']}" for r in rows][:40]
    return providers.complete(
        spec,
        "You write terse, factual research summaries. No preamble.",
        "In 2-3 sentences, summarize what matters most in today's research "
        "digest:\n" + "\n".join(lines),
        max_tokens=300) or None


def _text(ctx: dict) -> str:
    out = [f"{ctx['title']} — {ctx['date']}", f"{ctx['n']} items", ""]
    if ctx.get("synthesis"):
        out += [ctx["synthesis"], ""]
    if ctx.get("library"):
        out.append(f"== {ctx['library_label']} ==")
        for it in ctx["library"]:
            out.append(f"[{it['score']}] {it['title']} — {it['source']}\n"
                       f"    {it['why']}\n    {it['url']}")
        out.append("")
    for g in ctx["groups"]:
        out.append(f"== {g['label']} ==")
        for it in g["entries"]:
            out.append(f"[{it['score']}] {it['title']} — {it['source']}\n"
                       f"    {it['why']}\n    {it['url']}")
        out.append("")
    if ctx["failures"]:
        out.append("Source failures (3 consecutive): " + ", ".join(ctx["failures"]))
    return "\n".join(out)


def run(conn, cfg: dict, force: bool = False) -> dict:
    if not cfg["digest"].get("enabled", True):
        return {"sent": False, "note": "digest disabled"}
    if not is_due(conn, cfg["digest"].get("send_hour_utc", 0), force):
        return {"sent": False, "note": "not due yet today"}
    ctx = build(conn, cfg)
    if ctx is None:
        return {"sent": False, "note": "no items — skipped"}
    html = _env().get_template("digest.html.j2").render(**ctx)
    text = _text(ctx)
    try:
        ok = mailer.send_email(cfg["digest"]["recipient"], ctx["subject"], html, text)
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "note": f"send error: {type(exc).__name__}: {exc}"}
    if ok:
        store.set_meta(conn, "last_digest_sent_at", store.now_iso())
        return {"sent": True, "note": ctx["subject"]}
    return {"sent": False, "note": "no SMTP creds — digest skipped"}
