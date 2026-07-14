"""Research Radar orchestrator: collect -> store -> filter -> render -> digest.

Every source is wrapped so one failure logs and continues. Scoring auto-selects
keyword mode when ANTHROPIC_API_KEY is unset, so the whole pipeline is buildable
and testable with no key.

    python -m radar.run                 # full pipeline
    python -m radar.run --dry-run       # collect + per-source counts, no writes
    python -m radar.run --stage collect # a single stage (state persists in the db)
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

from . import store, filter as flt, render as render_mod, digest as digest_mod
from .net import PoliteSession, CONTACT_EMAIL
from .collectors import openalex, arxiv, gnews, rss

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def collect_all(sources: dict, window_days: int) -> tuple[list[dict], list[tuple]]:
    sess = PoliteSession()
    items: list[dict] = []
    records: list[tuple] = []

    def guarded(name, fn):
        try:
            got = fn()
            records.append((name, len(got), True, f"{len(got)} items"))
            items.extend(got)
        except Exception as exc:  # noqa: BLE001
            records.append((name, 0, False, f"{type(exc).__name__}: {exc}"))

    oa = sources.get("openalex", {})
    guarded("openalex", lambda: openalex.collect(
        sess, oa.get("queries", []), oa.get("issns", []), oa.get("authors", []),
        window_days, CONTACT_EMAIL))
    ax = sources.get("arxiv", {})
    guarded("arxiv", lambda: arxiv.collect(
        sess, ax.get("categories", []), ax.get("terms", []), window_days))
    guarded("gnews", lambda: gnews.collect(
        sess, sources.get("gnews", {}).get("queries", []), window_days))

    feed_items, feed_records = rss.collect(sess, sources.get("feeds", []), window_days)
    items.extend(feed_items)
    for name, ok, note in feed_records:
        records.append((f"rss:{name}", 0, ok, note))
    return items, records


def score(conn, cfg: dict, kw: dict, mode: str) -> dict:
    rows = store.unscored(conn)
    items = [{k: r[k] for k in r.keys()} for r in rows]
    if not items:
        return {"scored": 0, "prefiltered": 0}

    tags = list(cfg["tags"].keys())
    passing, prefiltered = [], 0
    for it in items:
        ok, hits = flt.prefilter(it, kw)
        it["prefilter_hits"] = hits
        if ok:
            passing.append(it)
        else:
            store.apply_score(conn, it["id"], 0, "none", "failed prefilter",
                              "prefilter", prefilter_hits=hits)
            prefiltered += 1

    passing.sort(key=lambda x: x.get("published_at") or x.get("fetched_at") or "", reverse=True)
    cap = cfg["scoring"].get("max_llm_items_per_run", 150)
    to_score = passing[:cap] if mode == "haiku" else passing

    if mode == "haiku":
        prompt = _read(os.path.join(ROOT, cfg["prompt"]))
        results = flt.haiku_score(to_score, cfg["scoring"]["model"], prompt,
                                  cfg["scoring"].get("batch_size", 20), tags)
    else:
        results = [flt.keyword_score(it, kw, tags) for it in to_score]

    for res in results:
        store.apply_score(conn, res["id"], res["score"], res["tag"],
                          res["rationale"], res["scorer"])
    return {"scored": len(results), "prefiltered": prefiltered,
            "deferred": len(passing) - len(to_score)}


def run_pipeline(args) -> int:
    cfg = _load_yaml(args.config)
    sources = _load_yaml(os.path.join(ROOT, "registry", "sources.yaml"))
    kw = sources.get("keywords", {})
    window_days = cfg.get("window_days", 7)
    db_path = args.db or os.path.join(ROOT, "data", "radar.db")

    if args.dry_run:
        items, records = collect_all(sources, window_days)
        print("=== DRY RUN — per-source counts (no writes) ===")
        for name, count, ok, note in records:
            print(f"  [{'ok ' if ok else 'FAIL'}] {name}: {note}")
        print(f"Total items collected: {len(items)}")
        return 0

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = store.connect(db_path)
    mode = flt.scoring_mode(cfg["scoring"].get("mode", "keyword"))
    stage = args.stage
    summary: list[str] = []

    if stage in ("all", "collect"):
        items, records = collect_all(sources, window_days)
        new = store.upsert_items(conn, items)
        for name, count, ok, note in records:
            store.record_run(conn, name, count, 0, ok, note)
        summary.append(f"collect: {len(items)} fetched, {new} new")

    if stage in ("all", "score"):
        summary.append(f"score[{mode}]: {score(conn, cfg, kw, mode)}")

    if stage in ("all", "render"):
        summary.append(f"render: {render_mod.render(conn, cfg, os.path.join(ROOT, 'docs', 'index.html'))}")

    if stage in ("all", "digest"):
        summary.append(f"digest: {digest_mod.run(conn, cfg, force=args.force_digest)}")

    if stage == "all":
        removed = store.prune(conn, cfg["show_threshold"], cfg.get("prune", {}).get("keep_days", 180))
        if removed:
            summary.append(f"prune: {removed} rows removed")

    print("\n".join(summary))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="radar")
    p.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    p.add_argument("--db", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-digest", action="store_true")
    p.add_argument("--stage", default="all", choices=["all", "collect", "score", "render", "digest"])
    return run_pipeline(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
