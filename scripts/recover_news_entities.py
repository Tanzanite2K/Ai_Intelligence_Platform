"""Targeted recovery of news articles whose organisation extraction was dead-lettered.

Scope (enforced, not configurable):
  NEWS records whose source URL is the key of a dead letter with stage ``llm.news_entities`` AND that have no
  organisations stored yet. Any other record is never written.

What --apply writes (and nothing else):
  * those news records: ``content.organizations`` + ``provenance.evidence.organizations`` + ``provenance.llmProvider``
    (same source priority, so the existing upsert rule applies unchanged);
  * entity mappings for those article URLs only (INSERT ... ON CONFLICT DO NOTHING - existing mappings are untouched).

It never crawls, never deletes, never touches the dead_letters table (failures stay as history), never rebuilds
mappings and never changes startups, products, papers or jobs.

Organisation names go through the existing pipeline: LLMOrchestrator (bounded retries for 429/503/timeouts, repair
retry for malformed output) -> evidence validator (every name needs its own verbatim quote) -> EntityResolver.
The resolver is primed in memory by replaying stored startups/products/jobs exactly like ``rebuild-entities``;
the run aborts if that replay would rename any stored record (i.e. the resolver would disagree with the database).

Usage (from the project root):
  python scripts/recover_news_entities.py --check                 # scope + config only: no API calls, no writes
  python scripts/recover_news_entities.py --snapshot FILE         # read-only fingerprint of the database
  python scripts/recover_news_entities.py --apply                 # extract + persist (take a backup first)
  python scripts/recover_news_entities.py --verify BEFORE AFTER   # prove only in-scope rows changed
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.settings import Settings, get_settings
from src.core.schemas import NewsRecord, RecordType
from src.database.models import RecordRow
from src.database.repository import Repository, record_to_row
from src.database.session import Database
from src.entity_resolution.resolver import EntityResolver
from src.extraction.orchestrator import ExtractionFailed, LLMOrchestrator, NoProviderAvailable
from src.extraction.providers.vendors import build_providers
from src.extraction.tasks import NEWS_ENTITIES
from src.monitoring.logging import redact
from src.pipelines.maintenance import _re_resolve

STAGE = "llm.news_entities"
REPLAY_TYPES = (RecordType.STARTUP, RecordType.PRODUCT, RecordType.JOB)


class ScopeError(RuntimeError):
    """The database does not look the way the recovery expects; nothing is written."""


def sqlite_path(url: str) -> Path:
    if not url.startswith("sqlite"):
        raise ScopeError("only the SQLite database is supported by this recovery script")
    return Path(url.split(":///", 1)[1])


# ------------------------------------------------------------------ scope
async def load_scope(repo: Repository, db_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        keys = [r[0] for r in con.execute("select key from dead_letters where stage = ? order by id", (STAGE,))]
    finally:
        con.close()
    keyset = set(keys)
    rows = await repo.fetch_payloads_with_priority(RecordType.NEWS.value)
    by_url = {p["source"]["url"]: (p, prio) for p, prio in rows}
    targets, already, missing, no_text = [], [], [], []
    for k in dict.fromkeys(keys):
        if k not in by_url:
            missing.append(k)
            continue
        p, prio = by_url[k]
        c = p["content"]
        if c.get("organizations"):
            already.append(k)
        elif not (c.get("full_text") or c.get("summary")):
            no_text.append(k)
        else:
            targets.append((p, prio))
    untagged_news = [u for u, (p, _) in by_url.items() if not p["content"].get("organizations")]
    return {
        "dead_letters": len(keys), "distinct_keys": len(keyset), "targets": targets, "already_tagged": already,
        "missing_records": missing, "no_text": no_text, "untagged_news_total": len(untagged_news),
        "untagged_news_not_dead_lettered": sorted(set(untagged_news) - keyset), "news_total": len(rows),
    }


async def prime_resolver(repo: Repository, s: Settings) -> tuple[EntityResolver, dict[str, Any]]:
    """Rebuild the resolver's in-memory state from stored records (same order as rebuild-entities). No writes."""
    r = EntityResolver(fuzzy_accept=s.er_fuzzy_accept, fuzzy_review=s.er_fuzzy_review, semantic_accept=s.er_semantic_accept)
    stats: dict[str, Any] = {}
    for rt in REPLAY_TYPES:
        rows = await repo.fetch_payloads_with_priority(rt.value)
        rows.sort(key=lambda t: -t[1])
        changed = 0
        for payload, _ in rows:
            changed += _re_resolve(rt, json.loads(json.dumps(payload)), r)[1]
        stats[rt.value] = {"records": len(rows), "would_rename": changed}
    if any(v["would_rename"] for v in stats.values()):
        raise ScopeError(f"resolver replay disagrees with stored canonical names {stats}; refusing to write")
    return r, stats


