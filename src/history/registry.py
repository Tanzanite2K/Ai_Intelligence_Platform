"""Phase 7D persistence: entity registry, resolution decisions, claims and evidence links.

Idempotent, append-only sync from existing data (run with HISTORY_ENABLED=true, after a collection batch):
* ``entities``: one row per canonical organisation with a stable id = UUID5(normalised key). Seed entities keep
  origin 'seed'; organisations already present at the baseline get 'baseline_backfill' and are never "discovered";
  later ones get 'discovered' with the batch that first showed them.
* ``entity_resolution_events``: one open decision per (raw name, source url); a changed decision supersedes the old one.
* ``claims`` / ``evidence_links``: the claim engine of src/history/evidence.py over VERIFIED mappings, linked to the
  exact record version that states each claim. Co-mentions are never stored as claims.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.database.session import Database
from src.entity_resolution.normalize import compact_key
from src.history.capture import BASELINE_ORIGIN, HistoryRecorder, ts
from src.history.evidence import build_claims, source_group

RESOLVER_VERSION = "phase4-rules"


def entity_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"frontieratlas/entity/{compact_key(name) or name.casefold()}"))


def claim_id(subject: str, kind: str, obj: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"frontieratlas/claim/{subject}/{kind}/{obj}/{value}"))


def _j(v: Any) -> Any:
    return json.loads(v) if isinstance(v, (str, bytes)) else (v or {})


async def sync_registry(db: Database, *, now: datetime | None = None) -> dict[str, Any]:
    await HistoryRecorder(db).check_ready()
    at = ts(now or datetime.now(UTC))
    out: dict[str, int] = defaultdict(int)
    async with db.session() as s:
        seeds = {r[0]: r[1] for r in (await s.execute(text("select canonical_name, domain from canonical_entities"))).all()}
        maps = (await s.execute(text(
            "select original_name, canonical_name, match_method, confidence, needs_review, source_url from entity_mappings order by id"))).all()
        batches = (await s.execute(text("select batch_id, trigger from ingestion_batches order by started_at"))).all()
        baseline = next((b for b, t in batches if t == BASELINE_ORIGIN), None)
        latest_live = next((b for b, t in reversed(batches) if t != BASELINE_ORIGIN), None)
        has_live = latest_live is not None
        existing = {r[0] for r in (await s.execute(text("select entity_id from entities"))).all()}
        names = sorted({m[1] for m in maps if not m[4]} | set(seeds))
        seen_keys: dict[str, str] = {}
        for n in names:
            eid = entity_id(n)
            key = compact_key(n) or n.casefold()
            if key in seen_keys and seen_keys[key] != n:
                out["key_collisions_skipped"] += 1
                continue
            seen_keys[key] = n
            if eid in existing:
                continue
            origin = "seed" if n in seeds else ("discovered" if has_live else BASELINE_ORIGIN)
            res = await s.execute(text(
                "insert into entities(entity_id,canonical_name,normalized_key,domain,origin,first_observed_batch,status,created_at) "
                "values (:id,:n,:k,:d,:o,:b,'active',:at) on conflict do nothing"),
                {"id": eid, "n": n, "k": key, "d": seeds.get(n), "o": origin, "b": latest_live if origin == "discovered" else baseline, "at": at})
            out["entities_created"] += res.rowcount
        valid = {r[0]: r[1] for r in (await s.execute(text("select entity_id, canonical_name from entities where status='active'"))).all()}
        open_dec = {(r[0], r[1]): (r[2], r[3]) for r in (await s.execute(text(
            "select e.raw_name, e.source_url, n.canonical_name, e.id from entity_resolution_events e join entities n on n.entity_id=e.entity_id "
            "where e.superseded_at is null"))).all()}
        for raw, canon, method, conf, _review, url in maps:
            eid = entity_id(canon)
            if eid not in valid:
                continue  # names awaiting review are not registered as entities
            key = (raw[:300], url or "")
            cur = open_dec.get(key)
            if cur and cur[0] == canon:
                continue
            if cur:
                await s.execute(text("update entity_resolution_events set superseded_at=:at where id=:id"), {"at": at, "id": cur[1]})
                out["decisions_superseded"] += 1
            res = await s.execute(text(
                "insert into entity_resolution_events(entity_id,raw_name,source_url,method,confidence,decided_by,resolver_version,decided_at) "
                "values (:e,:r,:u,:m,:c,'rule',:v,:at) on conflict do nothing"),
                {"e": eid, "r": key[0], "u": key[1], "m": method, "c": max(0.0, min(1.0, float(conf))), "v": RESOLVER_VERSION, "at": at})
            open_dec[key] = (canon, None)
            out["decisions_recorded"] += res.rowcount
    out.update(await sync_claims(db, now=now))
    return dict(out)


async def sync_claims(db: Database, *, now: datetime | None = None) -> dict[str, int]:
    at = ts(now or datetime.now(UTC))
    out: dict[str, int] = defaultdict(int)
    async with db.session() as s:
        ents = {r[1]: r[0] for r in (await s.execute(text("select entity_id, canonical_name from entities where status='active'"))).all()}
        review = {r[0] for r in (await s.execute(text("select distinct canonical_name from entity_mappings where needs_review"))).all()}
        rows = (await s.execute(text(
            "select m.canonical_name, r.id, r.record_type, r.dedup_hash, r.source_name, r.source_url, r.published_at, r.payload, v.version_no "
            "from entity_mappings m join records r on r.source_url = m.source_url "
            "join record_versions v on v.record_type=r.record_type and v.dedup_hash=r.dedup_hash and v.valid_to is null "
            "where not m.needs_review"))).all()
        by_org: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
        for canon, rid, rt, dh, src, url, pub, payload, ver in rows:
            p = _j(payload)
            by_org[canon][rid] = {"id": rid, "type": rt, "dedup": dh, "version": ver, "source": src, "url": url, "published": str(pub) if pub else None,
                                  "content": p.get("content") or {}, "evidence": (p.get("provenance") or {}).get("evidence") or {}}
        for org, recs in by_org.items():
            eid = ents.get(org)
            if not eid:
                continue
            idx = {r["id"]: r for r in recs.values()}
            assessed = build_claims(org, list(recs.values()), needs_review=org in review)["claims"]
            for c in assessed:
                cid = claim_id(eid, c["kind"], c["object"], c["value"])
                dates = sorted(e["date"] for e in c["evidence"] if e.get("date")) or [at]
                await s.execute(text(
                    "insert into claims(claim_id,claim_kind,subject_entity_id,predicate,object_kind,object_key,value,first_observed_at,last_observed_at,"
                    "support_tier,independent_groups) values (:id,:k,:e,:p,:ok,:o,:v,:f,:l,:t,:g) on conflict(claim_id) do update set "
                    "support_tier=excluded.support_tier, independent_groups=excluded.independent_groups, last_observed_at=excluded.last_observed_at"),
                    {"id": cid, "k": c["kind"], "e": eid, "p": c["predicate"], "ok": c["kind"], "o": c["object"][:300], "v": c["value"], "f": dates[0], "l": max(dates[-1], dates[0]),
                     "t": c["support_tier"], "g": len(c["independent_source_groups"])})
                out["claims_upserted"] += 1
                for ev in c["evidence"]:
                    r = idx.get(ev["record_id"])
                    if not r:
                        continue
                    res = await s.execute(text(
                        "insert into evidence_links(claim_id,record_type,dedup_hash,version_no,source_name,source_group,primary_url,content_fingerprint,snippet,evidence_kind) "
                        "values (:c,:t,:h,:v,:s,:g,:u,NULL,:sn,:k) on conflict do nothing"),
                        {"c": cid, "t": r["type"], "h": r["dedup"], "v": r["version"], "s": ev["source"], "g": source_group(ev["source"]), "u": ev.get("url"),
                         "sn": (ev["snippet"] or ev["source"])[:2000], "k": ev["kind"]})
                    out["evidence_links_created"] += res.rowcount
    return dict(out)
