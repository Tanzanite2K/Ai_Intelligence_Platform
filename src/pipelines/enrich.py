"""LLM enrichment over already-stored records (no re-crawl).

Products whose pricing is unknown are sent to the extraction engine **only if
their text contains a pricing cue** (free/$/plan/trial/enterprise…) — text with
no such cue cannot yield an evidenced answer, so calling an LLM would only
cost quota. Accepted values carry a verbatim evidence quote; everything else
stays ``null``. Every call is logged to ``docs/llm_enrichment.json``.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config.settings import get_settings
from src.core.schemas import PricingModel, ProductRecord
from src.database.repository import Repository
from src.database.session import Database
from src.entity_resolution.resolver import EntityResolver
from src.extraction.orchestrator import ExtractionFailed, LLMOrchestrator, NoProviderAvailable
from src.extraction.providers.vendors import build_providers
from src.extraction.tasks import PRODUCT_PRICING

PRICING_CUE = re.compile(r"(\$\s?\d|€\s?\d|£\s?\d|\b(free|freemium|pricing|per (month|user|seat)|/mo\b|subscription|trial|enterprise|paid|plan|open[- ]source)\b)", re.I)


async def run_enrich_products(limit: int | None = None, concurrency: int = 4) -> dict[str, Any]:
    s = get_settings()
    providers = build_providers(s)
    out: dict[str, Any] = {"started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "providers_configured": [p.name for p in providers if p.configured]}
    if not out["providers_configured"]:
        out["error"] = "no LLM provider configured"
        return out
    db = Database(s.database_url)
    repo = Repository(db, batch_size=s.bulk_insert_batch_size)
    orch = LLMOrchestrator(providers, s)
    resolver = EntityResolver()
    rows = await repo.fetch_records("PRODUCT")
    unknown = [r for r in rows if r["content"].get("pricingModel") is None]
    cands = [r for r in unknown if PRICING_CUE.search(f"{r['content'].get('productName', '')} {r['content'].get('description') or ''}")]
    cands = cands[:limit] if limit else cands
    out.update(products=len(rows), pricing_unknown=len(unknown), with_pricing_cue=len(cands))
    log: list[dict[str, Any]] = []
    updated: list[ProductRecord] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(r: dict[str, Any]) -> None:
        c = r["content"]
        async with sem:
            try:
                res = await orch.extract(PRODUCT_PRICING, f"{c['productName']}\n\n{c.get('description') or ''}")
            except NoProviderAvailable as e:
                log.append({"url": r["source"]["url"], "outcome": "no_provider", "detail": str(e)[:200]})
                return
            except ExtractionFailed as e:
                log.append({"url": r["source"]["url"], "outcome": "failed", "detail": str(e)[:300]})
                await repo.dead_letter("llm.product_pricing", r["source"]["url"], {"description": (c.get("description") or "")[:2000]}, str(e))
                return
        entry = {"url": r["source"]["url"], "outcome": "ok", "providers": res.providers_used, "confidence": res.confidence, "status": res.status.value,
                 "accepted": res.data, "rejected": res.rejected_fields, "attempts": [f"{a.provider}:{a.outcome}" for a in res.attempts]}
        log.append(entry)
        upd: dict[str, Any] = {}
        ev = dict(r["provenance"].get("evidence") or {})
        if res.data.get("pricing_model"):
            upd["pricingModel"] = PricingModel(res.data["pricing_model"]).value
            ev["pricingModel"] = res.evidence["pricing_model"][:300]
        if res.data.get("startup_name") and not c.get("startupName"):
            upd["startupName"] = resolver.resolve(res.data["startup_name"], source_url=r["source"]["url"]).canonical
            ev["startupName"] = res.evidence["startup_name"][:300]
        if upd:
            prov = dict(r["provenance"], evidence=ev, llmProvider=",".join(res.providers_used),
                        confidence=min(r["provenance"].get("confidence", 1.0), res.confidence))
            updated.append(ProductRecord.model_validate({**r, "content": {**c, **upd}, "provenance": prov}))

    await asyncio.gather(*(one(r) for r in cands))
    if updated:
        await repo.bulk_upsert(updated, priority=10)
    await repo.save_mappings(resolver.log)
    await orch.close()
    await db.dispose()
    out.update(records_updated=len(updated), outcomes=dict(Counter(e["outcome"] for e in log)),
               providers_used=dict(Counter(p for e in log for p in e.get("providers", []))),
               attempt_outcomes=dict(Counter(a for e in log for a in e.get("attempts", []))),
               rejected_field_reasons=dict(Counter(v for e in log for v in (e.get("rejected") or {}).values())))
    Path("docs").mkdir(exist_ok=True)
    Path("docs/llm_enrichment.json").write_text(json.dumps({"summary": out, "calls": log[:500]}, indent=2), encoding="utf-8")
    return out


async def run_enrich_news(limit: int | None = None, concurrency: int = 4) -> dict[str, Any]:
    """Extract organisation mentions for stored news without them (e.g. after an LLM outage dead-lettered them)."""
    from src.core.schemas import NewsRecord
    from src.extraction.tasks import NEWS_ENTITIES

    s = get_settings()
    providers = build_providers(s)
    out: dict[str, Any] = {"providers_configured": [p.name for p in providers if p.configured]}
    if not out["providers_configured"]:
        return out | {"error": "no LLM provider configured"}
    db = Database(s.database_url)
    repo = Repository(db, batch_size=s.bulk_insert_batch_size)
    orch = LLMOrchestrator(providers, s)
    resolver = EntityResolver()
    rows = [r for r in await repo.fetch_records("NEWS") if not r["content"].get("organizations") and (r["content"].get("full_text") or r["content"].get("summary"))]
    rows = rows[:limit] if limit else rows
    updated: list[NewsRecord] = []
    log: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(r: dict[str, Any]) -> None:
        c = r["content"]
        async with sem:
            try:
                res = await orch.extract(NEWS_ENTITIES, f"{c['title']}\n\n{c.get('full_text') or c.get('summary')}")
            except (ExtractionFailed, NoProviderAvailable) as e:
                log.append({"url": r["source"]["url"], "outcome": "failed", "detail": str(e)[:300]})
                return
        orgs = list(dict.fromkeys(resolver.resolve(o, source_url=r["source"]["url"]).canonical for o in res.data.get("organizations", [])))
        log.append({"url": r["source"]["url"], "outcome": "ok", "organizations": orgs, "providers": res.providers_used, "confidence": res.confidence,
                    "attempts": [f"{a.provider}:{a.outcome}" for a in res.attempts]})
        if orgs:
            ev = dict(r["provenance"].get("evidence") or {}, organizations=res.evidence.get("organizations", "")[:600])
            prov = dict(r["provenance"], evidence=ev, llmProvider=",".join(res.providers_used))
            updated.append(NewsRecord.model_validate({**r, "content": {**c, "organizations": orgs}, "provenance": prov}))

    await asyncio.gather(*(one(r) for r in rows))
    if updated:
        await repo.bulk_upsert(updated, priority=10)
    await repo.save_mappings(resolver.log)
    await orch.close()
    await db.dispose()
    out.update(candidates=len(rows), records_updated=len(updated), outcomes=dict(Counter(e["outcome"] for e in log)))
    Path("docs").mkdir(exist_ok=True)
    Path("docs/llm_enrichment_news.json").write_text(json.dumps({"summary": out, "calls": log}, indent=2), encoding="utf-8")
    return out
