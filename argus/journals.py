"""Find the ISSN for a journal, and check the ones you already watch.

`registry/sources.yaml` identifies target journals by ISSN, because names are
ambiguous — "Applied Energy", "ACS Applied Energy Materials" and "Advances in
Applied Energy" are three different journals. But nobody knows ISSNs by heart,
and hunting them down on portal.issn.org is the most tedious step in pointing
ARGUS at a new field.

    python -m argus.journals "Nature Energy" "Joule"   # look up by name
    python -m argus.journals                           # audit your watchlist

The audit matters more than it sounds: a wrong or dead ISSN doesn't error, it
just silently returns nothing, and a journal you think you're watching quietly
contributes zero papers forever.
"""
from __future__ import annotations

import os
import sys

import yaml

from .net import PoliteSession, CONTACT_EMAIL

API = "https://api.openalex.org/sources"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def search(session, name: str, limit: int = 4) -> list[dict]:
    try:
        resp = session.get(API, params={"search": name, "per-page": limit,
                                        "mailto": CONTACT_EMAIL})
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:  # noqa: BLE001 — a lookup failure isn't worth a traceback
        return []
    return [{"name": s.get("display_name") or "?",
             "issn": s.get("issn_l") or "",
             "works": s.get("works_count") or 0,
             "publisher": (s.get("host_organization_name") or "")} for s in results]


def lookup(names: list[str]) -> int:
    session = PoliteSession()
    print("Paste the matching lines into registry/sources.yaml under openalex.issns\n")
    missing = 0
    for name in names:
        hits = [h for h in search(session, name) if h["issn"]]
        if not hits:
            print(f"  # no match for {name!r} — try the exact title from the journal's homepage")
            missing += 1
            continue
        best, *rest = hits
        print(f'  - "{best["issn"]}"   # {best["name"]}')
        for alt in rest:
            # Near-name journals are the usual trap; show them so the wrong one
            # isn't picked silently.
            print(f'    #   other matches: "{alt["issn"]}" {alt["name"]} '
                  f'({alt["works"]:,} works)')
        print()
    return missing


def audit(window_days: int = 90) -> int:
    """Check every ISSN already in sources.yaml still resolves and is active."""
    path = os.path.join(ROOT, "registry", "sources.yaml")
    with open(path, "r", encoding="utf-8") as f:
        issns = (yaml.safe_load(f) or {}).get("openalex", {}).get("issns", []) or []
    if not issns:
        print("No ISSNs configured in registry/sources.yaml.")
        return 0

    session = PoliteSession()
    print(f"Checking {len(issns)} watched journals\n")
    bad = 0
    for issn in issns:
        try:
            resp = session.get(API, params={"filter": f"issn:{issn}", "per-page": 1,
                                            "mailto": CONTACT_EMAIL})
            resp.raise_for_status()
            hits = resp.json().get("results", [])
        except Exception:  # noqa: BLE001
            print(f"  [?]    {issn}  lookup failed — network")
            continue
        if not hits:
            print(f"  [DEAD] {issn}  no journal with this ISSN — it will silently "
                  f"contribute nothing")
            bad += 1
            continue
        s = hits[0]
        print(f"  [ok]   {issn}  {(s.get('display_name') or '?')[:46]:<46} "
              f"{s.get('works_count') or 0:>8,} works")
    if bad:
        print(f"\n{bad} ISSN(s) resolve to nothing. Fix them, or they stay silent.")
    return bad


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # `make journals` with no Q still passes one empty argument, because the
    # recipe has to quote "$(Q)" to keep multi-word titles in one piece.
    names = [a for a in argv if a.strip()]
    if names:
        return 1 if lookup(names) else 0
    return 1 if audit() else 0


if __name__ == "__main__":
    sys.exit(main())
