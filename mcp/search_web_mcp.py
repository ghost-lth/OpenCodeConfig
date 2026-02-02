from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List

import anyio
import httpx
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 3
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:latest")
SUMMARY_MAX_CHARS = 6000
_MODEL_CHECK = {"checked": False, "available": True, "error": None}


logging.basicConfig(level=logging.WARNING)
logging.getLogger("crawl4ai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def get_ollama_base_url() -> str:
    if "/api/" in OLLAMA_URL:
        return OLLAMA_URL.split("/api/", 1)[0]
    return OLLAMA_URL.rstrip("/")


async def ensure_model_available() -> Dict[str, Any]:
    if _MODEL_CHECK["checked"]:
        return _MODEL_CHECK
    base_url = get_ollama_base_url()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
        models = [m.get("name") for m in data.get("models", [])]
        if OLLAMA_MODEL not in models:
            _MODEL_CHECK.update(
                {
                    "checked": True,
                    "available": False,
                    "error": f"Model not found: {OLLAMA_MODEL}",
                }
            )
            return _MODEL_CHECK
        _MODEL_CHECK.update({"checked": True, "available": True, "error": None})
    except Exception as exc:
        _MODEL_CHECK.update({"checked": True, "available": False, "error": str(exc)})
    return _MODEL_CHECK


async def fetch_duckduckgo_results(query: str, limit: int) -> List[Dict[str, str]]:
    params = {"q": query}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(DUCKDUCKGO_HTML_URL, params=params, headers=headers)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    results: List[Dict[str, str]] = []
    for result in soup.select(".result"):
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if not link or not link.get("href"):
            continue
        href = link["href"]
        if href.startswith("//"):
            href = f"https:{href}"
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            if uddg:
                href = uddg
                parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com"):
            continue
        results.append(
            {
                "title": link.get_text(strip=True),
                "url": href,
                "snippet": snippet.get_text(" ", strip=True) if snippet else "",
            }
        )
        if len(results) >= limit:
            break
    return results


async def crawl_urls(urls: List[str]) -> List[Any]:
    if not urls:
        return []
    async with AsyncWebCrawler() as crawler:
        tasks = [crawler.arun(url=url) for url in urls]
        return await asyncio.gather(*tasks, return_exceptions=True)


def normalize_crawl_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, Exception):
        return {"content": "", "error": str(result)}
    content = ""
    if hasattr(result, "markdown"):
        content = result.markdown
        if hasattr(result.markdown, "raw_markdown"):
            content = result.markdown.raw_markdown or ""
    elif hasattr(result, "text"):
        content = result.text
    return {"content": content}


async def extract_facts(content: str, query: str) -> Dict[str, Any]:
    if not content:
        return {"facts": "", "facts_error": None}
    model_status = await ensure_model_available()
    if not model_status.get("available"):
        return {"facts": "", "facts_error": model_status.get("error")}
    snippet = content[:SUMMARY_MAX_CHARS]
    prompt = (
        "Answer the user query using only concrete facts found in the page. "
        "Do not include generic site descriptions or boilerplate. Ignore navigation, "
        "menus, legal, and layout. If key facts are missing, say 'not found'.\n\n"
        f"User query: {query}\n\n"
        f"Page content:\n{snippet}"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
        facts = data.get("response", "").strip()
        return {"facts": facts, "facts_error": None}
    except Exception as exc:
        return {"facts": "", "facts_error": str(exc)}


async def search_web(query: str, top_k: int) -> Dict[str, Any]:
    top_k = max(1, min(top_k, MAX_RESULTS))
    results = await fetch_duckduckgo_results(query, top_k)
    urls = [item["url"] for item in results]
    crawl_results = await crawl_urls(urls)

    payloads = [normalize_crawl_result(crawl) for crawl in crawl_results]
    facts_list = await asyncio.gather(
        *(extract_facts(payload.get("content", ""), query) for payload in payloads)
    )

    output: List[Dict[str, Any]] = []
    for item, payload, facts in zip(results, payloads, facts_list):
        entry = {
            "title": item["title"],
            "url": item["url"],
            "error": payload.get("error"),
        }
        facts_text = facts.get("facts")
        facts_error = facts.get("facts_error")
        if facts_text and not facts_error:
            entry["facts"] = facts_text
        output.append(entry)
    return {"results": output}


def parse_top_k(value: Any) -> int:
    if value is None:
        return MAX_RESULTS
    try:
        return int(value)
    except (TypeError, ValueError):
        return MAX_RESULTS


def build_tool_definition() -> types.Tool:
    return types.Tool(
        name="search_web",
        description="Search the web and return top results.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results",
                },
            },
            "required": ["query"],
        },
    )


def create_server() -> Server:
    server = Server("searchWeb", version="1.0.0")

    @server.list_tools()
    async def list_tools(_: types.ListToolsRequest) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[build_tool_definition()])

    @server.call_tool()
    async def call_tool(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        req = None
        tool_name = None
        arguments: Dict[str, Any] = {}
        if args and isinstance(args[0], types.CallToolRequest):
            req = args[0]
        elif len(args) >= 2 and isinstance(args[0], str):
            tool_name = args[0]
            if isinstance(args[1], dict):
                arguments = args[1]
        elif "req" in kwargs and isinstance(kwargs["req"], types.CallToolRequest):
            req = kwargs["req"]
        elif "name" in kwargs:
            tool_name = kwargs.get("name")
            if isinstance(kwargs.get("arguments"), dict):
                arguments = kwargs.get("arguments") or {}

        if req is not None:
            tool_name = req.params.name
            arguments = req.params.arguments or {}

        if tool_name != "search_web":
            raise ValueError(f"Unknown tool: {tool_name}")
        query = arguments.get("query") or arguments.get("q")
        if not query:
            return {"error": "Missing query"}
        top_k = parse_top_k(arguments.get("top_k") or arguments.get("limit"))
        return await search_web(str(query), top_k)

    return server


async def run_server() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    anyio.run(run_server)


if __name__ == "__main__":
    main()
