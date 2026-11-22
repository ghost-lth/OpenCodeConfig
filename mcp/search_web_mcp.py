from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict, List

import anyio
import httpx
from bs4 import BeautifulSoup
from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 3


logging.basicConfig(level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


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


async def search_web(query: str, top_k: int) -> Dict[str, Any]:
    top_k = max(1, min(top_k, MAX_RESULTS))
    results = await fetch_duckduckgo_results(query, top_k)
    # No additional page fetch or LLM post-processing; return DDG metadata only.
    for item in results:
        item.setdefault("error", "")
    return {"results": results}


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
