"""Daily email digest, grouped by workstream.

Trigger: once per UTC day, on the first run at/after send_hour_utc, tracked in
meta.last_digest_sent_at. Zero items -> skip. A digest failure never fails the run.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import store, mailer

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


def build(conn, cfg: dict) -> dict | None:
    since = _since(conn)
    show = cfg["show_threshold"]
    rows = conn.execute(
        "SELECT * FROM items WHERE score >= ? AND fetched_at >= ? ORDER BY score DESC",
        (show, since)).fetchall()
    if not rows:
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

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "title": cfg.get("title", "Research Radar"),
        "date": date, "n": len(rows), "groups": groups,
        "synthesis": _maybe_synthesis(cfg, rows),
        "failures": store.recent_source_failures(conn, streak=3),
        "subject": f"{cfg.get('title', 'Research Radar')} — {len(rows)} items · {date}",
    }


def _shape(r) -> dict:
    return {"title": r["title"] or "", "url": r["url"] or "",
            "source": r["source"] or "", "score": r["score"], "why": r["rationale"] or ""}


def _maybe_synthesis(cfg, rows) -> str | None:
    if cfg["digest"].get("synthesis") != "sonnet" or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        lines = [f"- {r['title']}" for r in rows][:40]
        resp = client.messages.create(
            model=cfg["digest"].get("synthesis_model", "claude-sonnet-4-6"),
            max_tokens=300, temperature=0,
            messages=[{"role": "user", "content":
                       "In 2-3 sentences, summarize what matters most in today's "
                       "research digest:\n" + "\n".join(lines)}])
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    except Exception:  # noqa: BLE001
        return None


def _text(ctx: dict) -> str:
    out = [f"{ctx['title']} — {ctx['date']}", f"{ctx['n']} items", ""]
    if ctx.get("synthesis"):
        out += [ctx["synthesis"], ""]
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
