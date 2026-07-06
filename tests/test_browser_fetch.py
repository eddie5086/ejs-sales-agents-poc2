"""Browser fetch source — offline behavior (no AWS, no playwright launch)."""
from __future__ import annotations

import pytest

import poc2.stages  # noqa: F401 — registers all sources
from poc2.pipeline.engine import StageContext
from poc2.pipeline.schema import StageConfig
from poc2.stages import browser_fetch
from poc2.stages.fetch import _SOURCES, fetch_pages


def _ctx(params: dict, account: dict) -> StageContext:
    stage = StageConfig(id="fetch_pages", kind="tool", strategy="fetch_pages", params=params)
    return StageContext(stage=stage, payload={"account": account}, outputs={})


MERIDIAN = {"account_id": "a", "bdr_id": "b", "name": "Meridian Robotics",
            "domain": "meridianrobotics.com"}
CHAIN = {"fetch": ["attached", "browser", "fixture"], "fixture_dir": "mocks/pages"}


def test_browser_source_is_registered():
    assert "browser" in _SOURCES


def test_browser_failure_falls_through_to_fixture(monkeypatch):
    monkeypatch.setattr(browser_fetch, "_collect",
                        lambda account, params: (_ for _ in ()).throw(RuntimeError("no aws")))
    pages = fetch_pages(_ctx(CHAIN, MERIDIAN))
    assert len(pages) == 2  # fixture served the identify lane


def test_browser_pages_win_over_fixture(monkeypatch):
    live = [{"url": "https://meridianrobotics.com/team", "text": "Jane Doe — VP of People " * 20}]
    monkeypatch.setattr(browser_fetch, "_collect", lambda account, params: live)
    pages = fetch_pages(_ctx(CHAIN, MERIDIAN))
    assert pages == live


def test_attached_still_wins_over_browser(monkeypatch):
    def boom(account, params):
        raise AssertionError("browser must not be tried when pages are attached")
    monkeypatch.setattr(browser_fetch, "_collect", boom)
    acct = {**MERIDIAN, "page_texts": ["Jane Doe — VP of People"]}
    pages = fetch_pages(_ctx(CHAIN, acct))
    assert pages == [{"url": "page_texts[0]", "text": "Jane Doe — VP of People"}]


def test_browser_skips_account_without_domain(monkeypatch):
    called = {"n": 0}

    def count(account, params):
        called["n"] += 1
        return []
    monkeypatch.setattr(browser_fetch, "_collect", count)
    acct = {"account_id": "a", "bdr_id": "b", "name": "NoDomain Co"}
    assert fetch_pages(_ctx({"fetch": ["browser"]}, acct)) == []
    assert called["n"] == 0  # no session attempted without a domain


def test_ddg_url_encodes_query():
    url = browser_fetch._ddg_url('"Acme Rockets" "VP People" LinkedIn')
    assert url.startswith("https://html.duckduckgo.com/html/?q=")
    assert "%22Acme+Rockets%22" in url


def test_identify_only_pipeline_config_is_valid():
    from pathlib import Path

    from poc2.pipeline.schema import load_pipeline
    root = Path(__file__).resolve().parent.parent
    cfg = load_pipeline(root / "pipelines" / "identify_only.yaml", base_dir=root)
    assert cfg.stage("fetch_pages").params["fetch"] == ["attached", "browser", "fixture"]


@pytest.mark.parametrize("pipeline", ["bdr_outreach.yaml"])
def test_bdr_outreach_declares_browser_in_chain(pipeline):
    from pathlib import Path

    from poc2.pipeline.schema import load_pipeline
    root = Path(__file__).resolve().parent.parent
    cfg = load_pipeline(root / "pipelines" / pipeline, base_dir=root)
    assert cfg.stage("fetch_pages").params["fetch"] == ["attached", "browser", "fixture"]