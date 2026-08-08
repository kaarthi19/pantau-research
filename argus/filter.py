"""Two-stage relevance filter.

Stage 1 — prefilter (zero cost): papers always pass; news/org items need >=1
topic hit; an exclude-term hit is a hard zero.
Stage 2 — scorer: `llm` (batched, temperature 0, strict JSON, one retry, any
provider — see providers.py) or `keyword` (weighted keyword scoring, the $0
fallback). Items failing the prefilter are written as score 0
(scorer='prefilter') so they never re-queue.

Tags are the workstreams defined in config.yaml; any tag the model returns that
isn't configured collapses to "none".
"""
from __future__ import annotations

import json

from . import providers


def _hits(text: str, terms: list[str]) -> int:
    return sum(1 for t in terms if t and t.lower() in text)


def prefilter(item: dict, kw: dict) -> tuple[bool, int]:
    """Return (passes, topic_hit_count)."""
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    n = _hits(text, kw.get("topic", []))
    if _hits(text, kw.get("exclude", [])) > 0 and item.get("source_type") != "paper":
        return False, n
    if item.get("source_type") == "paper":
        return True, n
    return n >= 1, n


def keyword_score(item: dict, kw: dict, tags: list[str], venue_cfg: dict | None = None) -> dict:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    if _hits(text, kw.get("exclude", [])) > 0:
        return _result(item, 0, "none", "excluded topic", "keyword", tags)
    tag_terms = kw.get("tags", {})
    tag_hits = {tag: _hits(text, terms) for tag, terms in tag_terms.items()}
    best_tag, best = ("none", 0)
    if tag_hits:
        best_tag = max(tag_hits, key=tag_hits.get)
        best = tag_hits[best_tag]
    topic = _hits(text, kw.get("topic", []))
    region = _hits(text, kw.get("region", []))
    score = min(10, best * 3 + topic + region)
    tag = best_tag if best > 0 else "none"
    why = f"keyword match: {best} tag / {topic} topic / {region} region hits"

    delta, vwhy = venue_adjust(item, venue_cfg or {})
    if delta:
        score = max(0, min(10, score + delta))
        why = f"{why} [{vwhy}]"
    return _result(item, score, tag, why, "keyword", tags)


def _result(item, score, tag, why, scorer, tags) -> dict:
    return {
        "id": item["id"],
        "score": int(max(0, min(10, score))),
        "tag": tag if tag in tags else "none",
        "rationale": why,
        "scorer": scorer,
    }


def venue_adjust(item: dict, cfg: dict) -> tuple[int, str]:
    """Score nudge for where a paper appeared. Returns (delta, reason).

    This exists to correct a measurable bias, not to express a taste. OpenAlex
    carries no abstract for 60-82% of recent articles from the big commercial
    publishers, while arXiv always has one — so a peer-reviewed paper is scored
    on its title while a preprint is scored on a full abstract, and loses on
    information rather than merit. Before this, arXiv supplied 57% of everything
    above threshold. Set both weights to 0 to rank purely on content.
    """
    if not cfg or not cfg.get("enabled", True):
        return 0, ""
    pre = item.get("is_preprint")
    if pre is None:
        return 0, ""
    if int(pre) == 1:
        d = int(cfg.get("preprint", 0))
        return d, (f"preprint {d:+d}" if d else "")
    d = int(cfg.get("peer_reviewed", 0))
    # A title-only peer-reviewed paper is the case this is really for: it had no
    # abstract to be judged on, so give back what the missing text cost it.
    if d and not (item.get("summary") or "").strip():
        d += int(cfg.get("no_abstract_offset", 0))
        return d, f"peer-reviewed, no abstract available {d:+d}"
    return d, (f"peer-reviewed {d:+d}" if d else "")


def _clamp(raw: dict, tags: list[str], scorer: str) -> dict:
    tag = raw.get("tag", "none")
    if tag not in tags:
        tag = "none"
    try:
        score = int(raw.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    return {
        "id": raw.get("id"),
        "score": max(0, min(10, score)),
        "tag": tag,
        "rationale": (raw.get("why") or raw.get("rationale") or "")[:280],
        "scorer": scorer,
    }


def _payload(items: list[dict]) -> str:
    slim = []
    for it in items:
        row = {"id": it["id"], "title": it.get("title", ""),
               "summary": (it.get("summary") or "")[:1000]}
        if it.get("venue"):
            row["venue"] = it["venue"]
        if it.get("is_preprint") is not None:
            row["peer_reviewed"] = not int(it["is_preprint"])
        # Say so explicitly: otherwise the model reads a bare title as a thin
        # paper rather than as a paper whose abstract the metadata lacks.
        if not (it.get("summary") or "").strip():
            row["note"] = "no abstract available from the source; judge on title and venue"
        slim.append(row)
    return json.dumps(slim, ensure_ascii=False)


def llm_score(items: list[dict], spec: dict, system_prompt: str,
              batch_size: int, tags: list[str], venue_cfg: dict | None = None) -> list[dict]:
    """Score items in batches through whichever provider `spec` names. A batch
    that fails (network, quota, unparseable JSON) is skipped, not fatal — those
    items stay unscored and are retried on the next run."""
    scorer = spec["provider"]
    by_item = {it["id"]: it for it in items}
    results: list[dict] = []
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        by_id = {it["id"] for it in batch}
        user = ("Score every item below. Return ONLY a JSON array, one object "
                "per item, no markdown fences.\n\nITEMS:\n" + _payload(batch))
        parsed = _call(spec, system_prompt, user)
        if parsed is None:
            parsed = _call(spec, system_prompt,
                           user + "\n\nReturn valid JSON only — an array of objects.")
        if parsed is None:
            continue
        for raw in parsed:
            if isinstance(raw, dict) and raw.get("id") in by_id:
                res = _clamp(raw, tags, scorer)
                results.append(_apply_venue(res, by_item.get(res["id"], {}), venue_cfg))
    return results


def _apply_venue(res: dict, item: dict, venue_cfg: dict | None) -> dict:
    delta, why = venue_adjust(item, venue_cfg or {})
    if not delta:
        return res
    res["score"] = max(0, min(10, res["score"] + delta))
    res["rationale"] = f"{res['rationale']} [{why}]"[:280]
    return res


def _call(spec, system_prompt, user) -> list[dict] | None:
    text = providers.complete(spec, system_prompt, user, max_tokens=4096)
    if not text:
        return None
    text = text.replace("```json", "").replace("```", "").strip()
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        return None
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def scoring_plan(scoring: dict) -> tuple[str, dict, str]:
    """Decide how this run scores: ('llm'|'keyword', provider_spec, note).

    Every capability degrades — a missing key downgrades to keyword scoring
    rather than failing the run, which is what keeps ARGUS runnable with no
    secrets at all.
    """
    if not providers.wants_llm(scoring):
        return "keyword", {}, "keyword scoring (configured)"
    spec = providers.resolve(scoring)
    if not spec.get("model"):
        return "keyword", spec, f"no model set for provider '{spec['provider']}'"
    if not providers.has_credentials(spec):
        return "keyword", spec, providers.missing_key_hint(spec)
    return "llm", spec, providers.describe(spec)
