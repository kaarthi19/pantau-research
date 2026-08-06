# Changelog

All notable changes to ARGUS are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`argus/__init__.py` holds the canonical version; the release workflow refuses to
publish a tag that disagrees with it.

## [Unreleased]

## [1.1.0] — 2026-08-06

First release under the ARGUS name, and the first intended for use outside a
single machine.

### Added

- **Any LLM can score, and several are free.** Scoring is no longer tied to
  Claude. `scoring.provider` accepts `anthropic`, `groq`, `gemini`,
  `openrouter`, `openai`, `deepseek`, `together`, `ollama`, or `lmstudio`;
  Groq, Gemini, and OpenRouter have usable free tiers, and Ollama/LM Studio run
  locally for nothing. Any other OpenAI-compatible endpoint works by setting
  `base_url` and `api_key_env` — including a lab-hosted gateway.
- **Continuous integration** (`.github/workflows/ci.yml`) — the test suite runs
  on every push and pull request across Python 3.11–3.13, checks that
  `registry/sources.yaml` keyword tags line up with the `config.yaml`
  workstreams, and proves the pipeline still runs with no API keys present.
- **GitHub Pages deployment** (`.github/workflows/pages.yml`) — the dashboard
  publishes automatically after each sweep. A fork needs no setup: the workflow
  enables Pages on first run, and skips with an explanatory notice where Pages
  isn't available instead of failing the run.
- **Releases** (`.github/workflows/release.yml`) — pushing a `v*` tag verifies
  the tag against `argus.__version__`, runs the tests, and publishes a GitHub
  Release using this file's matching section as the notes.
- `--out` on `python -m argus.run` to render the dashboard somewhere other than
  `docs/index.html`, so a test run never dirties the committed page.
- `digest.synthesis_provider`, for running the digest summary on a different
  provider than the scorer.
- **`digest.min_score`** — an email-only relevance floor. The dashboard and the
  digest previously shared `show_threshold`, so there was no way to browse
  widely while keeping the daily email tight. Defaults to `show_threshold`.
- `prune.keep_run_days` (30) — the per-source run log was never pruned and grew
  unboundedly, one row per source per sweep. Only the last 3 entries per source
  are ever read, for the dashboard's failure banner.
- `MIT LICENSE`.
- `--vacuum` and `make vacuum` to reclaim space after pruning. `store.vacuum()`
  existed but was unreachable dead code. It stays manual on purpose: VACUUM
  rewrites every page, which turns one sweep into a whole-file diff and defeats
  git's delta compression on the committed database.

### Changed

- **Renamed to ARGUS** — Autonomous Research Gathering Utility System. The
  Python package moved from `radar/` to `argus/` (`python -m argus.run`), the
  database from `data/radar.db` to `data/argus.db`, and the outbound
  User-Agent now identifies ARGUS and its version.
- `scoring.mode` is `llm` or `keyword`. The old `mode: haiku` still resolves to
  the Anthropic provider, so existing configs keep working unchanged.
- `digest.synthesis` is `llm` or `none` (was `sonnet`), and defaults to the
  scoring provider rather than hardcoding Claude.
- Runs now print which scorer was selected and why, so a silent downgrade to
  keyword scoring is visible in the log instead of being inferred from output.
- The dashboard's scorer badge names the provider (`groq`, `anthropic`) rather
  than always reading `haiku`.
- The pipeline workflow gained a 20-minute timeout and a rebase-and-retry around
  its final `git push`, so a manual dispatch racing the cron no longer discards
  a completed run.
- **Sweep cadence relaxed from every 4 hours to every 8.** Each sweep appends to
  the run log, so the database changes every run and one sweep is one commit.
  Nothing is lost by sweeping less often — collectors look back `window_days`
  (7), so a missed sweep is covered by the next.

### Fixed

- Two dashboard tests pinned absolute dates (`2026-07-13`) that fell outside the
  14-day render window as soon as time passed, so they had been failing since
  shortly after they were written. Fixtures are now relative to the run date.
  Nothing caught this because tests had never run in CI.
- A malformed or hallucinated `id` in a scoring response is now ignored rather
  than raising, and a failed batch leaves its items unscored for the next run
  instead of aborting the stage.

## [1.0.0] — 2026-07-14

Initial internal version, under the name Pantau Research.

### Added

- Collectors for OpenAlex (topic queries + journal ISSNs), the arXiv Atom API,
  Google News RSS, and arbitrary organisation RSS/Atom feeds, sharing one
  rate-limited HTTP session.
- SQLite storage with DOI-or-normalised-URL deduplication and write-once scores.
- Two-stage filtering: a zero-cost keyword prefilter, then Claude Haiku scoring
  with a free keyword scorer as the fallback.
- A self-contained, theme-aware dashboard and a daily grouped email digest.
- Opt-in Zotero library seeding — surfaces recent papers citing your library and
  new work by the authors you read most, via the read-only Zotero Web API.

[Unreleased]: https://github.com/kaarthi19/pantau-research/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/kaarthi19/pantau-research/releases/tag/v1.1.0
[1.0.0]: https://github.com/kaarthi19/pantau-research/releases/tag/v1.0.0
