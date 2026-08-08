# ARGUS

**Autonomous Research Gathering Utility System** — a personal, low-maintenance
radar for a research field. It watches the literature and the news — **papers**
(OpenAlex, arXiv), **news** (Google News), and **org reports** (RSS) — scores
each item for relevance to *your* work, and surfaces the good stuff on a
phone-first dashboard and a once-a-day email.

`collect → store → filter (keyword prefilter → optional LLM scoring) → dashboard + digest`

Runs free on GitHub Actions every 8 hours; state lives in a committed SQLite
file. Works with **no API key** (keyword scoring) and **no email** (dashboard
only) — every capability degrades cleanly when its secret is absent, and a run
prints which scorer it actually used.

> This is a **fork-per-person template**. Each researcher runs their own copy,
> pointed at their own topics. It ships configured for one example field
> (Southeast-Asia power systems) so it runs out of the box — replace that config
> with yours.

## Make it yours (≈15 min)

1. **Use this template / fork.** In `config.yaml`, set `title`,
   `digest.recipient`, and your **workstream `tags`** — the buckets the scorer
   sorts items into (colors are Paul Tol *vibrant*).
2. In `registry/sources.yaml`, replace the example with your field:
   - `openalex.queries` — topic searches · `openalex.issns` — your journals
   - `arxiv.categories` — your arXiv categories · `gnews.queries` — news searches
   - `feeds` — org RSS/Atom feeds (confirm each returns items)
   - `keywords` — power the prefilter and the free keyword scorer;
     `keywords.tags.<key>` must match the `tags` keys in `config.yaml` (CI checks this)
3. In `prompts/research.md`, rewrite the researcher profile, rubric, and
   few-shot examples for your work. This is the system prompt used for LLM scoring.
4. **Pick a scorer** — see the table below. The free keyword scorer needs
   nothing; an LLM scorer needs one API key, and several are free.
5. **Turn on Actions and Pages.** Two one-time switches in your fork:
   - **Actions** tab → enable workflows (forks start with them disabled).
   - **Settings → Pages → Source: GitHub Actions** — needed once before the
     dashboard can publish. Skip it and everything still works; the sweep just
     warns that it couldn't publish, and you read `docs/index.html` locally.

## Scoring: bring your own model

Set `scoring.provider` in `config.yaml` and add the matching secret under
Settings → Secrets → Actions. Only the one you choose is needed.

| `provider`   | Secret               | Default model                   | Cost |
|--------------|----------------------|---------------------------------|------|
| `keyword`\*  | —                    | —                               | **Free**, no key, no network |
| `groq`       | `GROQ_API_KEY`       | `llama-3.3-70b-versatile`       | **Free tier** |
| `gemini`     | `GEMINI_API_KEY`     | `gemini-2.0-flash`              | **Free tier** |
| `openrouter` | `OPENROUTER_API_KEY` | `…-instruct:free`               | **Free** on `:free` models |
| `ollama`     | —                    | `llama3.1`                      | **Free**, runs locally |
| `anthropic`  | `ANTHROPIC_API_KEY`  | `claude-haiku-4-5`              | ~$1/$5 per Mtok |
| `openai`     | `OPENAI_API_KEY`     | `gpt-4o-mini`                   | paid |
| `deepseek`   | `DEEPSEEK_API_KEY`   | `deepseek-chat`                 | paid, cheap |
| `together`   | `TOGETHER_API_KEY`   | `Llama-3.3-70B-Instruct-Turbo`  | paid |

\* `keyword` is `scoring.mode: keyword` rather than a provider.

At the default settings a sweep scores at most 150 items every 4 hours, which
lands in single-digit dollars a month on a paid provider and inside the free
tier on Groq or Gemini. Model IDs move around — these are defaults, and
`scoring.model` overrides any of them.

**Anything OpenAI-compatible works**, including a model your lab already hosts.
Name it whatever you like and point ARGUS at it:

```yaml
scoring:
  mode: llm
  provider: lab-gateway
  base_url: https://gpu.your-lab.edu/v1
  api_key_env: LAB_GATEWAY_KEY
  model: mixtral-8x7b
```

`ollama` and `lmstudio` run against `localhost`, so they work for `make run` on
your own machine but not on a GitHub runner — a hosted runner can't reach your
laptop. Use a hosted provider for the scheduled sweep, or keyword scoring.

**If the key is missing, ARGUS scores with keywords and says so** rather than
failing the run:

```
scoring: keyword — groq needs $GROQ_API_KEY — falling back to keyword scoring
```

## Tell it what you're working on (`context/`)

Drop a concept note, proposal, chapter outline or reading list into `context/`
and its text is appended to the scoring prompt, so items are judged against your
actual project rather than a keyword list.

This is usually the highest-leverage tuning available. `registry/sources.yaml`
decides what gets **collected**; `context/` decides what counts as **relevant**
— and a two-page concept note says more about that than any number of query
strings. Plain text only (`.md`, `.txt`, `.rst`); the block is truncated at
`context.max_chars`. Each run reports what it loaded:

