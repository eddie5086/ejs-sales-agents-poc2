"""Page fetching for the identify composite (ported from poc1 page_fetcher,
Exa client dropped — poc2 reverses that decision; the AgentCore Browser
strategy lands in Phase 3).

The fallback chain is pipeline config now, not env plumbing:

    params:
      fetch: [attached, fixture]      # ordered; Phase 3 inserts `browser`
      fixture_dir: mocks/pages

- attached: the Account payload carries `page_texts` (str or {"url","text"}).
- fixture:  a local file {fixture_dir}/{domain}.json with [{"url","text"}].
- Nothing found -> [] (the prioritizer degrades gracefully and warns).

The engine checkpoints this stage (`fetch_pages`), so a replay reuses the same
page text instead of re-fetching — the identify lane then replays
byte-identical.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from poc2.pipeline.registry import register
from poc2.stages.common import REPO_ROOT, account_from


def _normalize(pages) -> List[dict]:
    out = []
    for i, p in enumerate(pages or []):
        if isinstance(p, dict):
            text = str(p.get("text") or "")
            if text.strip():
                out.append({"url": p.get("url") or f"page_texts[{i}]", "text": text})
        elif isinstance(p, str) and p.strip():
            out.append({"url": f"page_texts[{i}]", "text": p})
    return out


def _domain(account) -> Optional[str]:
    d = (getattr(account, "domain", None) or "").strip().lower()
    return re.sub(r"^www\.", "", d) or None


def _fetch_attached(account, ctx) -> List[dict]:
    return _normalize(getattr(account, "page_texts", None))


def _fetch_fixture(account, ctx) -> List[dict]:
    domain = _domain(account)
    if not domain:
        return []
    base = Path(ctx.params.get("fixture_dir", "mocks/pages"))
    if not base.is_absolute():
        base = REPO_ROOT / base
    try:
        return _normalize(json.loads((base / f"{domain}.json").read_text()))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


_SOURCES = {"attached": _fetch_attached, "fixture": _fetch_fixture}


def register_source(name: str, fn) -> None:
    """Extend the fetch chain (browser_fetch.py registers "browser" here)."""
    _SOURCES[name] = fn


@register("tool", "fetch_pages")
def fetch_pages(ctx) -> List[dict]:
    """Walk the config-declared chain; first source with pages wins."""
    account = account_from(ctx.payload)
    for source in ctx.params.get("fetch", ["attached", "fixture"]):
        fn = _SOURCES.get(source)
        if fn is None:
            raise ValueError(f"unknown fetch source in config: {source!r}")
        pages = fn(account, ctx)
        if pages:
            return pages
    return []
