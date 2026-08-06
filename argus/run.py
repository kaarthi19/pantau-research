"""ARGUS orchestrator: collect -> store -> filter -> render -> digest.

Every source is wrapped so one failure logs and continues. Scoring auto-selects
keyword mode when the configured provider's API key is unset, so the whole
pipeline is buildable and testable with no key at all.

    python -m argus.run                 # full pipeline
    python -m argus.run --dry-run       # collect + per-source counts, no writes
    python -m argus.run --stage collect # a single stage (state persists in the db)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import yaml

from . import store, filter as flt, render as render_mod, digest as digest_mod
from .net import PoliteSession, CONTACT_EMAIL
from .collectors import openalex, arxiv, gnews, rss, library

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _library_due(conn, lib_cfg: dict) -> bool:
    """Library discovery runs at most every `every_days` (default 7) — it's a
    weekly shortlist, not something to rebuild every 4h. dry-run (conn=None) always runs."""
    if conn is None:
        return True
    every = int(lib_cfg.get("every_days", 7))
    last = store.get_meta(conn, "last_library_run")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last_dt).days >= every


def collect_all(sources: dict, cfg: dict, window_days: int, conn=None) -> tuple[list[dict], list[tuple]]:
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

    lib_cfg = cfg.get("library", {})
    if lib_cfg.get("enabled") and _library_due(conn, lib_cfg):
        guarded("library", lambda: library.collect(sess, lib_cfg, CONTACT_EMAIL))
        if conn is not None:
            store.set_meta(conn, "last_library_run", store.now_iso())
    return items, records


def score(conn, cfg: dict, kw: dict, mode: str, spec: dict) -> dict:
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
    to_score = passing[:cap] if mode == "llm" else passing

    if mode == "llm":
        prompt = _read(os.path.join(ROOT, cfg["prompt"]))
        results = flt.llm_score(to_score, spec, prompt,
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
    db_path = args.db or os.path.join(ROOT, "data", "argus.db")

    if args.dry_run:
        items, records = collect_all(sources, cfg, window_days)
        print("=== DRY RUN — per-source counts (no writes) ===")
        for name, count, ok, note in records:
            print(f"  [{'ok ' if ok else 'FAIL'}] {name}: {note}")
        print(f"Total items collected: {len(items)}")
        return 0

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = store.connect(db_path)
    mode, spec, note = flt.scoring_plan(cfg.get("scoring", {}))
    stage = args.stage
    summary: list[str] = [f"scoring: {mode} — {note}"]

    if stage in ("all", "collect"):
        items, records = collect_all(sources, cfg, window_days, conn=conn)
        new = store.upsert_items(conn, items)
        for name, count, ok, note in records:
            store.record_run(conn, name, count, 0, ok, note)
        summary.append(f"collect: {len(items)} fetched, {new} new")

    if stage in ("all", "score"):
        summary.append(f"score[{mode}]: {score(conn, cfg, kw, mode, spec)}")

    if stage in ("all", "render"):
        out = args.out or os.path.join(ROOT, "docs", "index.html")
        summary.append(f"render: {render_mod.render(conn, cfg, out)}")

    if stage in ("all", "digest"):
        summary.append(f"digest: {digest_mod.run(conn, cfg, force=args.force_digest)}")

    if stage == "all":
        pcfg = cfg.get("prune", {})
        removed = store.prune(conn, cfg["show_threshold"], pcfg.get("keep_days", 180))
        removed_runs = store.prune_runs(conn, pcfg.get("keep_run_days", 30))
        if removed or removed_runs:
            summary.append(f"prune: {removed} items, {removed_runs} run-log rows removed")

    if args.vacuum:
        store.vacuum(conn)
        summary.append("vacuum: database rewritten")

    print("\n".join(summary))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="argus")
    p.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    p.add_argument("--db", default=None)
    p.add_argument("--out", default=None, help="dashboard path (default docs/index.html)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--vacuum", action="store_true",
                   help="reclaim space after pruning (rewrites the whole file — occasional, not scheduled)")
    p.add_argument("--force-digest", action="store_true")
    p.add_argument("--stage", default="all", choices=["all", "collect", "score", "render", "digest"])
    return run_pipeline(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
