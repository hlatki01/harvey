"""Scout's web search: a self-hosted SearXNG goes first and fails over cleanly."""

from types import SimpleNamespace

import httpx
import pytest

from harvey.agents import scout as S


def _scout(searxng_url="http://searxng:8080"):
    sc = S.Scout.__new__(S.Scout)
    sc.env = SimpleNamespace(searxng_url=searxng_url, serper_api_key="")
    sc._queries_this_cycle = 0
    return sc


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(S.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.asyncio
async def test_searxng_is_used_first(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = request.url
        return httpx.Response(200, json={"results": [
            {"url": "https://acme.com/", "content": "Acme roofing"},
            {"url": "https://acme.com", "content": "dup"},
            {"title": "no url"},
        ]})
    _mock_httpx(monkeypatch, handler)

    async def never(*a):
        raise AssertionError("fell through")
    monkeypatch.setattr(S.Scout, "_search_duckduckgo", never)

    out = await _scout()._web_search("roofer denver")
    assert out == [("https://acme.com/", "Acme roofing")]
    assert seen["url"].params["format"] == "json" and seen["url"].host == "searxng"


@pytest.mark.asyncio
async def test_searxng_down_falls_over_to_next_backend(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no such host")
    _mock_httpx(monkeypatch, handler)

    async def ddg(self, q):
        return [("https://b.com", "b")]
    monkeypatch.setattr(S.Scout, "_search_duckduckgo", ddg)

    assert await _scout()._web_search("x") == [("https://b.com", "b")]
