"""Rehearse the activation commands against a DISPOSABLE COPY of production. Never writes to data/platform.db.

1. Online-backs-up production (read-only) to C:\\FrontierAtlas-test\\activation-dryrun-<stamp>\\platform.db.
2. Points DATABASE_URL at the copy and sets HISTORY_ENABLED=true for THIS PROCESS ONLY (.env is not touched); aborts
   unless the resolved settings point at the copy. Redis dedup is disabled for the process (REDIS_URL="").
3. Records every outbound aiohttp request host and counts LLM construction/extraction calls.
4. Runs exactly the documented CLI: `collect --trigger manual --no-llm --concurrency 1 --source ...` then `sync-evidence`.
5. Reports row-count deltas, integrity/FK on the copy, LLM calls (must be 0), hosts contacted, and production SHA-256
   before/after (must be equal). Report: docs/activation_dryrun_<stamp>.json
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = (ROOT / "data" / "platform.db").resolve()
WORK_ROOT = Path(r"C:\FrontierAtlas-test") if os.name == "nt" else Path.home() / "FrontierAtlas-test"
SOURCES = ["techcrunch_ai", "theverge_ai", "mit_tr_ai", "arstechnica_ai", "openai_news",
           "greenhouse", "ashby", "lever", "remoteok", "himalayas"]
# Tables each command is allowed to change (anything else fails the rehearsal).
COLLECT_TABLES = {"records", "record_observations", "record_versions", "presence_events", "ingestion_batches", "source_runs",
                  "crawl_checkpoints", "entity_mappings", "dead_letters", "metric_snapshots", "catalogue_scans"}
SYNC_TABLES = {"entities", "entity_resolution_events", "claims", "evidence_links"}
LLM_HOSTS = ("generativelanguage.googleapis.com", "api.groq.com", "api.deepseek.com", "api.openai.com", "api.anthropic.com")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ro(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)


def counts(p: Path) -> dict[str, int]:
    with closing(ro(p)) as c:
        ts = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in ts}


def digests(p: Path) -> dict[str, str]:
    """SHA-256 over every row of every table (rowid order) plus the schema, so UPDATEs show up, not only INSERTs."""
    out: dict[str, str] = {}
    with closing(ro(p)) as c:
        out["__schema__"] = hashlib.sha256("\n".join(r[0] or "" for r in c.execute("select sql from sqlite_master order by name")).encode()).hexdigest()
        for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name").fetchall():
            h = hashlib.sha256()
            for row in c.execute(f'select * from "{t}" order by rowid'):
                h.update(repr(row).encode())
            out[t] = h.hexdigest()
    return out


def main() -> int:
    os.chdir(ROOT)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    W = WORK_ROOT / f"activation-dryrun-{stamp}"
    W.mkdir(parents=True, exist_ok=False)
    copy = W / "platform.db"
    live_before = sha(LIVE)
    s, d = ro(LIVE), sqlite3.connect(copy)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{copy.as_posix()}"
    os.environ["HISTORY_ENABLED"] = "true"
    os.environ["REDIS_URL"] = ""
    sys.path.insert(0, str(ROOT))
    from src.config.settings import get_settings

    st = get_settings()
    if copy.as_posix() not in st.database_url or LIVE.as_posix() in st.database_url or not st.history_enabled:
        print("ABORT: settings do not point at the disposable copy", st.database_url)
        return 2

    import aiohttp
    from yarl import URL

    hosts: Counter[str] = Counter()
    orig = aiohttp.ClientSession._request

    async def spy(self, method, url, *a, **k):  # type: ignore[no-untyped-def]
        hosts[str(URL(str(url)).host)] += 1
        return await orig(self, method, url, *a, **k)

    aiohttp.ClientSession._request = spy  # type: ignore[method-assign]
    import src.extraction.orchestrator as orch_mod
    import src.pipelines.runtime as rt

    llm = Counter()
    real_build, real_extract = rt.build_providers, orch_mod.LLMOrchestrator.extract

    def build_spy(*a, **k):  # type: ignore[no-untyped-def]
        llm["build_providers"] += 1
        return real_build(*a, **k)

    async def extract_spy(self, *a, **k):  # type: ignore[no-untyped-def]
        llm["extract"] += 1
        return await real_extract(self, *a, **k)

    rt.build_providers = build_spy
    orch_mod.LLMOrchestrator.extract = extract_spy  # type: ignore[method-assign]

    import src.main as cli

    c0, d0 = counts(copy), digests(copy)
    argv_collect = ["collect", "--trigger", "manual", "--no-llm", "--concurrency", "1", *[a for x in SOURCES for a in ("--source", x)]]
    print("$ python -m src.main " + " ".join(argv_collect), flush=True)
    rc_collect = cli.main(argv_collect)
    c1, d1 = counts(copy), digests(copy)
    print("$ python -m src.main sync-evidence", flush=True)
    rc_sync = cli.main(["sync-evidence"])
    c2, d2 = counts(copy), digests(copy)
    with closing(ro(copy)) as c:
        integ = c.execute("pragma integrity_check").fetchone()[0]
        fk = len(c.execute("pragma foreign_key_check").fetchall())
        batches = c.execute("select trigger, status from ingestion_batches order by started_at").fetchall()
        runs = c.execute(f"select source, status, fetched, stored from source_runs where id > (select max(id) - {len(SOURCES)} from source_runs) order by id").fetchall()
    live_after = sha(LIVE)
    rep = {
        "stamp": stamp, "copy": str(copy), "sources": SOURCES,
        "exit_codes": {"collect": rc_collect, "sync_evidence": rc_sync},
        "delta_collect": {t: c1[t] - c0[t] for t in c0 if c1[t] != c0[t]},
        "delta_sync_evidence": {t: c2[t] - c1[t] for t in c1 if c2[t] != c1[t]},
        "tables_modified_by_collect": sorted(t for t in d1 if d1[t] != d0.get(t)),
        "tables_modified_by_sync_evidence": sorted(t for t in d2 if d2[t] != d1.get(t)),
        "source_availability": [{"source": r[0], "status": r[1], "fetched": r[2], "stored": r[3]} for r in runs],
        "batches": batches, "copy_integrity": integ, "copy_fk_violations": fk,
        "llm_calls": dict(llm), "hosts_contacted": dict(hosts),
        "llm_hosts_contacted": [h for h in hosts if h in LLM_HOSTS],
        "production_sha256_before": live_before, "production_sha256_after": live_after,
        "production_unchanged": live_before == live_after,
    }
    ok = (rc_collect == 0 and rc_sync == 0 and integ == "ok" and fk == 0 and not llm and not rep["llm_hosts_contacted"]
          and rep["production_unchanged"] and bool(rep["delta_collect"].get("ingestion_batches"))
          and bool(batches) and batches[-1][1] == "completed"
          and "__schema__" not in rep["tables_modified_by_collect"] and "__schema__" not in rep["tables_modified_by_sync_evidence"]
          and set(rep["tables_modified_by_collect"]) <= COLLECT_TABLES and set(rep["tables_modified_by_sync_evidence"]) <= SYNC_TABLES)  # a partial batch would not count toward the 30-day rule
    rep["result"] = "PASS" if ok else "FAIL"
    (ROOT / "docs" / f"activation_dryrun_{stamp}.json").write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "sources"}, indent=2, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
