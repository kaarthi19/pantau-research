# Pantau Research

A personal, low-maintenance radar for a research field. It watches the
literature and news — **papers** (OpenAlex, arXiv), **news** (Google News), and
**org reports** (RSS) — scores each item for relevance to *your* work, and
surfaces the good stuff on a phone-first dashboard and a once-a-day email.

`collect → store → filter (keyword prefilter → optional Haiku scoring) → dashboard + digest`.
Runs free on GitHub Actions every 4 hours; state lives in a committed SQLite
file. Works with **no API key** (keyword scoring) and **no email** (dashboard
only) — every capability degrades cleanly when its secret is absent.

> This is a **fork-per-person template**. Each researcher runs their own copy,
> pointed at their own topics. It ships configured for one example field
> (Southeast-Asia power systems) so it runs out of the box — replace that config
> with yours.

## Make it yours (≈15 min)

1. **Use this template / fork**, then in `config.yaml`: set `title`,
   `digest.recipient`, and your **workstream `tags`** (the buckets the scorer
   sorts items into — colors are Paul Tol *vibrant*).
2. In `registry/sources.yaml`, replace the example with your field:
   - `openalex.queries` — topic searches · `openalex.issns` — your journals
   - `arxiv.categories` — your arXiv categories · `gnews.queries` — news searches
   - `feeds` — org RSS/Atom feeds (confirm each returns items)
   - `keywords` — power the prefilter + the free keyword scorer; `keywords.tags.<key>`
     must match the `tags` keys in `config.yaml`
3. In `prompts/research.md`, rewrite the researcher profile + rubric + few-shots
   for your work. This is the system prompt used when Haiku scoring is on.
4. **Enable Pages:** Settings → Pages → `main` / `docs/`. The dashboard URL is
   public — this repo carries research only, so that's fine.
5. **Add secrets** (Settings → Secrets → Actions), all optional:
   - `ANTHROPIC_API_KEY` — switches scoring from keyword mode to Haiku (~$3–5/mo).
   - `DIGEST_SMTP_USER` / `DIGEST_SMTP_PASS` — a Gmail address + **app password**
     (account needs 2FA) for the daily digest.

## Local dev

```bash
make install    # pip install -r requirements.txt
make dry-run    # collect + per-source counts, no writes
make run        # full pipeline in keyword mode
make test       # offline unit tests
open docs/index.html
```

## How it works

- **Collectors** hit OpenAlex (queries + journal ISSNs), the arXiv Atom API,
  Google News RSS, and any org feeds — politely (honest UA, per-host spacing,
  `window_days` cutoff). A failing source logs and is skipped, never killing the run.
- **Filter** — a zero-cost prefilter (papers pass; news needs a topic hit;
  excluded terms drop) then a scorer: **Haiku** (batched, temperature 0, strict
  JSON) or **keyword** (the free fallback). Scores are write-once.
- **Dashboard** (`docs/index.html`) — self-contained, theme-aware, phone-first:
  a workstream legend, a Top Picks band (≥ `highlight_threshold`, last 48h), and a
  reverse-chron list (≥ `show_threshold`), refreshing itself every 10 min.
- **Digest** — one email per UTC day, grouped by workstream, with an optional
  one-paragraph Sonnet synthesis on top.

## Library seeding from your Zotero library (opt-in)

The radar can study your reference library and, once a week, surface a **top-N
shortlist** of recent papers that **cite something in your library** (new work
building on what you read) and papers **by the authors you read most** — the
"papers based on your papers' history." The shortlist lands in a dedicated *From
your library* block at the top of the digest.

### Automated — no weekly export (`source: zotero_api`, recommended)

If your library is synced to zotero.org, the pipeline pulls it over the read-only
[Zotero Web API](https://www.zotero.org/support/dev/web_api/v3/start) each run —
you never export a `.bib` by hand. Works for a **personal** library or a **shared
lab group** library (great for a lab: everyone's collective reading in one radar).

1. Create a **read-only** API key at <https://www.zotero.org/settings/keys> and add
   it as the `ZOTERO_API_KEY` Actions secret.
2. In `config.yaml → library`: set `enabled: true`, `source: zotero_api`,
   `zotero_library_type: user|group`, and `zotero_library_id:` (your numeric user
   id from the keys page, or the group id from the group URL).

It runs at most weekly (`every_days`), so it doesn't rebuild every 4 hours.

### Manual alternative (`source: bib`)

Set `source: bib` and drop an export at `library/zotero.bib`.

**Privacy:** only the **DOIs** are sent to OpenAlex (the same public API the rest
of the pipeline uses). The library contents / your `.bib` are **never committed**
(git-ignored) and never leave the machine otherwise. Implementation:
`radar/collectors/library.py`.

## Target journals

`registry/sources.yaml → openalex.issns` is your **target-journal watchlist** —
every new paper in those journals (within `window_days`) is pulled and scored.
Add or remove ISSNs to match your field (find a journal's ISSN on its homepage or
at portal.issn.org).

## Roadmap (not built yet)

- **Embedding similarity** — SPECTER2 / Semantic Scholar vectors for a
  finer "more like my library" signal than citation coupling.
- Author-watch lists, a searchable archive page, Zotero push for top scorers.

---

*Extracted from a two-track (research + careers) parent project; this is the
research half, made standalone and shareable.*
