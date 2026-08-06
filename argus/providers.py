"""Pluggable LLM backends for scoring — bring your own model.

Two adapters cover essentially the whole field:

* ``anthropic`` — the official Anthropic SDK (Claude).
* ``openai``    — the OpenAI ``/chat/completions`` request shape, which nearly
  every other vendor and every local server speaks. Implemented over plain
  ``requests`` (already a dependency), so adding a provider costs nothing.

``PRESETS`` maps a friendly name onto a base URL, a key environment variable,
and a default model, so config.yaml can just say ``provider: groq``. A provider
that isn't in the table still works — set ``base_url`` and ``api_key_env``
yourself and ARGUS treats it as an OpenAI-compatible endpoint.

Nothing here is required: when the configured provider has no key, the caller
falls back to the free keyword scorer instead of failing the run.
"""
from __future__ import annotations

import os

import requests

from . import __version__
from .net import CONTACT_EMAIL

TIMEOUT = 120  # scoring a batch of 20 is slower than a feed fetch
# Plain UA — net.USER_AGENT is browser-shaped to get past publisher feeds that
# 403 bots; an LLM endpoint has no such quirk and deserves the honest string.
API_USER_AGENT = f"argus/{__version__} (+{CONTACT_EMAIL})"

# provider -> how to reach it. `free` is a documentation flag (README table);
# `needs_key` drives the degrade-to-keyword decision. Model IDs are defaults
# only — vendors rename models often, so check yours and override in config.
PRESETS: dict[str, dict] = {
    "anthropic": {
        "kind": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-haiku-4-5",
        "free": False,
        "note": "Claude Haiku 4.5 — $1/$5 per Mtok, the default.",
    },
    "openai": {
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "model": "gpt-4o-mini",
        "free": False,
        "note": "OpenAI.",
    },
    "groq": {
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "free": True,
        "note": "Free tier, rate-limited but ample for a 4-hourly radar.",
    },
    "gemini": {
        "kind": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.0-flash",
        "free": True,
        "note": "Google AI Studio free tier, via its OpenAI-compatible endpoint.",
    },
    "openrouter": {
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "free": True,
        "note": "Router across many vendors; models suffixed ':free' cost nothing.",
    },
    "deepseek": {
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "free": False,
        "note": "Very cheap per token.",
    },
    "together": {
        "kind": "openai",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "free": False,
        "note": "Open-weight model host.",
    },
    "ollama": {
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": None,
        "model": "llama3.1",
        "free": True,
        "needs_key": False,
        "note": "Fully local and free. `make run` only — a GitHub runner can't reach your laptop.",
    },
    "lmstudio": {
        "kind": "openai",
        "base_url": "http://localhost:1234/v1",
        "api_key_env": None,
        "model": "local-model",
        "free": True,
        "needs_key": False,
        "note": "Local, same caveat as ollama.",
    },
}

# config `mode:` values that mean "use an LLM". `haiku` is the pre-1.1 spelling
# and still resolves to Anthropic, so existing configs keep working untouched.
_LLM_MODES = {"llm", "haiku"}


def resolve(scoring: dict) -> dict:
    """Turn the config `scoring:` block into a concrete provider spec.

    Explicit config always wins over the preset, so an unknown provider is a
    valid choice as long as you supply `base_url` + `api_key_env`.
    """
    mode = str(scoring.get("mode", "keyword")).strip().lower()
    provider = str(scoring.get("provider") or "").strip().lower()
    if not provider:
        # `mode: groq` is a natural shorthand, and `mode: haiku` is the pre-1.1
        # spelling for Anthropic. Accept both rather than silently misresolving.
        provider = mode if mode in PRESETS else ("anthropic" if mode in _LLM_MODES else "")
    preset = PRESETS.get(provider, {})

    api_key_env = scoring.get("api_key_env", preset.get("api_key_env"))
    return {
        "provider": provider or "anthropic",
        "kind": scoring.get("kind") or preset.get("kind", "openai"),
        "model": scoring.get("model") or preset.get("model"),
        "base_url": str(scoring.get("base_url") or preset.get("base_url") or "").rstrip("/"),
        "api_key_env": api_key_env,
        # Local servers accept anything as a key; everything else needs one.
        "needs_key": preset.get("needs_key", True),
    }


def wants_llm(scoring: dict) -> bool:
    mode = str(scoring.get("mode", "keyword")).strip().lower()
    return mode in _LLM_MODES or mode in PRESETS


def has_credentials(spec: dict) -> bool:
    if not spec.get("needs_key"):
        return True
    env = spec.get("api_key_env")
    return bool(env and os.environ.get(env))


def describe(spec: dict) -> str:
    return f"{spec['provider']}:{spec['model']}"


def missing_key_hint(spec: dict) -> str:
    return (f"{spec['provider']} needs ${spec['api_key_env']} — "
            f"falling back to keyword scoring")


def complete(spec: dict, system_prompt: str, user: str, max_tokens: int = 4096) -> str | None:
    """One chat completion. Returns the assistant text, or None on any failure —
    scoring treats None as 'skip this batch', never as a fatal error."""
    if spec["kind"] == "anthropic":
        return _anthropic_complete(spec, system_prompt, user, max_tokens)
    return _openai_complete(spec, system_prompt, user, max_tokens)


def _anthropic_complete(spec, system_prompt, user, max_tokens) -> str | None:
    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=spec["model"], max_tokens=max_tokens, temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user}])
    except Exception:  # noqa: BLE001 — SDK already backs off 429/5xx
        return None
    return "".join(b.text for b in resp.content
                   if getattr(b, "type", None) == "text").strip()


def _openai_complete(spec, system_prompt, user, max_tokens) -> str | None:
    if not spec.get("base_url"):
        return None
    key = os.environ.get(spec["api_key_env"] or "", "") or "not-needed"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": API_USER_AGENT}
    body = {
        "model": spec["model"], "temperature": 0, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user}],
    }
    text = _post_chat(spec, headers, body)
    if text is None and "max_tokens" in body:
        # Newer OpenAI models renamed the field; every other vendor kept the
        # old one, so only pay for the retry when the first shape is rejected.
        body["max_completion_tokens"] = body.pop("max_tokens")
        text = _post_chat(spec, headers, body)
    return text


def _post_chat(spec, headers, body) -> str | None:
    try:
        resp = requests.post(f"{spec['base_url']}/chat/completions",
                             headers=headers, json=body, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001 — network, auth, quota, malformed body
        return None
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return None
