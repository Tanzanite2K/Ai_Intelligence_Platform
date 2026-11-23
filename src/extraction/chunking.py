"""Token-aware, semantics-preserving chunking.

Goals
-----
1. **No 413s by construction** – every chunk is sized against the target
   provider's input budget using a *conservative* token estimate (over-counts
   rather than under-counts), with the prompt overhead subtracted.
2. **Keep semantically dense content** – boilerplate (cookie banners, nav,
   share links) is removed first; documents are split on structural
   boundaries (headings > paragraphs > sentences > hard cut), never mid-word;
   every chunk is prefixed with the document *lead* (title + first paragraph)
   so it stays self-describing; when a hard cap on chunk count applies,
   paragraphs are ranked by information density and the lowest are dropped
   (order preserved), and the drop is reported.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_BOILERPLATE = re.compile(
    r"^(accept( all)? cookies|we use cookies|cookie (policy|settings)|subscribe( to our newsletter)?|sign up|log ?in|"
    r"share (this|on)|follow us|advertisement|skip to (main )?content|all rights reserved|privacy policy|terms of (use|service)|"
    r"read more|related (articles|posts)|click here)\b",
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_DENSITY_CUES = re.compile(
    r"(\$\s?\d|\d+(\.\d+)?\s?(%|k|m|bn|million|billion)|\b(19|20)\d{2}\b|\b(free|freemium|pricing|per (month|user|seat)|enterprise|"
    r"paid|plan|trial|remote|hybrid|on-?site|posted|published|founded|employees|headquarter|raised|series [a-e]|seed|ceo|founder|"
    r"github|arxiv|stars|salary)\b)",
    re.I,
)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (no network, no vendor tokenizer).

    BPE tokenizers average ~4 chars/token for English; we use 3.2 for ASCII
    (over-estimate by ~25 %) and 1 token per non-ASCII char (CJK, emoji),
    then add 5 % headroom.
    """
    if not text:
        return 0
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ascii_n = len(text) - non_ascii
    return math.ceil((ascii_n / 3.2 + non_ascii) * 1.05)


def clean_document(text: str) -> str:
    lines = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        if _BOILERPLATE.match(line) and len(line) < 120:
            continue
        key = line.casefold()
        if len(line) < 200 and key in seen:  # repeated menu items / footers
            continue
        seen.add(key)
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def split_units(text: str) -> list[str]:
    """Structural units: paragraphs (blank-line separated); headings stay attached to the next paragraph."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    pending_heading = ""
    for p in paras:
        is_heading = len(p) < 90 and "\n" not in p and not p.endswith((".", "!", "?", ":"))
        if is_heading:
            pending_heading = (pending_heading + "\n" + p).strip()
            continue
        units.append((pending_heading + "\n" + p).strip() if pending_heading else p)
        pending_heading = ""
    if pending_heading:
        units.append(pending_heading)
    return units


def _split_oversize(unit: str, budget: int) -> list[str]:
    if estimate_tokens(unit) <= budget:
        return [unit]
    sentences = _SENT_SPLIT.split(unit)
    out, cur = [], ""
    for s in sentences:
        cand = (cur + " " + s).strip()
        if estimate_tokens(cand) <= budget:
            cur = cand
            continue
        if cur:
            out.append(cur)
        if estimate_tokens(s) <= budget:
            cur = s
        else:  # single giant "sentence" (tables, minified text): hard cut on whitespace
            words, cur = s.split(), ""
            for w in words:
                cand = (cur + " " + w).strip()
                if estimate_tokens(cand) > budget and cur:
                    out.append(cur)
                    cur = w
                else:
                    cur = cand
    if cur:
        out.append(cur)
    return out


def density(unit: str) -> float:
    words = max(1, len(unit.split()))
    caps = len(re.findall(r"\b[A-Z][a-zA-Z0-9]+", unit))
    cues = len(_DENSITY_CUES.findall(unit))
    return (cues * 3 + caps) / words


@dataclass(slots=True)
class ChunkPlan:
    chunks: list[str]
    lead: str
    total_tokens: int
    dropped_units: int = 0
    notes: list[str] = field(default_factory=list)


def plan_chunks(
    text: str,
    *,
    budget_tokens: int,
    prompt_overhead_tokens: int = 600,
    max_chunks: int = 8,
    overlap_units: int = 1,
    lead_tokens: int = 200,
) -> ChunkPlan:
    """Split ``text`` into chunks that each fit ``budget_tokens`` including prompt overhead."""
    text = clean_document(text)
    units = split_units(text)
    if not units:
        return ChunkPlan([], "", 0)
    lead = units[0]
    if estimate_tokens(lead) > lead_tokens:
        lead = _split_oversize(lead, lead_tokens)[0]
    usable = budget_tokens - prompt_overhead_tokens - estimate_tokens(lead) - 20
    if usable < 100:
        raise ValueError(f"budget {budget_tokens} too small for prompt overhead {prompt_overhead_tokens}")
    pieces: list[str] = []
    for u in units:
        pieces.extend(_split_oversize(u, usable))
    total = sum(estimate_tokens(p) for p in pieces)
    plan = ChunkPlan([], lead, total)

    # Fit-to-cap: if the doc needs more than max_chunks, drop lowest-density units.
    if total > usable * max_chunks:
        keep_budget = usable * max_chunks * 0.95
        ranked = sorted(range(len(pieces)), key=lambda i: (i != 0, -density(pieces[i])))
        kept, acc = set(), 0
        for i in ranked:
            t = estimate_tokens(pieces[i])
            if acc + t <= keep_budget:
                kept.add(i)
                acc += t
        plan.dropped_units = len(pieces) - len(kept)
        plan.notes.append(f"dropped {plan.dropped_units} low-density units to respect max_chunks={max_chunks}")
        pieces = [p for i, p in enumerate(pieces) if i in kept]

    cur: list[str] = []
    cur_t = 0
    for p in pieces:
        t = estimate_tokens(p)
        if cur and cur_t + t > usable:
            plan.chunks.append("\n\n".join(cur))
            tail = cur[-overlap_units:] if overlap_units else []
            tail_t = sum(estimate_tokens(x) for x in tail)
            cur, cur_t = (tail, tail_t) if tail_t + t <= usable else ([], 0)
        cur.append(p)
        cur_t += t
    if cur:
        plan.chunks.append("\n\n".join(cur))
    return plan


def with_lead(lead: str, chunk: str) -> str:
    if not lead or chunk.startswith(lead):
        return chunk
    return f"[DOCUMENT LEAD]\n{lead}\n[/DOCUMENT LEAD]\n\n{chunk}"


def bisect_chunk(chunk: str) -> tuple[str, str]:
    """Split a chunk roughly in half on the nearest paragraph/sentence boundary (413 recovery)."""
    mid = len(chunk) // 2
    for sep in ("\n\n", "\n", ". ", " "):
        left = chunk.rfind(sep, 0, mid)
        right = chunk.find(sep, mid)
        cands = [i for i in (left, right) if i > 0]
        if cands:
            cut = min(cands, key=lambda i: abs(i - mid)) + len(sep)
            return chunk[:cut].strip(), chunk[cut:].strip()
    return chunk[:mid], chunk[mid:]
