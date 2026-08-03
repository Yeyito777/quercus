from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

from .session import CANVAS_BASE_URL

_BLOCKS = {
    "address", "article", "aside", "blockquote", "br", "div", "dl", "dt", "dd",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


class _CanvasHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._ignored = 0
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "template", "noscript"}:
            self._ignored += 1
            return
        if self._ignored:
            return
        if tag in _BLOCKS:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self._anchor_href = href if isinstance(href, str) else None
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "template", "noscript"}:
            self._ignored = max(0, self._ignored - 1)
            return
        if self._ignored:
            return
        if tag == "a" and self._anchor_href:
            candidate = urljoin(CANVAS_BASE_URL + "/", self._anchor_href)
            try:
                parsed = urlsplit(candidate)
            except ValueError:
                parsed = None
            if (
                parsed is not None
                and parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
            ):
                self.links.append({"url": candidate, "text": " ".join(self._anchor_text).strip()})
            self._anchor_href = None
            self._anchor_text = []
        if tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        self.parts.append(data)
        if self._anchor_href is not None:
            self._anchor_text.append(data)


def parse_html(value: Any) -> tuple[str, list[dict[str, str]]]:
    parser = _CanvasHTML()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (ValueError, TypeError):
        return str(value or "")[:1_000_000], []
    text = "".join(parser.parts).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in text.split("\n")]
    compact: list[str] = []
    for line in lines:
        if line or (compact and compact[-1]):
            compact.append(line)
    body = "\n".join(compact).strip()[:1_000_000]
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for link in parser.links:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        links.append(link)
    return body, links


def plain_text(value: Any) -> str:
    return parse_html(value)[0]
