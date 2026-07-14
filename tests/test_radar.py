"""Offline tests: normalization, dedupe, prefilter gating, keyword scoring,
dashboard render, digest guard. No live HTTP, no ANTHROPIC_API_KEY."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from radar import store, filter as flt, render, digest
from radar.net import normalize_url
from radar.collectors.library import parse_dois


def sample_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


KW = {"topic": ["grid", "power"], "exclude": ["horoscope"],
      "region": ["indonesia"], "tags": {"methods": ["decomposition", "milp"]}}


def test_normalize_url_strips_tracking_and_slash():
    assert normalize_url("HTTPS://Ex.com/A/?utm_source=x") == "https://ex.com/A"


def test_compute_id_doi_then_url():
    assert store.compute_id({"doi": "10.1/AB", "url": "u"}) == store.compute_id({"doi": "10.1/ab"})
    assert store.compute_id({"url": "https://x/1"}) != store.compute_id({"url": "https://x/2"})


def test_upsert_dedupe():
    conn = store.connect(":memory:")
    it = {"title": "t", "url": "https://x/1", "source": "arxiv", "source_type": "paper"}
    assert store.upsert_items(conn, [it]) == 1
    assert store.upsert_items(conn, [it]) == 0


def test_prefilter_paper_passes_news_needs_topic_excludes_hard_zero():
    assert flt.prefilter({"source_type": "paper", "title": "x", "summary": ""}, KW)[0]
    assert not flt.prefilter({"source_type": "news", "title": "cats", "summary": ""}, KW)[0]
    assert flt.prefilter({"source_type": "news", "title": "power grid", "summary": ""}, KW)[0]
    assert not flt.prefilter({"source_type": "news", "title": "grid horoscope", "summary": ""}, KW)[0]


def test_keyword_score_ranks_and_tags():
    tags = ["methods"]
    hot = flt.keyword_score({"id": "1", "title": "MILP decomposition for the grid in Indonesia",
                             "summary": ""}, KW, tags)
    cold = flt.keyword_score({"id": "2", "title": "a poem about cats", "summary": ""}, KW, tags)
    assert hot["score"] > cold["score"]
    assert hot["tag"] == "methods"


def test_render_produces_page(tmp_path):
    conn = store.connect(":memory:")
    store.upsert_items(conn, [{"title": "PLN grid study", "url": "https://r/1",
                               "source": "arxiv", "source_type": "paper",
                               "published_at": "2026-07-13"}])
    rid = store.compute_id({"url": "https://r/1"})
    store.apply_score(conn, rid, 9, "ch3-garuda", "why", "keyword")
    out = render.render(conn, sample_cfg(), str(tmp_path / "index.html"))
    html = open(out, encoding="utf-8").read()
    assert "PLN grid study" in html
    assert "Pantau Research" in html  # title parameterized


def test_parse_dois_cleans_and_dedupes(tmp_path):
    bib = tmp_path / "lib.bib"
    bib.write_text(
        '@article{a, title={X}, doi = {10.1016/J.APENERGY.2020.114679}}\n'
        '@article{b, title={Y}, doi = {https://doi.org/10.1109/tps.2019.123}}\n'
        '@article{c, title={Z}, doi={10.1016/j.apenergy.2020.114679}}\n'  # dup of a (case)
        '@misc{d, title={No DOI here}}\n', encoding="utf-8")
    dois = parse_dois(str(bib))
    assert dois == ["10.1016/j.apenergy.2020.114679", "10.1109/tps.2019.123"]


def test_digest_guard_and_empty():
    conn = store.connect(":memory:")
    assert digest.is_due(conn, send_hour_utc=0)
    store.set_meta(conn, "last_digest_sent_at", store.now_iso())
    assert not digest.is_due(conn, send_hour_utc=0)
    assert digest.build(conn, sample_cfg()) is None