```
context: 2 reference documents, 8431 chars
```

**LLM scoring only** — the free keyword scorer matches term lists and doesn't
read these. Everything here is committed, so keep unpublished results and
anything under embargo out of it. See [context/README.md](context/README.md).

## Journals vs. preprints

Left alone, preprints dominate — and not on merit. OpenAlex carries **no
abstract for 60–82% of recent articles** from the large commercial publishers
(Elsevier deposits none to Crossref either), while arXiv always has one. So a
peer-reviewed paper gets scored on its title while a preprint gets scored on a
full abstract. Measured on this repo before the fix, arXiv was **57% of
everything above threshold**.

Two corrections, both configurable:

- `crossref_backfill` recovers missing abstracts by DOI. Free, no key. It gives
  up automatically after 10 consecutive misses, so for an Elsevier-heavy
  watchlist it costs ~10 requests a run and then stops — but it genuinely works
  for MDPI, Springer, Wiley, IEEE and Taylor & Francis.
- `scoring.venue_weight` nudges the score by where the work appeared, with an
  extra offset for a journal paper whose abstract is missing, since it was
  judged on a title alone.

On this repo's data that moved arXiv from 54% to 33% of what's shown, and
journal papers from 34 to 127. It also raises total volume — raise
`show_threshold` if you want the same amount with better composition.

Set both weights to `0` to rank purely on content, or make `preprint` positive
if you'd rather see preprints first.

## Relevance floors — and why an empty day is fine

Every item gets a 0–10 relevance score, and three parameters in `config.yaml`
decide what you actually see:

| Parameter              | Default | Controls |
|------------------------|---------|----------|
| `show_threshold`       | 6       | Dashboard floor — nothing below this is displayed |
| `highlight_threshold`  | 8       | The "Top Picks" band |
| `digest.min_score`     | *unset* | Email floor; defaults to `show_threshold`. Raise it to keep the daily email tighter than the browsable dashboard |

**ARGUS never tries to hit a quota.** There is no "find N papers" logic anywhere
— every limit in the codebase is a *ceiling* (`max_llm_items_per_run` caps
scoring cost, `LIMIT 8` caps the Top Picks band), never a floor. A sweep that
turns up nothing above threshold renders `No research items above threshold yet`
and sends no email that day. That is the intended behaviour, not a failure.

Scores are also **write-once**: an item scored today is never re-scored to make
it fit a later run. Raise the thresholds to make the radar pickier; lower them
to widen the net.

## The dashboard

`docs/index.html` is self-contained, theme-aware, and phone-first: a workstream
legend, a Top Picks band (≥ `highlight_threshold`, last 48h), and a reverse-chron
list (≥ `show_threshold`), refreshing every 10 minutes.

Every sweep publishes it to `https://<you>.github.io/<repo>/`. Three things
worth knowing:

- **Enable Pages once per repo**: Settings → Pages → Source: **GitHub Actions**.
  The workflow attempts this itself, but the token Actions runs with cannot
  create a Pages site — `pages: write` covers *deploying* to an existing site,
  not creating one. Until you flip it, the sweep still succeeds and warns that
  it couldn't publish.
- Pages on a **private** repo requires a paid GitHub plan. On a private free
  repo the workflow skips with a warning rather than failing the run.
- The dashboard URL is public once published. This repo carries research only —
  and library-seeded items are deliberately kept off the dashboard and confined
  to the private email digest, since they reveal your reading.

Publishing is a `publish` job inside the pipeline, not a separate
event-triggered workflow. That's deliberate: the sweep's own commit is made with
`GITHUB_TOKEN`, and GitHub never triggers a workflow from such a push, so no
`push:` trigger could ever see it.

## Email digest (optional)

One email per UTC day, grouped by workstream. Add `DIGEST_SMTP_USER` and
`DIGEST_SMTP_PASS` (a Gmail address plus an **app password**; the account needs
2FA). Set `digest.synthesis: llm` for a one-paragraph summary on top, which uses
your scoring provider unless `digest.synthesis_provider` says otherwise.

## Local dev

```bash
make install    # pip install -r requirements.txt
make dry-run    # collect + per-source counts, no writes
make run        # full pipeline
make test       # offline unit tests — no network, no keys
make vacuum     # reclaim DB space after pruning (occasional; see below)
open docs/index.html
```

### Sweep cadence and the commit log

Each sweep appends to a per-source run log, so the SQLite file changes every
run and **one sweep is one commit** — the cadence in
`.github/workflows/pipeline.yml` is the commit rate. Sweeping less often costs
nothing: collectors look back `window_days` (7), so anything a missed sweep
would have caught is picked up by the next one.

Storage is not a concern. Git delta-compresses SQLite well — at 8-hourly, a
year of history packs to roughly 30 MB.

