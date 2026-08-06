# Contributing to ARGUS

Thanks for helping. ARGUS is a small tool with two kinds of users, and what you
should do depends on which one you are today.

## First: is this your copy, or the shared one?

ARGUS is a **fork-per-person template**. You run your own copy, pointed at your
own field. That makes one thing easy to get wrong:

> **Issues you open in your own fork go to your own tracker, where nobody will
> see them.** Report problems on the upstream repository —
> **[Power-Lab/argus](https://github.com/Power-Lab/argus/issues/new/choose)** —
> not on your fork.

If the top of the page says *"forked from …"*, you're on your own copy. Click
through to the parent and file it there.

### What belongs upstream, and what doesn't

| Change | Where |
|---|---|
| `config.yaml` thresholds, tags, recipient | **Your fork only.** These are yours. |
| `registry/sources.yaml` queries, journals, feeds | **Your fork only.** |
| `prompts/research.md` profile and rubric | **Your fork only.** |
| A collector is broken, or a feed 403s for everyone | **Upstream issue** |
| A bug in scoring, rendering, the digest, or a workflow | **Upstream issue or PR** |
| A new provider, collector, or feature | **Upstream — open an issue first** |

The rule of thumb: if the change describes *your research*, keep it in your
fork. If it changes how ARGUS *works*, bring it upstream.

Keeping your fork current — add the upstream as a remote once, then pull in
changes whenever you like:

```bash
git remote add upstream https://github.com/Power-Lab/argus.git
git fetch upstream && git merge upstream/main
```

`config.yaml`, `registry/sources.yaml`, and `prompts/research.md` are the files
most likely to conflict, since those are exactly the ones you've made yours.
Keep your versions.

## When ARGUS reports itself

You don't have to notice a broken sweep. If the pipeline fails, the `alert`
workflow opens an issue automatically with a link to the failed run and a
checklist. It keeps **one** issue open — repeat failures become comments — and
closes it on its own when a sweep next succeeds.

So an issue labelled `pipeline-failure` that you didn't file is ARGUS telling
you something. Most of the time it's GitHub infrastructure (`The job was not
acquired by Runner of type hosted`), which clears by itself; the issue will
close after the next good run.

To route those to a person, set the repository variable `ALERT_ASSIGNEE` to a
GitHub username under **Settings → Secrets and variables → Actions →
Variables**. Without it the workflow assigns the repository owner, which works
for a personal fork but not for an org-owned repo.

## Reporting a problem

Open an issue on the upstream repo. The templates will ask for what's needed;
the two things that make a report immediately actionable are:

**1. The scoring line from the run.** Every run prints which scorer it actually
used and why. It is the single most useful line in the log:

```
scoring: keyword — groq needs $GROQ_API_KEY — falling back to keyword scoring
```

**2. Which source, if a source is the problem.** The dashboard footer shows a
warning when a source has failed three times running. `make dry-run` prints
per-source counts without writing anything, which is usually enough to pin it
down:

```
=== DRY RUN — per-source counts (no writes) ===
  [ok ] openalex: 41 items
  [FAIL] rss:carbonbrief: HTTPError: 403 Client Error
```

Never paste an API key, a `.bib` export, or the contents of your library.

## Opening a pull request

1. **Open an issue first** for anything beyond a small fix, so we don't both
   build the same thing differently.
2. Fork, then branch: `git checkout -b fix/short-description`
3. Make the change. Match the surrounding style — the codebase favours short
   modules, comments that explain *why* rather than *what*, and graceful
   degradation over hard failure.
4. **Add or update a test.** `tests/test_argus.py` runs fully offline: no
   network, no API keys. Anything that talks to a provider or a feed gets
   stubbed. If your change fixes a bug, the test should fail without your fix.
   - Dates in fixtures must be **relative** (`days_ago(2)`), never absolute.
     Hardcoded dates fall outside the render window as time passes and silently
     rot the suite — this has already happened once.
5. Run `make test` locally.
6. Push and open the PR. CI runs the suite on Python 3.11–3.13, checks that
   `registry/sources.yaml` tags line up with `config.yaml`, and verifies the
   pipeline still runs with no secrets present.

Small, focused PRs get reviewed faster than large ones. If a change is going to
be big, say so in the issue first.

### Things that will get asked in review

- **Does it still work with no API key?** Every capability degrades: no key
  means keyword scoring, no SMTP means no digest. Don't add a hard dependency
  on a secret.
- **Does a failure stay contained?** One dead feed logs and is skipped; it never
  kills a run. New collectors and providers should behave the same way.
- **Is a new dependency really necessary?** `requirements.txt` is deliberately
  five lines. The OpenAI-compatible provider adapter is written against
  `requests` specifically to avoid adding an SDK per vendor.

## Local development

```bash
make install    # pip install -r requirements.txt
make test       # offline unit tests
make dry-run    # per-source counts, no writes
make run        # full pipeline against your local config
```

Add a provider by extending `PRESETS` in `argus/providers.py`. If it speaks the
OpenAI `/chat/completions` shape, a preset entry is the whole change — no new
adapter, no new dependency.

## Releases (maintainer)

1. Bump `__version__` in `argus/__init__.py`.
2. Add a `## [x.y.z]` section to `CHANGELOG.md`.
3. Tag and push:

   ```bash
   git tag v1.2.0 && git push origin v1.2.0
   ```

The release workflow refuses to publish if the tag disagrees with
`__version__`, if the tests fail, or if `CHANGELOG.md` has no matching section.

## Code of conduct

Be decent to each other. This is a research tool built to save people time —
keep the discussion practical and assume good faith.
