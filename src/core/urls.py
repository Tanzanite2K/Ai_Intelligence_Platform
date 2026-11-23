"""URL canonicalisation and content hashing used for distributed de-duplication."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = re.compile(
    r"^(utm_.*|gclid|fbclid|mc_cid|mc_eid|ref|ref_src|ref_url|source|src|cmpid|igshid|_hsenc|_hsmi|mkt_tok|guccounter|"
    r"gh_src|lever-source|lever-origin|trk|trackingId|spm)$",
    re.I,
)
_MULTI_PART_TLDS = {"co.uk", "co.in", "com.au", "co.jp", "com.br", "co.kr", "com.cn", "org.uk", "ac.uk", "com.sg"}


def canonicalize_url(url: str) -> str:
    """Normalise a URL so the same resource always yields the same string.

    lower-cases scheme/host, strips ``www.``, default ports, fragments, tracking
    params and trailing slashes, and sorts the remaining query parameters.
    """
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING_PARAMS.match(k)))
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonicalize_url(url).encode()).hexdigest()


def registrable_domain(url: str) -> str:
    host = (urlsplit(url if "://" in url else "https://" + url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_PART_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def normalize_text_for_hash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def text_fingerprint(text: str) -> str:
    """Content hash robust to whitespace/punctuation/case changes (syndicated copies)."""
    return hashlib.sha256(normalize_text_for_hash(text).encode()).hexdigest()
