"""Web tools: search the web and read pages."""
from __future__ import annotations

import concurrent.futures
import logging
import re
from typing import Any
from urllib.parse import urlparse

import requests

from .base import Tool, ToolContext, ToolResult, obj

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
PAGE_CHARS = 3500
FETCH_TIMEOUT = 12


def domain_of(url: str) -> str:
    """Host name of *url* without a leading ``www.``."""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def extract_text(html: str) -> tuple[str, str]:
    """Return ``(title, readable text)`` extracted from an HTML document."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        text = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return "", re.sub(r"\s+", " ", text).strip()
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form",
                     "aside", "iframe"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines = [line.strip() for line in root.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if len(line) > 1)
    return title, re.sub(r"\n{3,}", "\n\n", text)


def fetch_page(url: str, max_chars: int = PAGE_CHARS) -> tuple[str, str]:
    """Download *url* and return ``(title, text)``; raises ``ValueError`` on failure."""
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Only http and https addresses can be read.")
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise ValueError(f"Could not load the page ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise ValueError(f"The site answered with HTTP {response.status_code}.")
    kind = response.headers.get("content-type", "")
    if "html" not in kind and "text" not in kind and "json" not in kind and "xml" not in kind:
        raise ValueError(f"Unsupported content type: {kind or 'unknown'}")
    if "html" in kind or "<html" in response.text[:2000].lower():
        title, text = extract_text(response.text)
    else:
        title, text = "", response.text
    return title, text[:max_chars]


def web_search(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("A search query is required.")
    max_results = max(1, min(int(args.get("max_results") or 5), 10))
    read_pages = max(0, min(int(args.get("read_pages") if args.get("read_pages") is not None
                                else 3), 5))
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ValueError("Web search needs the 'ddgs' package (pip install ddgs).") from exc
    try:
        raw = list(DDGS().text(query, max_results=max_results))
    except Exception as exc:  # ddgs raises its own exception types
        raise ValueError(f"The search engine did not answer ({type(exc).__name__}). "
                         "Check the internet connection and try again.") from exc
    results = [{"title": str(r.get("title") or ""), "url": str(r.get("href") or r.get("url") or ""),
                "snippet": str(r.get("body") or "")} for r in raw]
    results = [r for r in results if r["url"]]
    if not results:
        return ToolResult(True, f'No results for "{query}".', "no results",
                          details=f'No results for "{query}".')

    pages: dict[str, str] = {}
    to_read = results[:read_pages]

    def _read(result: dict[str, str]) -> tuple[str, str]:
        if ctx.cancel.is_set():
            return result["url"], ""
        ctx.progress({"domain": domain_of(result["url"])})
        try:
            _title, text = fetch_page(result["url"])
            return result["url"], text
        except ValueError as exc:
            log.info("Could not read %s: %s", result["url"], exc)
            return result["url"], ""

    if to_read:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(to_read)) as pool:
            for url, text in pool.map(_read, to_read):
                if text:
                    pages[url] = text

    blocks, detail_lines = [], []
    for number, result in enumerate(results, 1):
        block = f"[{number}] {result['title']}\nURL: {result['url']}\nSnippet: {result['snippet']}"
        if result["url"] in pages:
            block += f"\nPage content:\n{pages[result['url']]}"
        blocks.append(block)
        mark = " · read" if result["url"] in pages else ""
        detail_lines.append(f"- [{result['title'] or result['url']}]({result['url']}){mark}")
    content = (f'Search results for "{query}":\n\n' + "\n\n".join(blocks) +
               "\n\nAnswer briefly and cite the sources you used as Markdown links.")
    return ToolResult(True, content, f"{len(pages)} pages read",
                      details=f'**"{query}"**\n' + "\n".join(detail_lines))


def fetch_url(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("A URL is required.")
    if "://" not in url:
        url = "https://" + url
    ctx.progress({"domain": domain_of(url)})
    title, text = fetch_page(url, max_chars=10_000)
    return ToolResult(True, f"Title: {title}\nURL: {url}\n\n{text}",
                      f"read {domain_of(url)}", details=f"[{title or url}]({url})")


def web_tools() -> list[Tool]:
    """Web search and page reading tools."""
    return [
        Tool("web_search", "web search",
             "Search the web and read the top result pages. Use for anything current or "
             "factual you are not sure about.",
             obj({"query": {"type": "string"},
                  "max_results": {"type": "integer", "description": "1-10, default 5"},
                  "read_pages": {"type": "integer",
                                 "description": "How many top pages to read fully, default 3"}},
                 ["query"]),
             web_search, "searching", "search",
             lambda a: f'SEARCHING "{str(a.get("query") or "")[:48]}"'),
        Tool("fetch_url", "read page", "Download one web page and return its readable text.",
             obj({"url": {"type": "string"}}, ["url"]), fetch_url, "searching", "search",
             lambda a: f"READING {domain_of(str(a.get('url') or '')) or 'page'}"),
    ]
