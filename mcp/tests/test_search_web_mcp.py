import json
from types import SimpleNamespace
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import search_web_mcp


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")


class FakeAsyncClient:
    def __init__(self, text: str, status_code: int = 200):
        self._text = text
        self._status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, headers=None):
        return FakeResponse(self._text, self._status_code)


def make_ddg_html(entries):
    blocks = []
    for title, href, snippet in entries:
        blocks.append(
            f"""
            <div class="result">
              <a class="result__a" href="{href}">{title}</a>
              <a class="result__snippet">{snippet}</a>
            </div>
            """
        )
    return "<html><body>" + "\n".join(blocks) + "</body></html>"


@pytest.mark.asyncio
async def test_fetch_duckduckgo_results_parses_and_unwraps_redirect(monkeypatch):
    html = make_ddg_html(
        [
            (
                "Example",
                "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage",
                "Snippet one",
            )
        ]
    )
    monkeypatch.setattr(
        search_web_mcp.httpx,
        "AsyncClient",
        lambda timeout=20: FakeAsyncClient(html),
    )

    results = await search_web_mcp.fetch_duckduckgo_results("test", 3)

    assert results == [
        {
            "title": "Example",
            "url": "https://example.com/page",
            "snippet": "Snippet one",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_duckduckgo_results_limits(monkeypatch):
    html = make_ddg_html(
        [
            ("One", "https://example.com/1", "A"),
            ("Two", "https://example.com/2", "B"),
        ]
    )
    monkeypatch.setattr(
        search_web_mcp.httpx,
        "AsyncClient",
        lambda timeout=20: FakeAsyncClient(html),
    )

    results = await search_web_mcp.fetch_duckduckgo_results("test", 1)

    assert results == [{"title": "One", "url": "https://example.com/1", "snippet": "A"}]


def test_normalize_crawl_result_with_markdown_raw():
    result = SimpleNamespace(
        markdown=SimpleNamespace(raw_markdown="raw", fit_markdown="fit")
    )
    assert search_web_mcp.normalize_crawl_result(result) == {"content": "raw"}


def test_normalize_crawl_result_with_text():
    result = SimpleNamespace(text="hello")
    assert search_web_mcp.normalize_crawl_result(result) == {"content": "hello"}


def test_normalize_crawl_result_with_exception():
    error = ValueError("boom")
    assert search_web_mcp.normalize_crawl_result(error) == {
        "content": "",
        "error": "boom",
    }


@pytest.mark.asyncio
async def test_search_web_uses_limit_and_returns_output(monkeypatch):
    async def fake_summarize(content):
        if content:
            return {"summary": "sum", "summary_error": None}
        return {"summary": "", "summary_error": None}

    async def fake_fetch(query, limit):
        assert limit == search_web_mcp.MAX_RESULTS
        return [
            {"title": "One", "url": "https://example.com/1", "snippet": "A"},
            {"title": "Two", "url": "https://example.com/2", "snippet": "B"},
        ]

    async def fake_crawl(urls):
        return [
            SimpleNamespace(markdown=SimpleNamespace(raw_markdown="m1")),
            Exception("fail"),
        ]

    monkeypatch.setattr(search_web_mcp, "fetch_duckduckgo_results", fake_fetch)
    monkeypatch.setattr(search_web_mcp, "crawl_urls", fake_crawl)
    monkeypatch.setattr(search_web_mcp, "summarize_content", fake_summarize)

    result = await search_web_mcp.search_web("query", 10)

    assert result == {
        "results": [
            {
                "title": "One",
                "url": "https://example.com/1",
                "error": None,
                "summary": "sum",
            },
            {
                "title": "Two",
                "url": "https://example.com/2",
                "error": "fail",
            },
        ]
    }


def test_handle_request_missing_query():
    assert search_web_mcp.handle_request({}) == {"error": "Missing query"}


def test_handle_request_parses_top_k(monkeypatch):
    async def fake_search(query, top_k):
        return {"results": [{"query": query, "top_k": top_k}]}

    monkeypatch.setattr(search_web_mcp, "search_web", fake_search)

    result = search_web_mcp.handle_request({"query": "x", "top_k": "2"})

    assert result == {"results": [{"query": "x", "top_k": 2}]}


def test_handle_request_falls_back_when_top_k_invalid(monkeypatch):
    async def fake_search(query, top_k):
        return {"results": [{"query": query, "top_k": top_k}]}

    monkeypatch.setattr(search_web_mcp, "search_web", fake_search)

    result = search_web_mcp.handle_request({"query": "x", "top_k": "bad"})

    assert result == {"results": [{"query": "x", "top_k": 5}]}


def test_main_reads_json_and_writes_response(monkeypatch, capsys):
    async def fake_search(query, top_k):
        return {"results": [{"query": query, "top_k": top_k}]}

    monkeypatch.setattr(search_web_mcp, "search_web", fake_search)
    monkeypatch.setattr(
        "builtins.input", lambda: json.dumps({"query": "x", "top_k": 1})
    )

    search_web_mcp.main()
    output = capsys.readouterr().out.strip()

    assert json.loads(output) == {"results": [{"query": "x", "top_k": 1}]}
