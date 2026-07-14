"""Two-stage relevance filter.

Stage 1 — prefilter (zero cost): papers always pass; news/org items need >=1
topic hit; an exclude-term hit is a hard zero.
Stage 2 — scorer: `haiku` (batched, temperature 0, strict JSON, one retry) or
`keyword` (weighted keyword scoring, the $0 fallback). Items failing the
prefilter are written as score 0 (scorer='prefilter') so they never re-queue.

Tags are the workstreams defined in config.yaml; any tag the model returns that
isn't configured collapses to "none".
"""
from __future__ import annotations

import json
import os


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


def keyword_score(item: dict, kw: dict, tags: list[str]) -> dict:
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
    return _result(item, score, tag, why, "keyword", tags)


def _result(item, score, tag, why, scorer, tags) -> dict:
    return {
        "id": item["id"],
        "score": int(max(0, min(10, score))),
        "tag": tag if tag in tags else "none",
        "rationale": why,
        "scorer": scorer,
    }


def _clamp(raw: dict, tags: list[str]) -> dict:
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
        "scorer": "haiku",
    }


def _payload(items: list[dict]) -> str:
    slim = [{"id": it["id"], "title": it.get("title", ""),
             "summary": (it.get("summary") or "")[:1000]} for it in items]
    return json.dumps(slim, ensure_ascii=False)


def haiku_score(items: list[dict], model: str, system_prompt: str,
                batch_size: int, tags: list[str]) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    results: list[dict] = []
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        by_id = {it["id"]: it for it in batch}
        user = ("Score every item below. Return ONLY a JSON array, one object "
                "per item, no markdown fences.\n\nITEMS:\n" + _payload(batch))
        parsed = _call(client, model, system_prompt, user)
        if parsed is None:
            parsed = _call(client, model, system_prompt,
                           user + "\n\nReturn valid JSON only — an array of objects.")
        if parsed is None:
            continue
        for raw in parsed:
            if raw.get("id") in by_id:
                results.append(_clamp(raw, tags))
    return results


def _call(client, model, system_prompt, user) -> list[dict] | None:
    try:
        resp = client.messages.create(
            model=model, max_tokens=2048, temperature=0, system=system_prompt,
            messages=[{"role": "user", "content": user}])
    except Exception:  # noqa: BLE001 — SDK already backs off 429/5xx
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    s, e = text.find("["), text.rfind("]")
    if s == -1 or e == -1:
        return None
    try:
        data = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def scoring_mode(configured: str) -> str:
    if configured == "haiku" and not os.environ.get("ANTHROPIC_API_KEY"):
        return "keyword"
    return configured
