"""Reading the web without a browser.

Driving a browser to answer a question is several fragile steps - open
it, wait for a tree, find the right link, hope the page exposes its
content - and it takes over the screen while it happens. Most research
questions only need the text.

Nothing here executes anything it fetches. Everything a page or a search
result says is DATA: it is quoted back, never obeyed.
"""

from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# Tags whose contents are never prose.
_SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer",
         "form", "iframe", "template", "aside"}

# Tags that end a line of text.
_BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
          "section", "article", "header", "blockquote", "pre"}


class _Reader(HTMLParser):
    """Turns a page into the text a person would actually read."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        # The title is checked first: it lives inside <head>, which is
        # skipped, so testing skip_depth first threw it away.
        if self._in_title:
            self.title += data.strip()
            return
        if self._skip_depth:
            return
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        # Collapse the ocean of whitespace a page leaves behind.
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _client():
    import httpx

    return httpx.Client(
        headers={"User-Agent": _UA, "Accept-Language": "en-GB,en;q=0.9"},
        follow_redirects=True,
        timeout=20.0,
    )


def _unwrap(url: str) -> str:
    """DuckDuckGo hands back its own redirect; the real link is inside."""
    if "duckduckgo.com/l/" not in url:
        return url
    try:
        target = parse_qs(urlparse(url).query).get("uddg")
        return target[0] if target else url
    except Exception:  # noqa: BLE001
        return url


_RESULT = re.compile(
    r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*class=[\"']result-link[\"'][^>]*>"
    r"(.*?)</a>",
    re.S | re.I,
)
_SNIPPET = re.compile(
    r"<td[^>]*class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", re.S | re.I
)


def _strip(fragment: str) -> str:
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def search(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Search the web. Returns title, url and snippet for each hit."""
    query = " ".join((query or "").split())
    if not query:
        return []

    with _client() as client:
        response = client.post(
            "https://lite.duckduckgo.com/lite/", data={"q": query}
        )
        response.raise_for_status()
        body = response.text

    titles = _RESULT.findall(body)
    snippets = _SNIPPET.findall(body)

    results: list[dict[str, str]] = []
    for i, (href, title) in enumerate(titles[:limit]):
        results.append({
            "title": _strip(title),
            "url": _unwrap(_html.unescape(href)),
            "snippet": _strip(snippets[i]) if i < len(snippets) else "",
        })
    return results


def fetch(url: str, max_chars: int = 6000) -> dict[str, Any]:
    """Read a page as text."""
    if not re.match(r"^https?://", url.strip(), re.I):
        url = "https://" + url.strip()

    with _client() as client:
        response = client.get(url)
        response.raise_for_status()
        kind = response.headers.get("content-type", "")
        body = response.text

    if "html" not in kind.lower() and body.lstrip()[:1] not in ("<",):
        text, title = body, ""
    else:
        reader = _Reader()
        reader.feed(body)
        text, title = reader.text(), reader.title

    truncated = len(text) > max_chars
    return {
        "url": str(response.url),
        "title": title[:200],
        "text": text[:max_chars],
        "truncated": truncated,
        "chars": len(text),
    }
