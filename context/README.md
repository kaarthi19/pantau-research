# Reference documents

Drop documents here that describe **what you are actually working on**, and the
scorer will judge every paper and article against them.

This is usually the highest-leverage tuning available. A two-page concept note
tells the scorer more about what matters to you than any number of query strings
in `registry/sources.yaml` — those decide what gets *collected*, these decide
what counts as *relevant*.

## What to put here

- A concept note, proposal, or grant abstract
- A thesis chapter outline, or a description of the argument you're building
- A short "what I care about and what I don't" note — exclusions help as much as
  inclusions
- A reading list with a line on why each item mattered

## What not to put here

- **Anything confidential.** Everything in this folder is committed to the
  repository, and the repository may be public. Unpublished results, personal
  data, and anything under embargo do not belong here.
- Whole PDFs of papers. Only `.md`, `.txt`, `.markdown` and `.rst` are read —
  a PDF read as bytes would be noise, and adding a parser would cost a
  dependency this project deliberately avoids.
- Anything enormous. The combined text is truncated at `context.max_chars`
  (24,000 by default), so a large drop silently loses its tail. Two or three
  focused pages beat twenty unfocused ones.

## How it works

Every file is read in filename order, concatenated, and appended to the system
prompt used for scoring. Each run prints what it loaded:

```
context: 2 reference documents, 8431 chars
```

**LLM scoring only.** The free keyword scorer doesn't read these files — it
matches the term lists in `registry/sources.yaml`. If you're running without an
API key, set `scoring.provider` to a free option (`groq`, `gemini`) to get any
benefit from what you put here. See the scoring table in the README.

Turn it off with `context.enabled: false`, or point it elsewhere with
`context.dir`.

---

This README is skipped automatically — it's the folder's own documentation, not
a research note. Delete it if you like; the folder works either way.
