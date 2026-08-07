"""Backfill abstracts that OpenAlex doesn't carry.

OpenAlex has no abstract for most recent articles from the large commercial
publishers — measured on this project's own journal watchlist, 60-82% of recent
works from Energy, Applied Energy, Energy Policy and Electric Power Systems
Research come back with an empty ``abstract_inverted_index``. arXiv, by
contrast, always has one.

That is not a cosmetic gap. The scorer reads title + summary, so a peer-reviewed
article arrives with a title alone while a preprint arrives with 1,000
characters of abstract — and loses on information rather than on merit. Before
this backfill, arXiv supplied 57% of everything above threshold.

Crossref carries abstracts for many of the same DOIs, as JATS XML. It's free,
needs no key, and asks only that you identify yourself (the "polite pool").
"""
from __future__ import annotations

import re

from ..net import strip_html

API = "https://api.crossref.org/works/"

# JATS wrappers that add nothing once the tags are stripped. Publishers vary in
# whether they include a title element, so drop it rather than render "Abstract"
# as the first word of every summary.
_JATS_TITLE = re.compile(r"<jats:title>.*?</jats:title>", re.I | re.S)


def _clean(abstract: str) -> str:
    return strip_html(_JATS_TITLE.sub(" ", abstract or ""))


def fetch_abstract(session, doi: str, mailto: str) -> str:
    """One DOI -> abstract text, or "" for anything that doesn't work out.
    Never raises: a backfill miss must not cost us the item."""
    if not doi:
        return ""
    try:
        resp = session.get(API + doi.strip(), params={"mailto": mailto})
        if resp.status_code != 200:
            return ""
        msg = (resp.json() or {}).get("message") or {}
    except Exception:  # noqa: BLE001 — network, 404, malformed JSON
        return ""
    return _clean(msg.get("abstract", ""))


# If the first this-many lookups all miss, the publisher behind this watchlist
# almost certainly doesn't deposit abstracts and every further call is wasted.
# Elsevier is the case that matters: it deposits none, and its titles dominate
# energy research. Give up rather than spend a round-trip per item forever.
GIVE_UP_AFTER = 10


def backfill(session, items: list[dict], mailto: str, limit: int = 60) -> dict:
    """Fill in missing summaries in place. Returns {filled, tried, gave_up}.

    Bounded by `limit` because this is one HTTP round-trip per item, serialised
    behind the polite per-host delay — an unbounded backfill on a first run
    would dominate the sweep.
    """
    if limit <= 0:
        return {"filled": 0, "tried": 0, "gave_up": False}
    filled = tried = 0
    for item in items:
        if filled >= limit:
            break
        if tried >= GIVE_UP_AFTER and filled == 0:
            return {"filled": 0, "tried": tried, "gave_up": True}
        if (item.get("summary") or "").strip():
            continue
        if item.get("source_type") != "paper" or not item.get("doi"):
            continue
        tried += 1
        text = fetch_abstract(session, item["doi"], mailto)
        if text:
            item["summary"] = text
            filled += 1
    return {"filled": filled, "tried": tried, "gave_up": False}