def model_info(s: Settings) -> dict[str, Any]:
    provs = build_providers(s)
    return {"gemini_model": s.gemini_model, "providers_order": s.providers_order,
            "providers_configured": [p.name for p in provs if p.configured],
            "retries_per_provider": s.llm_max_retries_per_provider, "backoff_s": [s.llm_backoff_base_s, s.llm_backoff_max_s]}


# ------------------------------------------------------------------ recovery
async def update_news_only(db: Database, records: list[NewsRecord]) -> int:
    """Minimal write: only payload, content_hash and updated_at of the existing NEWS rows (matched by dedup hash).
    collected_at, published_at, source fields and priority stay exactly as stored. Never inserts."""
    n = 0
    async with db.session() as s:
        for rec in records:
            row = record_to_row(rec)
            res = await s.execute(
                update(RecordRow)
                .where(RecordRow.record_type == RecordType.NEWS.value, RecordRow.dedup_hash == row["dedup_hash"])
                .values(payload=row["payload"], content_hash=row["content_hash"], updated_at=func.now())
            )
            if res.rowcount != 1:
                raise ScopeError(f"expected exactly one stored row for {rec.source.url}, matched {res.rowcount}")
            n += 1
    return n


async def recover(s: Settings, *, apply: bool, providers: list[Any] | None = None, expected: int | None = None) -> dict[str, Any]:
    db_path = sqlite_path(s.database_url)
    if not db_path.exists():
        raise ScopeError(f"database not found: {db_path}")
    db = Database(s.database_url)
    repo = Repository(db, batch_size=s.bulk_insert_batch_size)
    out: dict[str, Any] = {"run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "apply" if apply else "check",
                           "database": str(db_path), "config": model_info(s)}
    try:
        scope = await load_scope(repo, db_path)
        targets = scope.pop("targets")
        out["scope"] = scope | {"targets": len(targets)}
        if expected is not None and len(targets) != expected:
            raise ScopeError(f"expected {expected} target articles, found {len(targets)}; nothing written")
        if scope["missing_records"]:
            raise ScopeError(f"{len(scope['missing_records'])} dead-letter keys have no stored news record; nothing written")
        out["targets"] = [{"url": p["source"]["url"], "title": p["content"].get("title")} for p, _ in targets]
        if not apply:
            return out
        if not targets:
            out["results"] = []
            return out
        resolver, replay = await prime_resolver(repo, s)
        out["resolver_replay"] = replay
        start = len(resolver.log)
        chain = providers if providers is not None else build_providers(s)
        if not any(p.configured for p in chain):
            raise ScopeError("no LLM provider configured")
        orch = LLMOrchestrator(chain, s)
        results: list[dict[str, Any]] = []
        updated: list[tuple[NewsRecord, int]] = []  # (record, source priority) - priority is left unchanged
        try:
            for p, prio in targets:
                c, url = p["content"], p["source"]["url"]
                item: dict[str, Any] = {"url": url, "title": c.get("title")}
                try:
                    res = await orch.extract(NEWS_ENTITIES, f"{c['title']}\n\n{c.get('full_text') or c.get('summary')}")
                except (ExtractionFailed, NoProviderAvailable) as e:
                    results.append(item | {"outcome": "failed", "reason": redact(str(e))[:600]})
                    continue
                item["attempts"] = [f"{a.provider}:{a.outcome}" for a in res.attempts]
                raw = res.data.get("organizations") or []
                if not raw:
                    why = res.rejected_fields.get("organizations") or "model returned no organisation (field absent or null)"
                    results.append(item | {"outcome": "no_validated_organisations", "reason": redact(str(why))[:600]})
                    continue
                orgs = list(dict.fromkeys(resolver.resolve(o, source_url=url).canonical for o in raw))
                ev = dict(p["provenance"].get("evidence") or {}, organizations=res.evidence.get("organizations", "")[:600])
                prov = dict(p["provenance"], evidence=ev, llmProvider=",".join(res.providers_used))
                updated.append((NewsRecord.model_validate({**p, "content": {**c, "organizations": orgs}, "provenance": prov}), prio))
                results.append(item | {"outcome": "tagged", "organizations": orgs, "extracted": raw,
                                       "evidence": res.evidence.get("organizations", "")[:600], "providers": res.providers_used})
        finally:
            await orch.close()
        new_maps = list(resolver.log[start:])
        in_scope = {r["url"] for r in results if r["outcome"] == "tagged"}
        stray = [m for m in new_maps if (m.source_url or "") not in in_scope]
        if stray:
            raise ScopeError(f"{len(stray)} mapping(s) outside the recovered articles; nothing written")
        written = {"news_records": await update_news_only(db, [rec for rec, _ in updated]), "mappings_attempted": len(new_maps)}
        await repo.save_mappings(new_maps)
        out["written"] = written
        out["results"] = results
        out["summary"] = {k: sum(1 for r in results if r["outcome"] == k) for k in ("tagged", "no_validated_organisations", "failed")}
        out["mappings"] = [{"original_name": m.original_name, "canonical_name": m.canonical_name, "method": m.match_method,
                            "confidence": m.confidence, "needs_review": m.needs_review, "source_url": m.source_url} for m in new_maps]
        return out
    finally:
        await db.dispose()


