"""Offline tests for ARGUS: normalization, dedupe, prefilter gating, keyword scoring,
dashboard render, digest guard. No live HTTP, no ANTHROPIC_API_KEY."""
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from argus import store, filter as flt, render, digest, providers, context
from argus.net import normalize_url
from argus.collectors import library
from argus.collectors.library import parse_dois


def sample_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def days_ago(n: int) -> str:
    """Dates in render/digest fixtures must be relative — the dashboard only
    shows the last `render_days`, so hardcoded dates silently rot the suite."""
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


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
                               "published_at": days_ago(2)}])
    rid = store.compute_id({"url": "https://r/1"})
    store.apply_score(conn, rid, 9, "ch3-garuda", "why", "keyword")
    out = render.render(conn, sample_cfg(), str(tmp_path / "index.html"))
    html = open(out, encoding="utf-8").read()
    assert "PLN grid study" in html
    assert "ARGUS" in html  # title parameterized


def test_preset_provider_resolves_endpoint_and_key_env():
    spec = providers.resolve({"mode": "llm", "provider": "groq"})
    assert spec["kind"] == "openai"
    assert spec["base_url"] == "https://api.groq.com/openai/v1"
    assert spec["api_key_env"] == "GROQ_API_KEY"
    assert spec["model"]  # preset supplies a default


def test_legacy_haiku_mode_still_means_anthropic():
    # pre-1.1 configs said `mode: haiku` with no provider — must keep working
    spec = providers.resolve({"mode": "haiku", "model": "claude-haiku-4-5"})
    assert spec["provider"] == "anthropic"
    assert spec["kind"] == "anthropic"


def test_provider_name_as_mode_is_accepted():
    # `mode: groq` is a natural shorthand — must not silently resolve elsewhere
    spec = providers.resolve({"mode": "groq"})
    assert spec["provider"] == "groq"
    assert spec["base_url"].startswith("https://api.groq.com")


def test_unknown_provider_works_as_openai_compatible():
    spec = providers.resolve({"mode": "llm", "provider": "my-lab-gateway",
                              "base_url": "https://gpu.lab.internal/v1/",
                              "api_key_env": "LAB_KEY", "model": "mixtral"})
    assert spec["kind"] == "openai"
    assert spec["base_url"] == "https://gpu.lab.internal/v1"  # trailing slash trimmed
    assert spec["api_key_env"] == "LAB_KEY"


def test_local_provider_needs_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert providers.has_credentials(providers.resolve({"mode": "llm", "provider": "ollama"}))


def test_scoring_plan_degrades_to_keyword_without_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    mode, spec, note = flt.scoring_plan({"mode": "llm", "provider": "groq"})
    assert mode == "keyword"
    assert "GROQ_API_KEY" in note


