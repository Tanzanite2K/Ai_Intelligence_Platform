"""HTML -> main-content text extraction (readability-style heuristics, no network)."""

from __future__ import annotations

import html as html_lib
import re

from selectolax.parser import HTMLParser, Node

_DROP = "script,style,noscript,nav,footer,header,aside,form,iframe,svg,button,figure figcaption,[role=navigation],[aria-hidden=true]"
_NOISE_CLASS = re.compile(r"(comment|share|social|related|newsletter|subscribe|promo|advert|sidebar|cookie|footer|nav|breadcrumb)", re.I)


def html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    tree = HTMLParser(html_lib.unescape(fragment) if "&lt;" in fragment else fragment)
    for n in tree.css("script,style"):
        n.decompose()
    text = tree.text(separator="\n")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _paragraph_text(node: Node) -> str:
    parts = [p.text(strip=True) for p in node.css("p, h2, h3, li, blockquote")]
    return "\n".join(p for p in parts if len(p) > 25)


def extract_main_text(html: str) -> str:
    """Return the article body: prefer <article>/[itemprop=articleBody], else the densest container."""
    tree = HTMLParser(html)
    for n in tree.css(_DROP):
        n.decompose()
    for n in tree.css("[class],[id]"):
        cls = (n.attributes.get("class") or "") + " " + (n.attributes.get("id") or "")
        if _NOISE_CLASS.search(cls) and n.tag not in ("article", "main", "body", "html"):
            n.decompose()
    for sel in ('[itemprop="articleBody"]', "article", "main", '[class*="article-body"]', '[class*="entry-content"]', '[class*="post-content"]'):
        node = tree.css_first(sel)
        if node:
            txt = _paragraph_text(node)
            if len(txt) > 400:
                return txt
    best, best_len = "", 0
    for node in tree.css("div, section"):
        txt = _paragraph_text(node)
        if len(txt) > best_len:
            best, best_len = txt, len(txt)
    return best
