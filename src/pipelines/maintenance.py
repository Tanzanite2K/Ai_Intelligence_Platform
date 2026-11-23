"""Re-run entity resolution over stored records and regenerate the Entity Mapping Log.

Needed when resolution rules or natural keys change: canonical names are
recomputed from the *raw* names kept in every record (``rawName``,
``rawStartupName``, ``raw_company``), keys are recomputed, and each record type
is replaced in a single transaction. Richer sources are processed first so
their names become the canonical ones (same order as ingestion priority).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.config.settings import get_settings
from src.core.schemas import RECORD_CLASSES, RecordBase, RecordType
from src.database.repository import Repository
from src.database.session import Database
from src.entity_resolution.resolver import EntityResolver


def _re_resolve(rtype: RecordType, p: dict[str, Any], r: EntityResolver) -> tuple[dict[str, Any], bool]:
    c = dict(p["content"])
    url = p["source"]["url"]
    before = None
    if rtype is RecordType.STARTUP:
        raw = c.get("rawName") or c["entityName"]
        before = c["entityName"]
        handle = (p.get("provenance") or {}).get("evidence", {}).get("hubHandle")
        res = r.resolve_with_handle(raw, handle, source_url=url) if handle else r.resolve(raw, website=c.get("website"), source_url=url)
        c["entityName"] = res.canonical
        c["rawName"] = raw
        return {**p, "content": c}, before != c["entityName"]
    if rtype is RecordType.PRODUCT:
        # resolve the organisation's display name; the platform handle (rawStartupName, e.g. 'meta-llama')
        # may only confirm an already-known entity, never create one
        name = c.get("startupName") or c.get("rawStartupName")
        before = c.get("startupName")
        if name:
            c["startupName"] = r.resolve_with_handle(name, c.get("rawStartupName"), source_url=url).canonical
        return {**p, "content": c}, before != c.get("startupName")
    if rtype is RecordType.JOB:
        raw = c.get("raw_company") or c["company"]
        before = c["company"]
        c["company"] = r.resolve(raw, source_url=url).canonical
        c["raw_company"] = raw
        return {**p, "content": c}, before != c["company"]
    if rtype is RecordType.NEWS and c.get("organizations"):
        before = list(c["organizations"])
        c["organizations"] = list(dict.fromkeys(r.resolve(o, source_url=url).canonical for o in c["organizations"]))
        return {**p, "content": c}, before != c["organizations"]
    return p, False


async def rebuild_entities(database_url: str | None = None) -> dict[str, Any]:
    s = get_settings()
    db = Database(database_url or s.database_url)
    repo = Repository(db, batch_size=s.bulk_insert_batch_size)
    resolver = EntityResolver(fuzzy_accept=s.er_fuzzy_accept, fuzzy_review=s.er_fuzzy_review, semantic_accept=s.er_semantic_accept)
    await repo.upsert_canonical_entities(resolver.canonical_rows())
    out: dict[str, Any] = {}
    for rtype in (RecordType.STARTUP, RecordType.PRODUCT, RecordType.JOB, RecordType.NEWS):
        rows = await repo.fetch_payloads_with_priority(rtype.value)
        rows.sort(key=lambda t: -t[1])  # stable: higher-priority sources first, then insertion order
        recs: list[tuple[RecordBase, int]] = []
        renamed = 0
        for payload, prio in rows:
            new, changed = _re_resolve(rtype, payload, resolver)
            renamed += changed
            recs.append((RECORD_CLASSES[rtype].model_validate(new), prio))
        res = await repo.replace_records(rtype.value, recs)
        out[rtype.value] = res | {"canonical_name_changed": renamed}
    # Re-evaluate every historical raw name under the current rules so the log keeps original spellings
    # (e.g. 'OpenAI, Inc.') and no superseded decision (e.g. an old wrong FUZZY merge) survives.
    seen = {(m.original_name, m.source_url or "") for m in resolver.log}
    old = await repo.fetch_mappings()
    reevaluated = changed_decisions = 0
    for m in old:
        if (m["originalName"], m["sourceUrl"] or "") in seen:
            continue
        res = resolver.resolve(m["originalName"], source_url=m["sourceUrl"] or None, kind=m["entityKind"])
        seen.add((m["originalName"], m["sourceUrl"] or ""))
        reevaluated += 1
        changed_decisions += res.canonical != m["canonicalName"]
    out["historical_names_reevaluated"] = reevaluated
    out["historical_decisions_changed"] = changed_decisions
    n = await repo.replace_mappings(resolver.log)
    methods = Counter(m.match_method for m in resolver.log)
    out["entity_mapping_log"] = {"rows": n, "by_method": dict(methods), "needs_review": sum(1 for m in resolver.log if m.needs_review)}
    await db.dispose()
    return out
