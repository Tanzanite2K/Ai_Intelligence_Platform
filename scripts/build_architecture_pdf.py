"""Render architecture.pdf (max 3 pages) with ReportLab. Run: python scripts/build_architecture_pdf.py"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
INK, MUTED, ACCENT, RULE = colors.HexColor("#1f2328"), colors.HexColor("#57606a"), colors.HexColor("#0b5cad"), colors.HexColor("#d0d7de")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=INK, spaceAfter=2)
SUB = ParagraphStyle("sub", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=12, textColor=MUTED, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=ACCENT, spaceBefore=7, spaceAfter=3)
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=11, leading=13.6, textColor=INK, alignment=TA_LEFT, spaceAfter=3)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=10, bulletIndent=1, spaceAfter=1.5)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=9.5, leading=11.5, spaceAfter=0)


def P(t: str) -> Paragraph:
    return Paragraph(t, BODY)


def B(items: list[str]) -> list[Paragraph]:
    return [Paragraph(i, BUL, bulletText="•") for i in items]


def diagram() -> Drawing:
    w, h = 180 * mm, 50 * mm
    d = Drawing(w, h)

    def box(x: float, y: float, bw: float, bh: float, title: str, sub: str = "", fill: str = "#f6f8fa") -> None:
        d.add(Rect(x, y, bw, bh, rx=4, ry=4, fillColor=colors.HexColor(fill), strokeColor=RULE, strokeWidth=0.8))
        d.add(String(x + bw / 2, y + bh - 11, title, fontName="Helvetica-Bold", fontSize=8, fillColor=INK, textAnchor="middle"))
        for i, line in enumerate(sub.split("|")):
            if line:
                d.add(String(x + bw / 2, y + bh - 21 - i * 9, line, fontName="Helvetica", fontSize=6.6, fillColor=MUTED, textAnchor="middle"))

    def arrow(x1: float, y1: float, x2: float, y2: float) -> None:
        d.add(Line(x1, y1, x2, y2, strokeColor=MUTED, strokeWidth=0.8))
        d.add(Polygon([x2, y2, x2 - 4, y2 + 2.2, x2 - 4, y2 - 2.2], fillColor=MUTED, strokeColor=MUTED))

    bh = 38 * mm
    y = 6 * mm
    cols = [
        ("Celery beat", "schedules|15 min: news/jobs|daily: bulk", "#ffffff"),
        ("Redis", "task queue|claims (SET NX)|rate limits", "#fff8e6"),
        ("Crawler workers", "asyncio + aiohttp|per-domain buckets|circuit breakers|robots.txt", "#eef6ff"),
        ("Normalise", "dates -> UTC|freshness gate|URL canon + hash", "#eef6ff"),
        ("LLM engine", "Gemini -> Groq|-> DeepSeek|chunk, 413/429|evidence check", "#f3eefe"),
        ("Entity res.", "normalise|exact/alias/domain|fuzzy+semantic|review queue", "#eefaf0"),
        ("PostgreSQL", "UNIQUE dedup|JSONB payload|checkpoints, DLQ", "#fff8e6"),
    ]
    n = len(cols)
    gap = 4 * mm
    bw = (w - gap * (n - 1)) / n
    for i, (t, s, f) in enumerate(cols):
        x = i * (bw + gap)
        box(x, y, bw, bh, t, s, f)
        if i:
            arrow(x - gap + 0.5, y + bh / 2, x - 0.5, y + bh / 2)
    d.add(String(w - 1, 1.2 * mm, "Exports: Google Sheets (6 tabs) + CSV · Qdrant (vectors) · Neo4j (graph, optional) · Prometheus/Grafana",
                 fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="end"))
    return d


def table(rows: list[list[str]], widths: list[float]) -> Table:
    t = Table([[Paragraph(c, CELL) for c in r] for r in rows], colWidths=widths, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build() -> Path:
    bench = json.loads((ROOT / "docs/benchmarks.json").read_text()) if (ROOT / "docs/benchmarks.json").exists() else {}
    st, cr, llm = bench.get("storage_bulk_upsert", {}), bench.get("crawl_pipeline_local_http", {}), bench.get("llm_orchestrator_fault_injection", {})
    out = ROOT / "architecture.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=12 * mm,
                            title="AI Intelligence Ingestion Platform — Architecture", author="Karri Pavan Prabhas")
    s: list = []
    s += [Paragraph("AI Intelligence Ingestion Platform — Architecture", H1),
          Paragraph("GraphOne / FrontierAtlas assessment · Python 3.12, asyncio/aiohttp, FastAPI, Celery + Redis, PostgreSQL, Qdrant/Neo4j (optional)", SUB),
          diagram(), Spacer(1, 2 * mm)]
    s += [P("Each source is an independent async crawler that yields <b>schema-validated</b> records. A pipeline resolves entities, optionally "
            "enriches with LLMs (only evidence-backed values survive), and performs idempotent bulk upserts. Every record carries "
            "<font face='Helvetica-Bold'>source.url</font>, extraction method, evidence quotes, confidence and validation status; "
            "<i>collectedAt</i> is never used as a publication date.")]

    s += [Paragraph("1 · Scale strategy: 500,000 startups, products and papers without manual intervention", H2)]
    s += B([
        "<b>Work units, not scripts.</b> Celery tasks per source and per page range (arXiv offset, HF Papers date, YC tag/batch facet). "
        "Scaling means adding worker replicas; code does not change. <i>acks_late</i> + <i>reject_on_worker_lost</i> redeliver tasks from dead "
        "workers; <i>prefetch=1</i> and bounded semaphores give backpressure; failures go to retry, then a dead-letter table and queue.",
        "<b>APIs before HTML.</b> YC directory index (yc-oss mirror, Algolia fallback), Hugging Face Hub/Papers, arXiv, GitHub REST, "
        "Product Hunt GraphQL, Greenhouse/Ashby/Lever/RemoteOK/Himalayas job APIs, publisher RSS. Structured sources need no LLM and cannot hallucinate.",
        "<b>Politeness = throughput.</b> One shared connection pool, per-domain token buckets (arXiv 1 req/3 s; GitHub tied to "
        "X-RateLimit headers with ETag revalidation), per-domain circuit breakers and robots.txt checks. Distributed limits use a Redis sliding-window Lua script. "
        "Live run: all 2,290 stored papers re-verified against the authenticated GitHub API in two 3-minute batches.",
        "<b>Incremental and resumable.</b> Per-source checkpoints (cursors, last success, listing snapshot) let a crashed one-time bulk job resume; "
        "re-runs only rewrite rows whose content hash changed (for example new GitHub star counts).",
        f"<b>Measured (synthetic fixtures, 2 vCPU, PostgreSQL 16):</b> {st.get('records', 0):,} records bulk-upserted at "
        f"{st.get('insert_rows_per_s', 0):,} rows/s with a 0-insert idempotent re-run; {cr.get('jobs_fetched', 0):,} job postings through the full "
        f"pipeline at {cr.get('jobs_per_s', 0):,}/s over local HTTP with 50 ms latency. At that rate 500k records are ~2.5 min of DB time per worker. "
        "The real limit is source rate limits, which is why work is sharded per source.",
    ])

    s += [Paragraph("2 · Handling 413s and 429s across thousands of concurrent extractions", H2)]
    s += [table([
        ["Failure", "Detection", "Response"],
        ["413 / context overflow", "HTTP 413; Groq 'request too large' (TPM); 400 'maximum context length'; Gemini token-count error", "Never resend the same payload: bisect the chunk on a paragraph or sentence boundary and retry both halves (depth ≤ 3), then fall back"],
        ["Truncated output", "finish_reason=length / MAX_TOKENS", "Treated like 413: split input so each answer fits"],
        ["429 / RESOURCE_EXHAUSTED", "status, Retry-After header, Gemini retryDelay in body", "Wait Retry-After, otherwise exponential back-off with full jitter (cap 30 s, max 3). Drain the provider's token bucket so concurrent calls back off too. If the wait is over 45 s, fall back now"],
        ["5xx / timeout", "status, client timeout", "Back-off retry, then fall back; circuit breaker skips the provider for 120 s after 5 failures"],
        ["Malformed / invalid JSON", "loose JSON parse, Pydantic types", "One repair retry with the error message appended, then fall back"],
    ], [31 * mm, 55 * mm, 94 * mm])]
    s += [Spacer(1, 2 * mm), P(
        "<b>Prevention:</b> documents are cleaned (boilerplate, repeated nav) and chunked against the <i>target provider's</i> budget using a "
        "conservative token estimate (ASCII ÷ 3.2, 1 token per non-ASCII character, +5%), minus prompt overhead. Every chunk carries the document lead. "
        "If a document needs more than 8 chunks, the lowest information-density paragraphs are dropped (prices, dates, numbers and named entities score "
        "high), and the drop is logged. Falling back to a smaller-window provider (Groq) re-splits first. Global concurrency is capped by a semaphore; "
        "per-provider RPM by token buckets (Redis-shared in a cluster)."),
        P("<b>Anti-hallucination:</b> the model must return <i>value + verbatim evidence</i> for each field. A value is kept only if the quote is in the "
          "chunk and supports the value (enum cue words, digits, name match). Chunk results merge deterministically (majority vote, then earliest chunk; "
          "lists are unioned) and conflicts are recorded. Confidence = verified share × chunk coverage × conflict penalty. Dates are never computed by the "
          "LLM: it returns the raw phrase, and the deterministic parser normalises it.")]
    if llm:
        s += [P(f"<b>Fault-injection run:</b> {llm['extractions']:,} extractions with 25% 429, 5% 413 and 5% 5xx on the primary: "
                f"{llm['succeeded']:,} succeeded ({llm['success_rate']:.0%}), 0 invented values, p95 {llm['p95_s']} s (back-off time compressed).")]

    s += [Paragraph("3 · Freshness tracking: never processing the same article or job twice", H2)]
    s += B([
        "<b>Identity:</b> canonical URL (lower-cased host, no <i>www</i>, tracking parameters, fragments or trailing slash; sorted query) → SHA-256. "
        "A text fingerprint catches syndicated copies.",
        "<b>Claim before work:</b> <font face='Courier'>SET aip:claim:&lt;hash&gt; &lt;worker&gt; NX PX lease</font>. Only the winner fetches full text or calls an LLM. "
        "After the DB commit the key becomes <font face='Courier'>done:&lt;hash&gt;</font> (30-day TTL). If the write fails, the claim is released (owner-checked Lua); "
        "if the worker dies, the lease expires. Stale items are also marked done, so no node re-evaluates them.",
        "<b>Final guarantee:</b> <font face='Courier'>UNIQUE(record_type, dedup_hash)</font> + <font face='Courier'>INSERT … ON CONFLICT</font> in one transaction, "
        "so duplicates are impossible even if Redis is flushed. Tested: 8 simulated nodes × 100 URLs → exactly 100 claims; concurrent pipelines store each article once.",
        "<b>Publication time, not crawl time:</b> source-specific fields (Greenhouse <i>first_published</i>, never <i>updated_at</i>; Ashby <i>publishedAt</i>; "
        "Lever <i>createdAt</i>; RSS <i>pubDate</i>, never <i>updated</i>), then JSON-LD <i>datePublished</i> → meta tags → &lt;time&gt; → URL date. "
        "Relative phrases ('2 hours ago', '30+ days ago') carry their precision. The 24 h test is precision-aware: an hour-level date gets ±1 h slack, and a "
        "date-only value is fresh only if it fits for every timezone. Unprovable dates are <i>UNVERIFIED</i> and excluded. The window is re-checked at export time.",
        "<b>Undated sources:</b> items listed above the first previously-seen item, after a successful run less than 24 h ago, must have appeared since that run. "
        "They are exported with <i>freshness_basis=new_since_last_run</i>. On a first run nothing is vouched for.",
    ])

    s += [Paragraph("4 · Storage strategy", H2)]
    s += B([
        "<b>PostgreSQL (system of record).</b> ACID upserts and unique constraints are the dedup guarantee. JSONB keeps the versioned canonical payload "
        "(<i>schemaVersion</i>), so new fields need no migration, while hot fields (type, entity, published_at, date_verified) are typed and indexed. "
        "It scales to 500k+ rows easily on one node; past ~50M, partition <i>records</i> by type and month and add read replicas.",
        "<b>Redis</b>: broker, claims and leases, shared rate limits. All of it is reconstructible; nothing authoritative lives there.",
        "<b>Qdrant (vectors)</b>: embeddings of descriptions, abstracts and articles for semantic search and for the ambiguous band of entity resolution "
        "(pluggable <i>embedder</i>; the default is a deterministic character n-gram cosine). Filtered HNSW search keeps payload filters such as type and date fast.",
        "<b>Neo4j (relationships)</b>: organisation → product → paper → repository → job → news-mention graph for multi-hop questions ('labs whose papers' repos "
        "gained stars and who are hiring'). It is fed from PostgreSQL by idempotent MERGE, so the graph is a projection and can be rebuilt any time.",
        "<b>Entity resolution</b> canonicalises names before keys are computed: Unicode/case/punctuation/legal-suffix normalisation → exact → alias (60-org seed, "
        "learned aliases) → domain → fuzzy with guards (distinct tokens, numbers, domain conflicts) → semantic → human review. Every decision goes to the "
        "Entity Mapping Log. A single-word-brand guard sends near-identical one-word names ('Conntour'/'Contour') to review instead of merging. "
        "This came from auditing the live run, where 2 such merges (of 1,916 automatic decisions) were found and reversed by <i>rebuild-entities</i>. "
        "Labelled set: 68/68 positives, 0 false merges.",
    ])
    s += [Paragraph("Anti-bot policy", H2), P(
        "Cloudflare and DataDome challenges are <b>detected and reported, never evaded</b>: cf-mitigated header, challenge markup, captcha-delivery and "
        "x-datadome mark the source INACCESSIBLE, open its circuit and leave a visible run-log entry. High-value protected sources (Product Hunt, Wellfound, "
        "Crunchbase) are reached through official APIs, feeds or licensed data providers. Playwright (resource-blocking, bounded context pool) is used only "
        "for JavaScript-rendered pages that permit automation.")]
    doc.build(s)
    return out


if __name__ == "__main__":
    print(build())