`prune.keep_run_days` (30) caps the run log, which is otherwise unbounded; only
the last 3 entries per source are ever read, for the dashboard's
source-failure banner. Pruning frees pages inside the file but doesn't shrink
it — run `make vacuum` occasionally for that. It's deliberately manual: VACUUM
rewrites every page, which turns one sweep into a whole-file diff and defeats
git's delta compression.

## How it works

- **Collectors** hit OpenAlex (queries + journal ISSNs), the arXiv Atom API,
  Google News RSS, and any org feeds — politely (honest UA, per-host spacing,
  `window_days` cutoff). A failing source logs and is skipped, never killing the run.
- **Filter** — a zero-cost prefilter (papers pass; news needs a topic hit;
  excluded terms drop), then a scorer: an **LLM** (batched, temperature 0,
  strict JSON, one retry) or **keyword** (the free fallback). Scores are
  write-once, and a batch that fails leaves its items for the next run.
- **Digest** — one email per UTC day, grouped by workstream.

## Library seeding from your Zotero library (opt-in)

ARGUS can study your reference library and, once a week, surface a **top-N
shortlist** of recent papers that **cite something in your library** (new work
building on what you read) and papers **by the authors you read most**. The
shortlist lands in a dedicated *From your library* block at the top of the digest.

### Automated — no weekly export (`source: zotero_api`, recommended)

If your library syncs to zotero.org, the pipeline pulls it over the read-only
[Zotero Web API](https://www.zotero.org/support/dev/web_api/v3/start) each run.
Works for a **personal** library or a **shared lab group** library — good for a
lab, since it puts everyone's collective reading into one radar.

1. Create a **read-only** API key at <https://www.zotero.org/settings/keys> and
   add it as the `ZOTERO_API_KEY` Actions secret.
2. In `config.yaml → library`: set `enabled: true`, `source: zotero_api`,
   `zotero_library_type: user|group`, and `zotero_library_id` (your numeric user
   id from the keys page, or the group id from the group URL).

It runs at most weekly (`every_days`), so it doesn't rebuild every 4 hours.

### Manual alternative (`source: bib`)

Set `source: bib` and drop an export at `library/zotero.bib`.

**Privacy:** only the **DOIs** are sent to OpenAlex — the same public API the
rest of the pipeline uses. Library contents and your `.bib` are **never
committed** (git-ignored) and never leave the machine otherwise. Implementation:
`argus/collectors/library.py`.

## Target journals

`registry/sources.yaml → openalex.issns` is your **target-journal watchlist** —
every new paper in those journals (within `window_days`) is pulled and scored.
Add or remove ISSNs to match your field (find a journal's ISSN on its homepage
or at portal.issn.org).

## Releases

`argus/__init__.py` holds the canonical version. To cut one:

```bash
git tag v1.1.0 && git push origin v1.1.0
```

The `release` workflow verifies the tag against `__version__`, runs the tests,
and publishes a GitHub Release from the matching [CHANGELOG.md](CHANGELOG.md)
section. A tag that disagrees with the code fails instead of shipping.

## Contributing

Bug reports, fixes, and new providers are all welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

The one thing worth knowing up front, because ARGUS is a fork-per-person
template:

> **Issues you open on your own fork go to your own tracker, where nobody will
> see them.** Report problems on the **upstream** repo — the one you forked
> *from*. If the top of the page says *"forked from …"*, click through to the
> parent and file it there.

| You want to… | Do this |
|---|---|
| Change your topics, journals, feeds, thresholds, or prompt | Edit your fork. That's configuration, not a bug. |
| Report a broken feed or collector | [Open an issue](../../issues/new/choose) → *A source stopped working* |
| Report a bug in scoring, the dashboard, digest, or a workflow | [Open an issue](../../issues/new/choose) → *Bug report* |
| Suggest a provider or feature | [Open an issue](../../issues/new/choose) → *Feature or provider request* |
| Send a fix | Fork → branch → `make test` → PR. CI must be green. |

Two things make a report immediately actionable: **the scoring line** the run
prints (`scoring: keyword — groq needs $GROQ_API_KEY — …`), and **which source**
is failing (`make dry-run` gives per-source counts without writing anything).
Never paste an API key or your library contents.

**ARGUS reports its own failures.** Because it runs unattended, a broken sweep
would otherwise be invisible until someone opened the Actions tab. The `alert`
workflow opens an issue when the pipeline fails, keeps a single thread for
repeat failures, and closes it automatically once a sweep succeeds. Set the
`ALERT_ASSIGNEE` repository variable to a username to route those to a person.

## License

[MIT](LICENSE) — fork it, change it, use it however helps your work.

## Roadmap (not built yet)

- **Embedding similarity** — SPECTER2 / Semantic Scholar vectors for a finer
  "more like my library" signal than citation coupling.
- Author-watch lists, a searchable archive page, Zotero push for top scorers.

---

*Extracted from a two-track (research + careers) parent project; this is the
research half, made standalone and shareable.*
