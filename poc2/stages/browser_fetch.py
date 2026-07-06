"""AgentCore Browser fetch source for the identify composite (Phase 3).

Replaces poc1's Exa decision (user reversal): an AWS-hosted AgentCore Browser
session (aws.browser.v1) searches the web and fetches pages — no third-party
search API. Implements the full BDRAWSRESEARCHTOOL contacts.md recipe poc1
left TODO:

  Pass A — team/leadership pages: direct candidate paths on the company
           domain plus a DuckDuckGo discovery search, fetched and text-extracted.
  Pass B — role-targeted searches: `"<company>" "<role>" LinkedIn` per
           size-appropriate committee role; the SERP text itself is captured
           (LinkedIn is auth-walled — the snippet evidence is what the
           enrichment agent needs for `linkedin_found`).
  Pass C — signals: one funding/hiring/payroll query, SERP text captured.

Config (identify stage params):
    fetch: [attached, browser, fixture]
    browser: {max_pages: 8, page_timeout_s: 20, roles: [...]}   # all optional

Failure policy: fetching is best-effort — ANY failure (SDK missing, no AWS
credentials, region without the Browser tool, timeouts) logs a warning and
returns [], so the config-declared chain falls through to the next source.
Replay safety comes from the engine: `fetch_pages` is checkpointed, so a
replay never opens a browser session.
"""
from __future__ import annotations

import urllib.parse
from typing import List

from poc2.stages.common import REPO_ROOT  # noqa: F401 (parity with fetch.py imports)
from poc2.stages.fetch import _domain, register_source

DDG = "https://html.duckduckgo.com/html/?q={query}"
TEAM_PATHS = ("about", "team", "leadership", "company", "about-us")
DEFAULT_ROLES = ("CEO", "VP People", "HR Director", "CFO", "IT Director")
MAX_TEXT_CHARS = 8000
MIN_PAGE_CHARS = 200


def _ddg_url(query: str) -> str:
    return DDG.format(query=urllib.parse.quote_plus(query))


def _page_text(page, url: str, timeout_ms: int) -> str:
    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    return page.inner_text("body", timeout=timeout_ms)[:MAX_TEXT_CHARS]


def _result_links(page, domain: str, limit: int = 3) -> List[str]:
    """Domain-filtered result links from a rendered DuckDuckGo HTML SERP."""
    links = []
    for a in page.query_selector_all("a.result__a")[:15]:
        href = a.get_attribute("href") or ""
        # DDG html endpoint wraps targets in /l/?uddg=<encoded>
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        if domain in urllib.parse.urlparse(href).netloc and href not in links:
            links.append(href)
        if len(links) >= limit:
            break
    return links


def _collect(account, params: dict) -> List[dict]:
    """Drive one browser session through the three passes. Raises on session
    failure; per-page failures are skipped."""
    from bedrock_agentcore.tools.browser_client import browser_session
    from playwright.sync_api import sync_playwright

    from poc2.config import settings

    opts = params.get("browser") or {}
    max_pages = int(opts.get("max_pages", 8))
    timeout_ms = int(opts.get("page_timeout_s", 20)) * 1000
    roles = opts.get("roles") or list(DEFAULT_ROLES)
    domain = _domain(account) or ""
    company = account.name

    pages_out: List[dict] = []
    seen_urls: set[str] = set()

    def keep(url: str, text: str) -> None:
        if url not in seen_urls and len(text.strip()) >= MIN_PAGE_CHARS:
            seen_urls.add(url)
            pages_out.append({"url": url, "text": text})

    with browser_session(settings.aws_region) as client:
        ws_url, headers = client.generate_ws_headers()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(ws_url, headers=headers)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()

            # Pass A: direct team-page candidates on the company domain …
            for path in TEAM_PATHS:
                if len(pages_out) >= max_pages:
                    break
                try:
                    keep(f"https://{domain}/{path}",
                         _page_text(page, f"https://{domain}/{path}", timeout_ms))
                except Exception:
                    continue
            # … plus SERP-discovered leadership pages on that domain.
            try:
                page.goto(_ddg_url(f"{company} leadership team site:{domain}"),
                          timeout=timeout_ms, wait_until="domcontentloaded")
                for link in _result_links(page, domain):
                    if len(pages_out) >= max_pages:
                        break
                    try:
                        keep(link, _page_text(page, link, timeout_ms))
                    except Exception:
                        continue
            except Exception:
                pass

            # Pass B: role-targeted LinkedIn searches (SERP text is the evidence).
            for role in roles:
                if len(pages_out) >= max_pages:
                    break
                url = _ddg_url(f'"{company}" "{role}" LinkedIn')
                try:
                    keep(url, _page_text(page, url, timeout_ms))
                except Exception:
                    continue

            # Pass C: signals (funding / hiring / payroll incumbent).
            if len(pages_out) < max_pages:
                url = _ddg_url(f'"{company}" funding OR hiring OR "payroll provider"')
                try:
                    keep(url, _page_text(page, url, timeout_ms))
                except Exception:
                    pass

            browser.close()
    return pages_out


def fetch_browser(account, ctx) -> List[dict]:
    """The `browser` entry in the fetch chain. Best-effort: any failure logs
    and returns [] so the chain falls through (e.g. to fixture)."""
    if not _domain(account):
        return []
    try:
        pages = _collect(account, ctx.params)
        print(f"  [browser_fetch] {len(pages)} page(s) via AgentCore Browser")
        return pages
    except Exception as e:
        print(f"  [browser_fetch] browser fetch failed ({type(e).__name__}: {e}); "
              "falling through")
        return []


register_source("browser", fetch_browser)
