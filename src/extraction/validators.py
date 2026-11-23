"""Anti-hallucination validation of LLM output.

The LLM must return, for every field, ``{"value": ..., "evidence": "<verbatim quote>"}``.
A value is accepted only if:

1. its evidence quote occurs in the *source chunk* (whitespace/case/quote-
   insensitive; for long quotes a 90 % fuzzy partial match), and
2. the value is *supported by* the quote: strings/list items must appear in
   the quote or source; numbers must appear as digits (``1,200`` ~ ``1200``);
   enums must have a lexical cue in the quote; booleans need a cue word.

Anything else is nulled and reported — the system never "fills in" a field.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from src.extraction.providers.base import MalformedResponseError

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)


def parse_json_loose(text: str) -> dict[str, Any]:
    """Parse model output that should be JSON; tolerate code fences and prose around one object."""
    t = _FENCE.sub("", text.strip())
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        start = t.find("{")
        if start < 0:
            raise MalformedResponseError("no JSON object in response") from None
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start : i + 1])
                        break
                    except json.JSONDecodeError as e:
                        raise MalformedResponseError(f"invalid JSON: {e}") from e
        else:
            raise MalformedResponseError("unterminated JSON object (incomplete response)")
    if not isinstance(obj, dict):
        raise MalformedResponseError("top-level JSON is not an object")
    return obj


# ', ' is deliberately NOT a quote separator: commas occur inside real quotes, and splitting on them would turn
# "Baseten, Google and Hugging Face partnered" into bare-name fragments that look like quotes.
_SEGMENT_SPLIT = re.compile(r"\s*(?:\|\s*|\n+|;\s+|\.{3,}|\u2026)\s*")


def split_quotes(evidence: str) -> list[str]:
    """Split an evidence string into candidate verbatim quotes.

    Models join several quotes with ' | ' (as instructed) but also with '; ', newlines or '...'.
    Splitting only ever yields *shorter substrings*, so a segment can be found in the source only if the model
    really quoted it; segments shorter than 3 characters are discarded.
    """
    parts = [p.strip(" \"'\u201c\u201d\u2018\u2019") for p in _SEGMENT_SPLIT.split(evidence)]
    return [p for p in parts if len(p) >= 3]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().casefold()


def quote_in_source(quote: str, source: str) -> bool:
    q, s = norm(quote), norm(source)
    if not q:
        return False
    if q in s:
        return True
    if len(q) >= 25:
        return fuzz.partial_ratio(q, s) >= 90
    return False


ENUM_CUES: dict[str, dict[str, re.Pattern[str]]] = {
    "pricing_model": {
        "FREE": re.compile(r"\b(free|open[- ]source|no cost|\$0)\b", re.I),
        "FREEMIUM": re.compile(r"\b(freemium|free (plan|tier|trial|version)|free to (start|try)|upgrade|pro plan|premium)\b", re.I),
        "PAID": re.compile(r"(\$\s?\d|€\s?\d|£\s?\d|\b(per|/)\s?(month|mo|year|yr|user|seat)\b|\bpaid\b|\bsubscription\b|\bpricing\b)", re.I),
        "ENTERPRISE": re.compile(r"\b(enterprise|contact (sales|us)|custom pricing|request (a )?demo)\b", re.I),
    }
}
_BOOL_CUES = re.compile(r"\b(remote|on-?site|hybrid|in[- ]office|anywhere|yes|no|true|false|not)\b", re.I)


def _num_in(n: int | float, text: str) -> bool:
    digits = re.sub(r"[,\s_]", "", text)
    return str(int(n)) in digits if float(n).is_integer() else str(n) in digits


def value_supported(field_name: str, value: Any, quote: str, source: str) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return bool(_BOOL_CUES.search(quote))
    if isinstance(value, (int, float)):
        return _num_in(value, quote)
    if isinstance(value, str):
        cues = ENUM_CUES.get(field_name)
        if cues and value in cues:
            return bool(cues[value].search(quote))
        v = norm(value)
        return v in norm(quote) or v in norm(source) or fuzz.partial_ratio(v, norm(quote)) >= 92
    if isinstance(value, list):
        return all(value_supported(field_name, v, quote, source) for v in value)
    return False


# Publisher / syndication boilerplate. A name supported ONLY by text like this is the outlet carrying the article,
# not something the article reports on. If the article body also names it, that body quote supports it normally.
_ATTRIBUTION = re.compile(
    r"(appeared first on|originally (?:published|appeared) (?:on|in|at)|\bthe post\b.{0,200}\bappeared\b|"
    r"^\s*(?:source|via|credit|image credit|photo credit|photo|image)\s*[:\-]|\u00a9|\(c\)\s*\d{4}|\bcopyright\b|"
    r"all rights reserved|connect with us|follow us on|subscribe to (?:our|the) newsletter|sign up for (?:our|the) newsletter)",
    re.I,
)

_CORP_SUFFIX = re.compile(r"^(inc|ltd|llc|llp|corp|corporation|co|gmbh|plc|s\.?a|a\.?g|n\.?v|pte|pvt)\.?$", re.I)


def is_attribution(quote: str) -> bool:
    return bool(_ATTRIBUTION.search(quote))


def split_name_list(text: str) -> list[str]:
    """Conservative split of a comma/semicolon/newline-joined name string (fallback only).

    Corporate suffixes ('Hugging Face, Inc.') are re-attached to the preceding name. Every resulting name is then
    validated independently, exactly like an element of a proper JSON array."""
    out: list[str] = []
    for part in re.split(r"\s*[,;\n]\s*", text):
        part = part.strip()
        if not part:
            continue
        if out and _CORP_SUFFIX.match(part):
            out[-1] = f"{out[-1]}, {part}"
        else:
            out.append(part)
    return out


def item_evidence(item: str, quotes: list[str], source_text: str) -> tuple[str | None, str]:
    """Find this item's OWN supporting quote. Returns (quote, reason).

    A quote supports an item only if (a) it occurs in the source under the existing normalisation rules
    (exact normalised substring - no fuzzy match, so a fabricated quote with a swapped-in name cannot pass),
    (b) it contains that item's name, and (c) it is more than the bare name. A quote naming one organisation
    therefore never validates another. Quotes that are only publisher/attribution boilerplate do not count."""
    src_n, v = norm(source_text), norm(item)
    if not v or v not in src_n:
        return None, "name does not occur in the source"
    mentioning = [q for q in quotes if v in norm(q)]
    if not mentioning:
        return None, "no evidence quote mentions this name"
    verified = [q for q in mentioning if norm(q) in src_n and norm(q) != v]
    if not verified:
        return None, "its quote is not verbatim in the source (fabricated or paraphrased)"
    substantive = [q for q in verified if not is_attribution(q)]
    if not substantive:
        return None, "only supported by publisher/attribution text, not by the article content"
    return substantive[0], "verified"


@dataclass
class FieldVerdict:
    value: Any
    evidence: str | None
    accepted: bool
    reason: str = ""
    item_reasons: dict[str, str] = field(default_factory=dict)  # list fields: per-item verdict (diagnostics)


@dataclass
class ValidationReport:
    fields: dict[str, FieldVerdict] = field(default_factory=dict)

    @property
    def accepted(self) -> dict[str, Any]:
        return {k: v.value for k, v in self.fields.items() if v.accepted and v.value is not None}

    @property
    def rejected(self) -> dict[str, str]:
        return {k: v.reason for k, v in self.fields.items() if not v.accepted}


def validate_fields(raw: dict[str, Any], allowed_fields: set[str], source_text: str) -> ValidationReport:
    """Validate the ``fields`` object of one chunk response against its source chunk."""
    fields = raw.get("fields", raw)
    if not isinstance(fields, dict):
        raise MalformedResponseError("'fields' must be an object")
    rep = ValidationReport()
    for name in allowed_fields:
        item = fields.get(name)
        if item is None:
            rep.fields[name] = FieldVerdict(None, None, True, "absent")
            continue
        if not isinstance(item, dict) or "value" not in item:
            rep.fields[name] = FieldVerdict(None, None, False, "missing value/evidence structure")
            continue
        value, ev = item.get("value"), item.get("evidence")
        if value in (None, "", []):
            rep.fields[name] = FieldVerdict(None, None, True, "not stated")
            continue
        if isinstance(ev, list):
            ev = " | ".join(str(x) for x in ev)
        if not isinstance(ev, str) or not ev.strip():
            rep.fields[name] = FieldVerdict(value, None, False, "no evidence quote")
            continue
        quotes = [q.strip() for q in ev.split(" | ") if q.strip()]
        if isinstance(value, list):
            # List fields (e.g. organisations): every item is verified INDEPENDENTLY - it must occur in the source
            # and have its own verbatim quote that names it (see item_evidence). Unsupported items are dropped
            # with a per-item reason; if none survive the field is rejected.
            quotes = split_quotes(ev)
            kept: list[str] = []
            used: list[str] = []
            reasons: dict[str, str] = {}
            for v in value:
                if not isinstance(v, str):
                    reasons[str(v)] = "not a string"
                    continue
                q, why = item_evidence(v, quotes, source_text)
                reasons[v] = why
                if q is not None and v not in kept:
                    kept.append(v)
                    used.append(q)
            failed = {k: r for k, r in reasons.items() if r != "verified"}
            if kept:
                note = f"verified {len(kept)}/{len(value)}" + (f"; dropped unsupported: {failed}" if failed else "")
                rep.fields[name] = FieldVerdict(kept, " | ".join(used)[:600], True, note[:600], reasons)
            else:
                rep.fields[name] = FieldVerdict(value, ev, False, f"possible hallucination - no item independently supported by its own verbatim quote: {failed}"[:600], reasons)
            continue
        if not all(quote_in_source(q, source_text) for q in quotes):
            rep.fields[name] = FieldVerdict(value, ev, False, "evidence not found in source (possible hallucination)")
            continue
        if not value_supported(name, value, ev, source_text):
            rep.fields[name] = FieldVerdict(value, ev, False, "value not supported by evidence")
            continue
        rep.fields[name] = FieldVerdict(value, ev[:300], True, "verified")
    extra = set(fields) - allowed_fields
    for name in extra:
        rep.fields[name] = FieldVerdict(None, None, False, "unexpected field ignored")
    return rep
