"""Name normalisation layers 1-4: Unicode, case, whitespace/punctuation, legal suffixes."""

from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = [
    "incorporated", "inc", "corporation", "corp", "company", "co", "limited", "ltd", "llc", "l l c", "llp", "lp", "plc",
    "gmbh", "ag", "sa", "sas", "sarl", "s a", "srl", "spa", "bv", "nv", "oy", "ab", "as", "aps", "kk", "pte", "pty",
    "private limited", "pvt ltd", "pvt", "pbc",
]
_SUFFIX_RE = re.compile(r"(?:[\s,]+(?:" + "|".join(re.escape(s) for s in sorted(LEGAL_SUFFIXES, key=len, reverse=True)) + r")\.?)+\s*$", re.I)
_PUNCT = re.compile(r"[^\w\s]", re.U)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def unicode_clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace(" ", " ").replace("​", "")
    return strip_accents(s)


def strip_legal_suffix(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _SUFFIX_RE.sub("", s).strip(" ,.-")
    return s


def normalize_name(raw: str) -> str:
    """Spaced, lower-case, punctuation-free, legal-suffix-free form: 'OpenAI, Inc.' -> 'openai'."""
    s = unicode_clean(raw).casefold().strip()
    s = s.replace("&", " and ").replace("+", " plus ")
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"\(.*?\)", " ", s)  # "Meta (Facebook)" -> "meta"
    s = _PUNCT.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = strip_legal_suffix(s)
    return re.sub(r"\s+", " ", s).strip()


def compact_key(raw: str) -> str:
    """Space-free key so 'Open AI' == 'OpenAI' == 'OPENAI'."""
    return normalize_name(raw).replace(" ", "")


def display_name(raw: str) -> str:
    """Human canonical form for a *new* entity: original casing, suffix removed."""
    s = re.sub(r"\s+", " ", unicode_clean(raw)).strip()
    s = strip_legal_suffix(s)
    return s.strip(" ,.-") or raw.strip()
