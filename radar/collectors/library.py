"""Library-seeded discovery from a Zotero / BibTeX export.

"Find papers based on the papers' history": parse your reference library's DOIs,
resolve them to OpenAlex works, then surface RECENT papers that
  (a) CITE something in your library  — bibliographic descendants, i.e. new work
      building on what you read, and
  (b) are written BY authors you already read a lot of.

Uses only OpenAlex (already a dependency) via OR-filters, so the whole thing is
a handful of polite API calls. The .bib itself stays a local/secret input — it
is never committed (see .gitignore) and never leaves the machine except as DOIs
sent to OpenAlex.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta

from .openalex import _work_to_item

API = "https://api.openalex.org/works"
_DOI_RE = re.compile(r'doi\s*=\s*[{"]\s*([^}"]+?)\s*[}"]', re.IGNORECASE)


def parse_dois(path: str) -> list[str]:
    """Extract unique, cleaned DOIs from a .bib file."""
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    out: list[str] = []
    for raw in _DOI_RE.findall(text):
        d = raw.strip().lower()
        d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
        if d:
            out.append(d)
    return list(dict.fromkeys(out))          # dedupe, keep order


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _short(oa_id: str) -> str:
    return (oa_id or "").rsplit("/", 1)[-1]


def _works(session, filter_clause: str, mailto: str, per_page: int = 50) -> list[dict]:
    params = {"per-page": per_page, "mailto": mailto, "filter": filter_clause}
    resp = session.get(API, params=params)
    resp.raise_for_status()
    return resp.json().get("results", [])


def collect(session, lib_cfg: dict, mailto: str) -> list[dict]:
    path = lib_cfg.get("bib_path", "library/zotero.bib")
    if not lib_cfg.get("enabled") or not os.path.exists(path):
        return []
    dois = parse_dois(path)
    if not dois:
        return []

    discovery_days = int(lib_cfg.get("discovery_days", 120))
    max_authors = int(lib_cfg.get("max_authors", 15))
    max_seed_works = int(lib_cfg.get("max_seed_works", 200))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=discovery_days)).strftime("%Y-%m-%d")

    # 1) Resolve library DOIs -> OpenAlex work ids + author frequencies.
    lib_work_ids: list[str] = []
    author_count: dict[str, int] = {}
    for batch in _chunks(dois, 50):
        clause = "doi:" + "|".join(batch)
        for w in _works(session, clause, mailto):
            lib_work_ids.append(_short(w.get("id")))
            for a in w.get("authorships", []):
                aid = _short((a.get("author") or {}).get("id"))
                if aid:
                    author_count[aid] = author_count.get(aid, 0) + 1
    lib_set = set(lib_work_ids)
    if not lib_work_ids:
        return []

    items: list[dict] = []
    seen: set[str] = set()

    def add(work: dict, source: str):
        wid = _short(work.get("id"))
        if wid in lib_set or wid in seen:      # skip the library itself + dupes
            return
        seen.add(wid)
        items.append(_work_to_item(work, source))

    # 2) Recent papers CITING the library (bibliographic descendants).
    for batch in _chunks(lib_work_ids[:max_seed_works], 50):
        clause = f"cites:{'|'.join(batch)},from_publication_date:{cutoff}"
        for w in _works(session, clause, mailto):
            add(w, "library: cites your library")

    # 3) Recent papers BY the authors you read most.
    top_authors = [a for a, _ in sorted(author_count.items(), key=lambda kv: -kv[1])[:max_authors]]
    if top_authors:
        clause = (f"authorships.author.id:{'|'.join(top_authors)},"
                  f"from_publication_date:{cutoff},type:article")
        for w in _works(session, clause, mailto):
            add(w, "library: author you read")

    return items