# ------------------------------------------------------------------ read-only fingerprint + verification
def _h(rows: Any) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, default=str, sort_keys=True).encode())
    return h.hexdigest()


def snapshot(db_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = lambda sql, *a: con.execute(sql, a).fetchall()  # noqa: E731
        tables = [r[0] for r in q("select name from sqlite_master where type='table' order by 1")]
        keys = {r[0] for r in q("select key from dead_letters where stage=?", STAGE)}
        news = q("select source_url, payload from records where record_type='NEWS' order by id")
        return {
            "taken_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "integrity": q("pragma integrity_check")[0][0],
            "counts": {t: q(f'select count(*) from "{t}"')[0][0] for t in tables},
            "records_by_type": dict(q("select record_type, count(*) from records group by 1")),
            "news_untagged": sum(1 for _, p in news if not json.loads(p)["content"].get("organizations")),
            "news_tagged": sum(1 for _, p in news if json.loads(p)["content"].get("organizations")),
            "dead_letters_by_stage": dict(q("select stage, count(*) from dead_letters group by 1")),
            "fp_dead_letters": _h(q("select id, stage, key, error, attempts, created_at, payload from dead_letters order by id")),
            "fp_non_news_records": _h(q("select id, record_type, dedup_hash, content_hash, payload, source_priority, updated_at from records where record_type != 'NEWS' order by id")),
            "fp_news_out_of_scope": _h([(u, p) for u, p in news if u not in keys]),
            "fp_news_identity": _h(q("select id, dedup_hash, source_url, published_at, collected_at from records where record_type='NEWS' order by id")),
            "fp_other_tables": {t: _h(q(f'select * from "{t}" order by 1')) for t in ("canonical_entities", "source_runs", "crawl_checkpoints", "seen_items") if t in tables},
            "mappings_max_id": q("select coalesce(max(id),0) from entity_mappings")[0][0],
            "fp_mappings": _h(q("select id, original_name, canonical_name, match_method, confidence, needs_review, entity_kind, source_url, resolved_at from entity_mappings order by id")),
            "dead_letter_keys": sorted(keys),
        }
    finally:
        con.close()


def verify(before: dict[str, Any], after_db: Path) -> dict[str, Any]:
    after = snapshot(after_db)
    con = sqlite3.connect(f"file:{after_db}?mode=ro", uri=True)
    try:
        q = lambda sql, *a: con.execute(sql, a).fetchall()  # noqa: E731
        old_max = before["mappings_max_id"]
        old_fp = _h(q("select id, original_name, canonical_name, match_method, confidence, needs_review, entity_kind, source_url, resolved_at from entity_mappings where id <= ? order by id", old_max))
        new_maps = q("select original_name, canonical_name, match_method, needs_review, source_url from entity_mappings where id > ?", old_max)
        dupes = q("select count(*) from (select 1 from entity_mappings group by original_name, canonical_name, source_url having count(*) > 1)")[0][0]
        keys = set(before["dead_letter_keys"])
        tagged_now = {u: json.loads(p)["content"].get("organizations") for u, p in q("select source_url, payload from records where record_type='NEWS'")}
        bad_refs = [m for m in new_maps if not m[1] or m[4] not in keys]
        orgs_unmapped = [(u, o) for u, orgs in tagged_now.items() if u in keys for o in (orgs or [])
                         if not q("select 1 from entity_mappings where canonical_name=? and source_url=? limit 1", o, u)]
    finally:
        con.close()
    checks = {
        "integrity_ok": after["integrity"] == "ok",
        "record_counts_unchanged": after["records_by_type"] == before["records_by_type"],
        "non_news_records_unchanged": after["fp_non_news_records"] == before["fp_non_news_records"],
        "out_of_scope_news_unchanged": after["fp_news_out_of_scope"] == before["fp_news_out_of_scope"],
        "news_identity_unchanged": after["fp_news_identity"] == before["fp_news_identity"],
        "dead_letters_preserved": after["fp_dead_letters"] == before["fp_dead_letters"],
        "other_tables_unchanged": after["fp_other_tables"] == before["fp_other_tables"],
        "existing_mappings_unchanged": old_fp == before["fp_mappings"],
        "new_mappings_only_for_recovered_articles": not bad_refs,
        "no_duplicate_mappings": dupes == 0,
        "every_stored_organisation_has_a_mapping": not orgs_unmapped,
    }
    return {"ok": all(checks.values()), "checks": checks,
            "before": {k: before[k] for k in ("counts", "news_tagged", "news_untagged", "dead_letters_by_stage")},
            "after": {k: after[k] for k in ("counts", "news_tagged", "news_untagged", "dead_letters_by_stage")},
            "new_mappings": [dict(zip(("original_name", "canonical_name", "method", "needs_review", "source_url"), m, strict=True)) for m in new_maps],
            "problems": {"bad_mapping_refs": bad_refs[:20], "organisations_without_mapping": orgs_unmapped[:20]}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--snapshot", metavar="FILE")
    g.add_argument("--verify", nargs=2, metavar=("BEFORE", "OUT"))
    ap.add_argument("--expected", type=int, default=None, help="refuse to run unless exactly this many target articles")
    a = ap.parse_args(argv)
    # same patience as scripts/llm_live_check.py: Gemini 503 "high demand" was observed repeatedly
    s = get_settings().model_copy(update={"llm_max_retries_per_provider": 5, "llm_backoff_base_s": 2.0, "llm_backoff_max_s": 30.0})
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    Path("docs").mkdir(exist_ok=True)
    try:
        if a.snapshot:
            snap = snapshot(sqlite_path(s.database_url))
            Path(a.snapshot).write_text(json.dumps(snap, indent=2), encoding="utf-8")
            print(json.dumps({k: snap[k] for k in ("integrity", "counts", "news_tagged", "news_untagged", "dead_letters_by_stage")}, indent=2))
            return 0 if snap["integrity"] == "ok" else 1
        if a.verify:
            rep = verify(json.loads(Path(a.verify[0]).read_text(encoding="utf-8")), sqlite_path(s.database_url))
            Path(a.verify[1]).write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
            print(json.dumps({"ok": rep["ok"], "checks": rep["checks"], "before": rep["before"], "after": rep["after"]}, indent=2))
            return 0 if rep["ok"] else 1
        rep = asyncio.run(recover(s, apply=a.apply, expected=a.expected))
    except ScopeError as e:
        print(f"REFUSED: {e}")
        return 2
    path = Path(f"docs/news_recovery_{rep['mode']}_{stamp}.json")
    path.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: rep.get(k) for k in ("mode", "config", "scope", "resolver_replay", "summary", "written")}, indent=2))
    for r in rep.get("results", []):
        print(f"[{r['outcome']}] {r['title'][:70]!r} {r.get('organizations') or r.get('reason', '')}")
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
