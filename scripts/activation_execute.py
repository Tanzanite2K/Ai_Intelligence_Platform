"""AUTHORIZED production activation (2026-11-23, user authorization "AUTHORIZE PRODUCTION WRITES").

Stops at the first unexpected condition and leaves the database and backups as they are (no automatic restore).
  0. No writer: ports 8000/3000 closed, no -journal/-wal/-shm, mtime stable 5 s.
  1. Backup C:\\FrontierAtlas-backups\\activation-20261123T083552Z: SHA256SUMS match, cold hash == expected, integrity + FK on both.
  2. Production SHA-256 == expected; settings: HISTORY_ENABLED=true, API_READ_ONLY=true, DATABASE_URL -> data/platform.db.
  3. Snapshot (read-only): counts + per-table content digests.
  4. collect --trigger manual --no-llm --concurrency 1 --source x10   (LLM construction/extraction calls and HTTP hosts recorded)
  5. Batch must be 'completed'; otherwise STOP (exit 3) without sync-evidence.
  6. sync-evidence
  7. Verify: integrity, FK, revision, counts, digests -> only allowed tables changed, schema unchanged, source runs, 0 LLM calls.
Report: docs/activation_execution_<stamp>.json. Exit 0 = success; 2 = refused before any write; 3 = stopped after a write.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
LIVE = (ROOT / "data" / "platform.db").resolve()
EXPECTED = "c916f9cb81a036537318db3b9223c563ef7e3bcd3618269362017180dc1badff"
BACKUP = Path(r"C:\FrontierAtlas-backups\activation-20261123T083552Z")
SOURCES = ["techcrunch_ai", "theverge_ai", "mit_tr_ai", "arstechnica_ai", "openai_news", "greenhouse", "ashby", "lever", "remoteok", "himalayas"]
COLLECT_TABLES = {"records", "record_observations", "record_versions", "presence_events", "ingestion_batches", "source_runs",
                  "crawl_checkpoints", "entity_mappings", "dead_letters", "metric_snapshots", "catalogue_scans"}
SYNC_TABLES = {"entities", "entity_resolution_events", "claims", "evidence_links"}
LLM_HOSTS = ("generativelanguage.googleapis.com", "api.groq.com", "api.deepseek.com", "api.openai.com", "api.anthropic.com")
STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
REP: dict[str, object] = {"stamp": STAMP, "authorized_by_user": "AUTHORIZE PRODUCTION WRITES (2026-11-23 14:08 IST)", "steps": []}


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}Z] {msg}", flush=True)


def step(name: str, ok: bool, **info: object) -> None:
    REP["steps"].append({"step": name, "ok": ok, "at": datetime.now(UTC).isoformat(), **info})  # type: ignore[union-attr]
    log(f"{'PASS' if ok else 'FAIL'}  {name}  {json.dumps(info, default=str)[:400]}")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def ro(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)


def state(p: Path) -> dict[str, object]:
    with closing(ro(p)) as c:
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        dig = {"__schema__": hashlib.sha256("\n".join(r[0] or "" for r in c.execute("select sql from sqlite_master order by name")).encode()).hexdigest()}
        for t in tables:
            h = hashlib.sha256()
            for row in c.execute(f'select * from "{t}" order by rowid'):
                h.update(repr(row).encode())
            dig[t] = h.hexdigest()
        return {"counts": {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}, "digests": dig,
                "integrity": c.execute("pragma integrity_check").fetchone()[0], "fk": len(c.execute("pragma foreign_key_check").fetchall()),
                "revision": c.execute("select version_num from alembic_version").fetchone()[0]}


def finish(code: int, outcome: str) -> int:
    REP["outcome"], REP["exit_code"] = outcome, code
    try:
        REP["production_sha256_final"] = sha(LIVE)
    except OSError as e:
        REP["production_sha256_final"] = f"unreadable: {e}"
    (ROOT / "docs" / f"activation_execution_{STAMP}.json").write_text(json.dumps(REP, indent=2, default=str), encoding="utf-8")
    log(f"OUTCOME: {outcome} (exit {code})  report docs/activation_execution_{STAMP}.json")
    return code


def main() -> int:
    log(f"Production activation {STAMP}  target {LIVE}")
    # 0. no writer
    ports = {}
    for port in (8000, 3000):
        with socket.socket() as s:
            s.settimeout(0.5)
            ports[port] = s.connect_ex(("127.0.0.1", port)) == 0
    side = [x for x in ("-journal", "-wal", "-shm") if LIVE.with_name(LIVE.name + x).exists()]
    m0 = LIVE.stat().st_mtime_ns
    time.sleep(5)
    stable = LIVE.stat().st_mtime_ns == m0
    ok = not any(ports.values()) and not side and stable
    step("no writer (app stopped)", ok, ports_open=ports, side_files=side, mtime_stable_5s=stable)
    if not ok:
        return finish(2, "REFUSED: app or another writer active")
    # 1. backup
    cold, online = BACKUP / "platform.pre-activation.cold.db", BACKUP / "platform.pre-activation.online.db"
    sums = dict(reversed(line.split(None, 1)) for line in (BACKUP / "SHA256SUMS.txt").read_text(encoding="utf-8").split("\n") if line.strip())
    sums = {k.strip(): v for k, v in sums.items()}
    hc, ho = sha(cold), sha(online)
    with closing(ro(cold)) as c1, closing(ro(online)) as c2:
        ic, io_ = c1.execute("pragma integrity_check").fetchone()[0], c2.execute("pragma integrity_check").fetchone()[0]
        fc, fo = len(c1.execute("pragma foreign_key_check").fetchall()), len(c2.execute("pragma foreign_key_check").fetchall())
    ok = hc == EXPECTED and sums.get(cold.name) == hc and sums.get(online.name) == ho and ic == io_ == "ok" and fc == fo == 0 and "onedrive" not in str(BACKUP).lower()
    step("backup verified", ok, dir=str(BACKUP), cold_sha256=hc, online_sha256=ho, sums_match=[sums.get(cold.name) == hc, sums.get(online.name) == ho],
         integrity=[ic, io_], fk=[fc, fo])
    if not ok:
        return finish(2, "REFUSED: backup verification failed")
    # 2. production hash + settings
    h0 = sha(LIVE)
    from src.config.settings import get_settings

    st = get_settings()
    db_path = st.database_url.split(":///", 1)[1] if ":///" in st.database_url else ""
    db_ok = Path(db_path).resolve() == LIVE if db_path else False
    ok = h0 == EXPECTED and st.history_enabled is True and st.api_read_only is True and db_ok
    step("production hash + configuration", ok, production_sha256=h0, expected=EXPECTED, history_enabled=st.history_enabled,
         api_read_only=st.api_read_only, database_is_production=db_ok)
    if not ok:
        return finish(2, "REFUSED: hash or configuration mismatch")
    # 3. snapshot
    s0 = state(LIVE)
    REP["before"] = {k: s0[k] for k in ("counts", "integrity", "fk", "revision")}
    step("pre-write snapshot", s0["integrity"] == "ok" and s0["fk"] == 0, revision=s0["revision"])
    # spies (observe only)
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

    llm: Counter[str] = Counter()
    rb, rx = rt.build_providers, orch_mod.LLMOrchestrator.extract

    def b_spy(*a, **k):  # type: ignore[no-untyped-def]
        llm["build_providers"] += 1
        return rb(*a, **k)

    async def x_spy(self, *a, **k):  # type: ignore[no-untyped-def]
        llm["extract"] += 1
        return await rx(self, *a, **k)

    rt.build_providers = b_spy
    orch_mod.LLMOrchestrator.extract = x_spy  # type: ignore[method-assign]
    import src.main as cli

    # 4. collect  (FIRST PRODUCTION WRITE)
    argv = ["collect", "--trigger", "manual", "--no-llm", "--concurrency", "1", *[a for x in SOURCES for a in ("--source", x)]]
    log("$ python -m src.main " + " ".join(argv))
    REP["collect_command"] = "python -m src.main " + " ".join(argv)
    rc = cli.main(argv)
    s1 = state(LIVE)
    with closing(ro(LIVE)) as c:
        batch = c.execute("select batch_id, trigger, status, started_at, finished_at from ingestion_batches order by started_at desc limit 1").fetchone()
        runs = c.execute(f"select source, status, fetched, stored, error from source_runs order by id desc limit {len(SOURCES)}").fetchall()
    REP["batch"] = {"batch_id": batch[0], "trigger": batch[1], "status": batch[2], "started_at": batch[3], "finished_at": batch[4]}
    REP["source_runs"] = [{"source": r[0], "status": r[1], "fetched": r[2], "stored": r[3], "error": (r[4] or "")[:200]} for r in reversed(runs)]
    mod1 = sorted(t for t in s1["digests"] if s1["digests"][t] != s0["digests"].get(t))  # type: ignore[union-attr,attr-defined]
    REP["collect"] = {"exit": rc, "tables_modified": mod1, "delta": {t: s1["counts"][t] - s0["counts"].get(t, 0) for t in s1["counts"]  # type: ignore[index,union-attr,attr-defined]
                                                                     if s1["counts"][t] != s0["counts"].get(t, 0)}, "llm_calls": dict(llm)}  # type: ignore[index,union-attr,attr-defined]
    ok = (rc == 0 and batch[1] == "manual" and batch[2] == "completed" and "__schema__" not in mod1 and set(mod1) <= COLLECT_TABLES
          and not llm and s1["integrity"] == "ok" and s1["fk"] == 0)
    step("collect", ok, exit=rc, batch_status=batch[2], tables_modified=mod1, delta=REP["collect"]["delta"], llm_calls=dict(llm),  # type: ignore[index]
         source_statuses={r[0]: r[1] for r in runs}, integrity=s1["integrity"], fk=s1["fk"])
    if not ok:
        return finish(3, "STOPPED after collect: batch not completed or unexpected change; sync-evidence NOT run; database preserved")
    # 6. sync-evidence
    log("$ python -m src.main sync-evidence")
    rc2 = cli.main(["sync-evidence"])
    s2 = state(LIVE)
    mod2 = sorted(t for t in s2["digests"] if s2["digests"][t] != s1["digests"].get(t))  # type: ignore[union-attr,attr-defined]
    REP["sync_evidence"] = {"exit": rc2, "tables_modified": mod2, "delta": {t: s2["counts"][t] - s1["counts"].get(t, 0) for t in s2["counts"]  # type: ignore[index,union-attr,attr-defined]
                                                                           if s2["counts"][t] != s1["counts"].get(t, 0)}}  # type: ignore[index,union-attr,attr-defined]
    REP["after"] = {k: s2[k] for k in ("counts", "integrity", "fk", "revision")}
    REP["llm_calls_total"] = dict(llm)
    REP["hosts_contacted"] = dict(hosts)
    REP["llm_hosts_contacted"] = [h for h in hosts if h in LLM_HOSTS]
    ok = (rc2 == 0 and "__schema__" not in mod2 and set(mod2) <= SYNC_TABLES and s2["integrity"] == "ok" and s2["fk"] == 0
          and s2["revision"] == "0002_history_tables" and not llm and not REP["llm_hosts_contacted"])
    step("sync-evidence + final verification", ok, exit=rc2, tables_modified=mod2, delta=REP["sync_evidence"]["delta"],  # type: ignore[index]
         integrity=s2["integrity"], fk=s2["fk"], revision=s2["revision"], llm_calls=dict(llm), llm_hosts=REP["llm_hosts_contacted"])
    if not ok:
        return finish(3, "STOPPED after sync-evidence: verification failed; database preserved")
    return finish(0, "SUCCESS: collection completed, evidence synchronized, verification passed")


if __name__ == "__main__":
    raise SystemExit(main())