def test_scoring_plan_uses_llm_when_key_present(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    mode, spec, note = flt.scoring_plan({"mode": "llm", "provider": "groq"})
    assert mode == "llm"
    assert spec["provider"] == "groq"


def test_scoring_plan_keyword_mode_never_touches_a_provider():
    mode, _, _ = flt.scoring_plan({"mode": "keyword"})
    assert mode == "keyword"


def test_llm_score_parses_batch_and_labels_scorer(monkeypatch):
    """The whole batch path with the network stubbed: fenced JSON, an unknown
    tag, and an id that wasn't in the batch (must be dropped)."""
    monkeypatch.setattr(providers, "complete", lambda *a, **k: (
        '```json\n[{"id": "a", "score": 9, "tag": "methods", "why": "on point"},\n'
        ' {"id": "b", "score": 4, "tag": "not-a-tag", "why": "meh"},\n'
        ' {"id": "ghost", "score": 10, "tag": "methods", "why": "not requested"}]\n```'))
    items = [{"id": "a", "title": "t1", "summary": ""}, {"id": "b", "title": "t2", "summary": ""}]
    out = flt.llm_score(items, {"provider": "groq", "model": "m"}, "prompt", 20, ["methods"])
    assert [r["id"] for r in out] == ["a", "b"]      # hallucinated id dropped
    assert out[0]["scorer"] == "groq"                 # provider recorded, not "haiku"
    assert out[1]["tag"] == "none"                    # unconfigured tag collapses


def test_llm_score_survives_provider_failure(monkeypatch):
    monkeypatch.setattr(providers, "complete", lambda *a, **k: None)
    out = flt.llm_score([{"id": "a", "title": "t", "summary": ""}],
                        {"provider": "groq", "model": "m"}, "prompt", 20, ["methods"])
    assert out == []  # unscored items simply retry next run


VENUE = {"enabled": True, "peer_reviewed": 1, "preprint": -1, "no_abstract_offset": 1}


def test_venue_weighting_corrects_the_preprint_advantage():
    """A journal paper with no abstract is scored on its title alone, so it gets
    the offset back; a preprint with a full abstract does not."""
    journal_no_abs = {"is_preprint": 0, "summary": ""}
    journal_with_abs = {"is_preprint": 0, "summary": "a real abstract"}
    preprint = {"is_preprint": 1, "summary": "a real abstract"}

    assert flt.venue_adjust(journal_no_abs, VENUE)[0] == 2   # 1 + 1 offset
    assert flt.venue_adjust(journal_with_abs, VENUE)[0] == 1
    assert flt.venue_adjust(preprint, VENUE)[0] == -1
    # unknown provenance is never guessed at
    assert flt.venue_adjust({"is_preprint": None}, VENUE) == (0, "")
    # and it can be turned off entirely
    assert flt.venue_adjust(journal_no_abs, {"enabled": False}) == (0, "")


def test_venue_weighting_flips_ranking_of_equal_content():
    """The whole point: identical keyword relevance, journal should now win."""
    kw = {"topic": ["grid"], "tags": {"methods": ["milp"]}, "region": [], "exclude": []}
    body = {"title": "MILP for the grid", "summary": ""}
    pre = flt.keyword_score({**body, "id": "a", "is_preprint": 1}, kw, ["methods"], VENUE)
    jrn = flt.keyword_score({**body, "id": "b", "is_preprint": 0}, kw, ["methods"], VENUE)
    assert jrn["score"] > pre["score"]
    assert "peer-reviewed" in jrn["rationale"] and "no abstract" in jrn["rationale"]
    # scores stay inside the 0-10 scale
    top = flt.keyword_score({"id": "c", "title": "MILP grid", "summary": "", "is_preprint": 0},
                            {"topic": ["milp", "grid"], "tags": {"methods": ["milp", "grid"]},
                             "region": [], "exclude": []}, ["methods"], VENUE)
    assert 0 <= top["score"] <= 10


def test_context_documents_load_and_skip_the_readme(tmp_path):
    d = tmp_path / "context"
    d.mkdir()
    (d / "README.md").write_text("Folder instructions, must not reach the scorer.", encoding="utf-8")
    (d / "01-concept.md").write_text("My thesis is about inter-island interconnection.", encoding="utf-8")
    (d / "notes.txt").write_text("Also: captive coal in nickel smelters.", encoding="utf-8")
    (d / "paper.pdf").write_bytes(b"%PDF-1.4 binary")

    block = context.load(str(tmp_path), {"enabled": True, "dir": "context"})
    assert "inter-island interconnection" in block
    assert "captive coal" in block
    assert "Folder instructions" not in block   # README is the folder's docs
    assert "%PDF" not in block                  # binary never reaches the prompt
    assert "2 reference documents" in context.describe(str(tmp_path), {"dir": "context"})


def test_context_is_bounded_and_optional(tmp_path):
    d = tmp_path / "context"
    d.mkdir()
    (d / "big.md").write_text("x" * 50_000, encoding="utf-8")
    block = context.load(str(tmp_path), {"dir": "context", "max_chars": 500})
    assert len(block) < 1200 and "[truncated]" in block
    assert context.load(str(tmp_path), {"enabled": False, "dir": "context"}) == ""
    assert context.load(str(tmp_path), {"dir": "does-not-exist"}) == ""


def test_migration_adds_columns_to_a_pre_existing_database(tmp_path):
    """The committed db is carried across upgrades, never rebuilt, so new
    columns have to be added to a live file."""
    db = str(tmp_path / "old.db")
    old = sqlite3.connect(db)
    old.executescript("""CREATE TABLE items (id TEXT PRIMARY KEY, source TEXT, source_type TEXT,
        title TEXT, url TEXT, doi TEXT, published_at TEXT, fetched_at TEXT, summary TEXT,
        prefilter_hits INTEGER, score INTEGER, tag TEXT, rationale TEXT, scored_at TEXT, scorer TEXT);
        INSERT INTO items(id, title) VALUES('keep-me', 'existing row');""")
    old.commit(); old.close()

    conn = store.connect(db)   # connect() migrates
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    assert {"venue", "is_preprint"} <= cols
    assert conn.execute("SELECT title FROM items WHERE id='keep-me'").fetchone()[0] == "existing row"
    assert store.migrate(conn) == []           # idempotent
    store.upsert_items(conn, [{"title": "new", "url": "https://x/9",
                               "venue": "Energy Policy", "is_preprint": 0}])
    row = conn.execute("SELECT venue, is_preprint FROM items WHERE url='https://x/9'").fetchone()
    assert row["venue"] == "Energy Policy" and row["is_preprint"] == 0


def test_crossref_gives_up_when_the_publisher_deposits_nothing(monkeypatch):
    """Elsevier deposits no abstracts, so an unbounded backfill would spend a
    round-trip per item forever."""
    from argus.collectors import crossref
    calls = []
    monkeypatch.setattr(crossref, "fetch_abstract",
                        lambda s, doi, m: calls.append(doi) or "")
    items = [{"source_type": "paper", "doi": f"10.1016/{i}", "summary": ""} for i in range(50)]
    out = crossref.backfill(None, items, "x@y", limit=60)
    assert out["gave_up"] and out["filled"] == 0
    assert len(calls) == crossref.GIVE_UP_AFTER   # stopped, didn't grind through 50


def test_crossref_fills_when_the_publisher_does_deposit(monkeypatch):
    from argus.collectors import crossref
    monkeypatch.setattr(crossref, "fetch_abstract", lambda s, doi, m: "recovered abstract")
    items = [{"source_type": "paper", "doi": "10.3390/a", "summary": ""},
             {"source_type": "paper", "doi": "10.3390/b", "summary": "already here"},
             {"source_type": "news", "doi": "10.3390/c", "summary": ""}]
    out = crossref.backfill(None, items, "x@y")
    assert out["filled"] == 1
    assert items[0]["summary"] == "recovered abstract"
    assert items[1]["summary"] == "already here"   # never overwrites
    assert items[2]["summary"] == ""               # papers only


def test_crossref_strips_jats_markup():
    from argus.collectors import crossref
    raw = "<jats:title>Abstract</jats:title><jats:p>We model <jats:italic>grid</jats:italic> flows.</jats:p>"
    assert crossref._clean(raw) == "We model grid flows."


def test_parse_dois_cleans_and_dedupes(tmp_path):
    bib = tmp_path / "lib.bib"
    bib.write_text(
        '@article{a, title={X}, doi = {10.1016/J.APENERGY.2020.114679}}\n'
        '@article{b, title={Y}, doi = {https://doi.org/10.1109/tps.2019.123}}\n'
        '@article{c, title={Z}, doi={10.1016/j.apenergy.2020.114679}}\n'  # dup of a (case)
        '@misc{d, title={No DOI here}}\n', encoding="utf-8")
    dois = parse_dois(str(bib))
    assert dois == ["10.1016/j.apenergy.2020.114679", "10.1109/tps.2019.123"]


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _FakeZoteroSession:
    """Serves two pages of Zotero items then an empty page (pagination end)."""
    def __init__(self):
        self.calls = []
        self._pages = [
            [{"data": {"itemType": "journalArticle", "DOI": "10.1/AAA"}},
             {"data": {"itemType": "book", "DOI": ""}},                       # no DOI
             {"data": {"itemType": "journalArticle", "extra": "DOI: 10.1/bbb"}}],
            [{"data": {"DOI": "10.1/AAA"}}],                                   # dup (case)
        ]
    def get(self, url, **kw):
        start = kw.get("params", {}).get("start", 0)
        idx = start // 100
        return _FakeResp(self._pages[idx] if idx < len(self._pages) else [])


def test_zotero_dois_paginates_dedupes_and_reads_extra():
    dois = library.zotero_dois(_FakeZoteroSession(),
                               {"zotero_library_id": "12345", "zotero_library_type": "user"})
    assert dois == ["10.1/aaa", "10.1/bbb"]  # cleaned, deduped, DOI-from-extra picked up


def test_zotero_dois_noop_without_id():
    assert library.zotero_dois(_FakeZoteroSession(), {"zotero_library_id": "SET_ME"}) == []


def test_library_shortlist_in_digest():
    conn = store.connect(":memory:")
    cfg = sample_cfg()
    cfg["library"]["enabled"] = True
    store.upsert_items(conn, [{"title": "Cites your library paper", "url": "https://l/1",
                               "source": "library: cites your library", "source_type": "paper",
                               "published_at": days_ago(2)}])
    lid = store.compute_id({"url": "https://l/1"})
    store.apply_score(conn, lid, 9, "methods", "why", "keyword")
    ctx = digest.build(conn, cfg)
    assert ctx and ctx["library"] and ctx["library"][0]["title"].startswith("Cites")
    # library rows must not also appear in the workstream groups
    grouped = [it["title"] for g in ctx["groups"] for it in g["entries"]]
    assert "Cites your library paper" not in grouped


def test_digest_min_score_can_be_stricter_than_dashboard():
    conn = store.connect(":memory:")
    cfg = sample_cfg()
    cfg["library"]["enabled"] = False
    store.upsert_items(conn, [
        {"title": "Strong hit", "url": "https://d/1", "source": "arxiv",
         "source_type": "paper", "published_at": days_ago(1)},
        {"title": "Borderline hit", "url": "https://d/2", "source": "arxiv",
         "source_type": "paper", "published_at": days_ago(1)}])
    store.apply_score(conn, store.compute_id({"url": "https://d/1"}), 9, "methods", "w", "keyword")
    store.apply_score(conn, store.compute_id({"url": "https://d/2"}), 6, "methods", "w", "keyword")

    # default: digest floor follows show_threshold (6), so both are emailed
    titles = [it["title"] for g in digest.build(conn, cfg)["groups"] for it in g["entries"]]
    assert titles == ["Strong hit", "Borderline hit"]

    # raising digest.min_score keeps the dashboard as-is but tightens the email
    cfg["digest"]["min_score"] = 8
    titles = [it["title"] for g in digest.build(conn, cfg)["groups"] for it in g["entries"]]
    assert titles == ["Strong hit"]


def test_digest_sends_nothing_when_nothing_clears_the_floor():
    """An empty day is a valid outcome — no filler, no email."""
    conn = store.connect(":memory:")
    cfg = sample_cfg()
    cfg["library"]["enabled"] = False
    cfg["digest"]["min_score"] = 10
    store.upsert_items(conn, [{"title": "Mediocre", "url": "https://d/3", "source": "arxiv",
                               "source_type": "paper", "published_at": days_ago(1)}])
    store.apply_score(conn, store.compute_id({"url": "https://d/3"}), 7, "methods", "w", "keyword")
    assert digest.build(conn, cfg) is None


def test_prune_runs_trims_the_log_but_keeps_failure_detection():
    conn = store.connect(":memory:")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(5):
        conn.execute("INSERT INTO runs(run_at, source, fetched, new, ok, note) "
                     "VALUES(?,?,?,?,?,?)", (old, "arxiv", 0, 0, 0, "stale"))
    for _ in range(3):
        store.record_run(conn, "arxiv", 0, 0, False, "down")
    conn.commit()

    assert store.prune_runs(conn, keep_days=30) == 5
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    # the 3 recent failures must still trip the dashboard's warning banner
    assert "arxiv" in store.recent_source_failures(conn, streak=3)


def test_digest_guard_and_empty():
    conn = store.connect(":memory:")
    assert digest.is_due(conn, send_hour_utc=0)
    store.set_meta(conn, "last_digest_sent_at", store.now_iso())
    assert not digest.is_due(conn, send_hour_utc=0)
    assert digest.build(conn, sample_cfg()) is None


def test_dashboard_excludes_library_items(tmp_path):
    conn = store.connect(":memory:")
    store.upsert_items(conn, [
        {"title": "Public arxiv paper", "url": "https://r/1", "source": "arxiv",
         "source_type": "paper", "published_at": days_ago(2)},
        {"title": "MY-LIBRARY-only paper", "url": "https://r/2",
         "source": "library: cites your library", "source_type": "paper",
         "published_at": days_ago(2)}])
    for u in ("https://r/1", "https://r/2"):
        store.apply_score(conn, store.compute_id({"url": u}), 9, "methods", "why", "keyword")
    html = open(render.render(conn, sample_cfg(), str(tmp_path / "index.html")), encoding="utf-8").read()
    assert "Public arxiv paper" in html
    assert "MY-LIBRARY-only paper" not in html   # library items are digest-only
