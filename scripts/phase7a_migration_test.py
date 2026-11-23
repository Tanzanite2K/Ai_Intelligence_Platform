"""Phase 7A migration test: Alembic baseline + history tables, run ONLY on copies of the database.

What it does (every step is recorded in the report, with checksums):

 1. Pre-flight: the work folder must be outside OneDrive and outside the project; records the live database's
    SHA-256, size and modification time.
 2. Backup: SQLite online-backup API from the live file opened read-only (``mode=ro``) into the work folder; integrity
    check and row counts on both sides.
 3. Working copy of that backup; every migration step runs on copies, never on the live file.
 4. Schema check: a fresh database built by revision 0001_baseline must have exactly the same table and index DDL as
    the existing database, and Alembic's autogenerate comparison against the ORM models must find no difference.
 5. ``upgrade 0001`` on the existing copy must refuse (tables exist); ``stamp 0001_baseline`` marks it instead.
 6. ``upgrade head``, a second no-op ``upgrade head``, constraint probes on a throw-away copy, ``downgrade
    0001_baseline``, a refused ``downgrade base``, and ``upgrade head`` again. After every step the seven existing
    tables must be byte-for-byte identical (DDL, row count and a SHA-256 over every row).
 7. Restore: the backup restored to a new file, and restored over a migrated copy (rollback), must match the
    original exactly.
 8. The live database must be unchanged at the end (same SHA-256, size and modification time).

Usage:  python scripts/phase7a_migration_test.py [--source data/platform.db] [--workdir C:\\FrontierAtlas-test]
Exit code 0 only if every check passes. Reports: docs/phase7a_migration_test_<stamp>.json and .md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import time
import traceback
from argparse import Namespace
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASELINE_TABLES = ("canonical_entities", "crawl_checkpoints", "dead_letters", "entity_mappings", "records", "seen_items", "source_runs")
HISTORY_TABLES = (
    "catalogue_scans", "claims", "entities", "entity_resolution_events", "evidence_links", "ingestion_batches",
    "metric_snapshots", "presence_events", "record_observations", "record_versions", "source_groups",
    "source_reliability_daily",
)
BASELINE_REV, HEAD_REV = "0001_baseline", "0002_history_tables"


# ----------------------------------------------------------------------------------------------- helpers
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_facts(p: Path) -> dict[str, Any]:
    st = p.stat()
    return {"path": str(p), "sha256": sha256_file(p), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}


def ro(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)


def norm(sql: str | None) -> str:
    return " ".join((sql or "").split())


def tables(p: Path) -> set[str]:
    with ro(p) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}


def fingerprint(p: Path, names: tuple[str, ...] = BASELINE_TABLES) -> dict[str, dict[str, Any]]:
    """DDL, index DDL, row count and a SHA-256 over every row (in rowid order) for each table."""
    out: dict[str, dict[str, Any]] = {}
    c = ro(p)
    try:
        for t in names:
            row = c.execute("select sql from sqlite_master where type='table' and name=?", (t,)).fetchone()
            if row is None:
                out[t] = {"missing": True}
                continue
            idx = sorted(norm(s) for (s,) in c.execute("select sql from sqlite_master where type='index' and tbl_name=? and sql is not null", (t,)))
            cols = [r[1] for r in c.execute(f'pragma table_info("{t}")')]
            h = hashlib.sha256(repr(cols).encode())
            n = 0
            for r in c.execute(f'select * from "{t}" order by rowid'):
                h.update(repr(r).encode())
                n += 1
            out[t] = {"ddl_sha256": hashlib.sha256(norm(row[0]).encode()).hexdigest(), "index_ddl": idx, "rows": n, "data_sha256": h.hexdigest()}
    finally:
        c.close()
    return out


def schema_ddl(p: Path, names: tuple[str, ...]) -> dict[str, list[str]]:
    with ro(p) as c:
        return {t: [norm(s) for (s,) in c.execute("select sql from sqlite_master where tbl_name=? and sql is not null order by type desc, name", (t,))] for t in names}


def integrity(p: Path) -> str:
    with ro(p) as c:
        return c.execute("pragma integrity_check").fetchone()[0]


def revision(p: Path) -> str | None:
    with ro(p) as c:
        if not c.execute("select 1 from sqlite_master where type='table' and name='alembic_version'").fetchone():
            return None
        r = c.execute("select version_num from alembic_version").fetchall()
        return ",".join(x[0] for x in r) or None


def online_backup(src: Path, dst: Path, *, src_readonly: bool = True) -> None:
    s = ro(src) if src_readonly else sqlite3.connect(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def alembic_cfg(db: Path, *extra: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.cmd_opts = Namespace(x=[f"db_url=sqlite+aiosqlite:///{db.resolve().as_posix()}", *extra])
    cfg.skip_logging_config = True  # read by migrations/env.py
    return cfg


def alembic(action: str, db: Path, target: str | None = None) -> None:
    from alembic import command

    cfg = alembic_cfg(db)
    if action == "upgrade":
        command.upgrade(cfg, target or "head")
    elif action == "downgrade":
        command.downgrade(cfg, target or BASELINE_REV)
    elif action == "stamp":
        command.stamp(cfg, target or BASELINE_REV)
    else:
        raise ValueError(action)


# ----------------------------------------------------------------------------------------------- runner
class Run:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def step(self, name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"step": len(self.steps) + 1, "name": name}
        try:
            details = fn() or {}
            ok = bool(details.pop("ok", True))
            rec.update(ok=ok, details=details)
        except Exception as e:  # recorded, never hidden
            rec.update(ok=False, details={"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc(limit=4)})
        rec["seconds"] = round(time.perf_counter() - t0, 3)
        self.steps.append(rec)
        print(f"[{'PASS' if rec['ok'] else 'FAIL'}] {rec['step']:>2}. {name} ({rec['seconds']} s)", flush=True)
        if not rec["ok"]:
            print("      " + json.dumps(rec["details"], default=str)[:600], flush=True)
        return rec

    @property
    def ok(self) -> bool:
        return all(s["ok"] for s in self.steps)


def same(a: dict, b: dict) -> tuple[bool, list[str]]:
    diff = [t for t in sorted(set(a) | set(b)) if a.get(t) != b.get(t)]
    return not diff, diff


# ----------------------------------------------------------------------------------------------- probes
def constraint_probes(db: Path) -> dict[str, Any]:
    """Insert valid and invalid rows into the history tables of a throw-away copy; every invalid row must be refused."""
    c = sqlite3.connect(db, isolation_level=None)
    c.execute("pragma foreign_keys = on")
    results: list[dict[str, Any]] = []
    ts = "2026-11-23 02:00:00.000000"

    def expect(name: str, sql: str, params: tuple = (), *, refused: bool, match: str | None = None, rows: tuple[str, int] | None = None) -> None:
        got_refused, msg = False, ""
        try:
            c.execute(sql, params)
        except sqlite3.IntegrityError as e:
            got_refused, msg = True, str(e)
        ok = got_refused == refused and (match is None or match in msg)
        if rows is not None:
            n = c.execute(f"select count(*) from {rows[0]}").fetchone()[0]
            ok = ok and n == rows[1]
            msg = (msg + f" | {rows[0]} rows={n}").strip(" |")
        results.append({"case": name, "expected": "refused" if refused else "accepted", "result": "refused" if got_refused else "accepted", "message": msg, "ok": ok})

    B1, B2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    ins_batch = "insert into ingestion_batches(batch_id,trigger,scheduled_for,started_at,status) values (?,?,?,?,?)"
    expect("batch: first daily slot", ins_batch, (B1, "daily_0730", ts, ts, "running"), refused=False)
    expect("batch: same trigger + slot again (retry must resume, not duplicate)", ins_batch, (B2, "daily_0730", ts, ts, "running"), refused=True, match="UNIQUE")
    expect("batch: second batch, other slot", ins_batch, (B2, "news_jobs_1330", ts, ts, "running"), refused=False)

    obs = "insert into record_observations(batch_id,source_name,record_type,dedup_hash,observed_at,content_hash,observation_origin) values (?,?,?,?,?,?,?)"
    o = (B1, "Y Combinator", "STARTUP", "h1", ts, "c1", "live")
    expect("observation: first", obs, o, refused=False)
    expect("observation: retry with ON CONFLICT DO NOTHING writes nothing", obs + " on conflict do nothing", o, refused=False, rows=("record_observations", 1))
    expect("observation: duplicate without ON CONFLICT is refused", obs, o, refused=True, match="UNIQUE")
    expect("observation: same item in another batch is a new observation", obs, (B2, *o[1:]), refused=False, rows=("record_observations", 2))
    expect("observation: unknown origin refused", obs, (B1, "Y Combinator", "STARTUP", "h2", ts, "c", "guess"), refused=True, match="CHECK")
    expect("observation: unknown batch refused (foreign key)", obs, ("no-such-batch", "Y Combinator", "STARTUP", "h3", ts, "c", "live"), refused=True, match="FOREIGN KEY")
    expect("observation: baseline_backfill origin accepted", obs, (B1, "Y Combinator", "STARTUP", "h4", ts, "c4", "baseline_backfill"), refused=False)

    ver = "insert into record_versions(record_type,dedup_hash,version_no,content_hash,payload,valid_from,valid_to,batch_id,version_origin) values (?,?,?,?,?,?,?,?,?)"
    expect("version: v1 open", ver, ("STARTUP", "h1", 1, "c1", "{}", ts, None, B1, "baseline_backfill"), refused=False)
    expect("version: second open version for the same record refused", ver, ("STARTUP", "h1", 2, "c2", "{}", ts, None, B2, "observed_change"), refused=True, match="UNIQUE")
    c.execute("update record_versions set valid_to=? where dedup_hash='h1' and version_no=1", ("2026-11-23 02:05:00.000000",))
    expect("version: v2 open after v1 closed", ver, ("STARTUP", "h1", 2, "c2", "{}", "2026-11-23 02:05:00.000000", None, B2, "observed_change"), refused=False)
    expect("version: duplicate version number refused", ver, ("STARTUP", "h1", 2, "c9", "{}", ts, ts, B2, "observed_change"), refused=True, match="UNIQUE")
    expect("version: same content in the same batch refused (retry)", ver, ("STARTUP", "h1", 3, "c2", "{}", ts, ts, B2, "observed_change"), refused=True, match="UNIQUE")
    expect("version: unknown origin refused", ver, ("STARTUP", "h5", 1, "c", "{}", ts, None, B1, "reconstructed"), refused=True, match="CHECK")
    expect("version: valid_to before valid_from refused", ver, ("STARTUP", "h6", 1, "c", "{}", ts, "2026-01-01 00:00:00.000000", B1, "observed_change"), refused=True, match="CHECK")

    met = "insert into metric_snapshots(subject_kind,subject_key,metric,value,observed_at,source_name,batch_id) values (?,?,?,?,?,?,?)"
    m = ("RESEARCH_PAPER", "arxiv:2501.00001", "github_stars", 120.0, ts, "Hugging Face Papers", B1)
    expect("metric: first sample", met, m, refused=False)
    expect("metric: same subject/metric/source/batch refused (retry)", met, m, refused=True, match="UNIQUE")
    expect("metric: next batch accepted", met, (*m[:6], B2), refused=False)

    scan = "insert into catalogue_scans(scan_id,batch_id,source_name,pages_expected,pages_fetched,items_listed,complete,incomplete_reason) values (?,?,?,?,?,?,?,?)"
    expect("scan: complete scan with a reason refused", scan, ("s0", B1, "greenhouse", 3, 3, 10, 1, "oops"), refused=True, match="CHECK")
    expect("scan: incomplete scan with reason", scan, ("s1", B1, "Y Combinator", 5, 3, 900, 0, "rate limited after page 3"), refused=False)
    expect("scan: complete scan", scan, ("s2", B2, "Y Combinator", 5, 5, 1500, 1, None), refused=False)
    expect("scan: second scan of a source in one batch refused", scan, ("s3", B2, "Y Combinator", 5, 5, 1500, 1, None), refused=True, match="UNIQUE")

    pres = "insert into presence_events(record_type,dedup_hash,source_name,event,batch_id,scan_id,observed_at) values (?,?,?,?,?,?,?)"
    expect("presence: disappeared without a catalogue scan refused", pres, ("STARTUP", "h1", "Y Combinator", "disappeared", B2, None, ts), refused=True, match="CHECK")
    expect("presence: disappeared with a scan", pres, ("STARTUP", "h1", "Y Combinator", "disappeared", B2, "s2", ts), refused=False)
    expect("presence: duplicate event in the same batch refused", pres, ("STARTUP", "h1", "Y Combinator", "disappeared", B2, "s2", ts), refused=True, match="UNIQUE")
    expect("presence: unknown event refused", pres, ("STARTUP", "h1", "Y Combinator", "deleted", B2, "s2", ts), refused=True, match="CHECK")

    ent = "insert into entities(entity_id,canonical_name,normalized_key,origin,status,merged_into) values (?,?,?,?,?,?)"
    expect("entity: first active", ent, ("e1", "OpenAI", "openai", "seed", "active", None), refused=False)
    expect("entity: second active with the same key refused", ent, ("e2", "Open AI", "openai", "discovered", "active", None), refused=True, match="UNIQUE")
    expect("entity: merged without a target refused", ent, ("e3", "Open AI", "openai", "discovered", "merged", None), refused=True, match="CHECK")
    expect("entity: merged with a target (same key allowed when merged)", ent, ("e3", "Open AI", "openai", "discovered", "merged", "e1"), refused=False)

    res = "insert into entity_resolution_events(entity_id,raw_name,source_url,method,confidence,decided_by,resolver_version,decided_at,superseded_at) values (?,?,?,?,?,?,?,?,?)"
    expect("resolution: open decision", res, ("e1", "OpenAI, Inc.", "https://x", "ALIAS", 0.99, "rule", "1", ts, None), refused=False)
    expect("resolution: second open decision for the same name+source refused", res, ("e1", "OpenAI, Inc.", "https://x", "EXACT", 1.0, "rule", "2", ts, None), refused=True, match="UNIQUE")
    c.execute("update entity_resolution_events set superseded_at=? where raw_name='OpenAI, Inc.'", (ts,))
    expect("resolution: new decision after supersede", res, ("e1", "OpenAI, Inc.", "https://x", "EXACT", 1.0, "human", "2", ts, None), refused=False)

    clm = "insert into claims(claim_id,claim_kind,subject_entity_id,predicate,object_kind,object_key,value,first_observed_at,last_observed_at,support_tier,independent_groups) values (?,?,?,?,?,?,?,?,?,?,?)"
    expect("claim: corroborated with one source group refused", clm, ("k0", "hiring", "e1", "hires_for", "role", "ml-engineer", "", ts, ts, "corroborated", 1), refused=True, match="CHECK")
    expect("claim: single source", clm, ("k1", "hiring", "e1", "hires_for", "role", "ml-engineer", "", ts, ts, "single_source", 1), refused=False)
    expect("claim: same statement twice refused", clm, ("k2", "hiring", "e1", "hires_for", "role", "ml-engineer", "", ts, ts, "single_source", 1), refused=True, match="UNIQUE")

    ev = "insert into evidence_links(claim_id,record_type,dedup_hash,version_no,source_name,source_group,primary_url,content_fingerprint,snippet,evidence_kind) values (?,?,?,?,?,?,?,?,?,?)"
    expect("evidence: link to an existing record version", ev, ("k1", "STARTUP", "h1", 1, "Y Combinator", "yc", None, None, "We are hiring ML engineers", "primary"), refused=False)
    expect("evidence: same version twice for one claim refused", ev, ("k1", "STARTUP", "h1", 1, "Y Combinator", "yc", None, None, "We are hiring ML engineers", "primary"), refused=True, match="UNIQUE")
    expect("evidence: link to a missing record version refused (foreign key)", ev, ("k1", "STARTUP", "nope", 1, "Y Combinator", "yc", None, None, "x", "primary"), refused=True, match="FOREIGN KEY")
    expect("evidence: empty snippet refused", ev, ("k1", "STARTUP", "h1", 2, "Y Combinator", "yc", None, None, "", "reported"), refused=True, match="CHECK")

    rel = "insert into source_reliability_daily(source,day,runs,ok_runs) values (?,?,?,?)"
    expect("reliability: ok_runs above runs refused", rel, ("yc_startups", "2026-11-23", 1, 2), refused=True, match="CHECK")
    expect("reliability: one row per source and day", rel, ("yc_startups", "2026-11-23", 1, 1), refused=False)
    expect("reliability: second row for the same day refused", rel, ("yc_startups", "2026-11-23", 1, 1), refused=True, match="UNIQUE")
    fk = c.execute("pragma foreign_key_check").fetchall()
    c.close()
    return {"ok": all(r["ok"] for r in results) and not fk, "cases": results, "passed": sum(r["ok"] for r in results), "total": len(results), "foreign_key_violations": fk}


# ----------------------------------------------------------------------------------------------- main
def default_workdir() -> Path:
    return Path(r"C:\FrontierAtlas-test") if os.name == "nt" else Path.home() / "FrontierAtlas-test"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=str(ROOT / "data" / "platform.db"))
    ap.add_argument("--workdir", default=str(default_workdir()))
    ap.add_argument("--report-dir", default=str(ROOT / "docs"))
    a = ap.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    src = Path(a.source).resolve()
    work_root = Path(a.workdir).resolve()
    W = work_root / f"phase7a-{stamp}"
    run = Run()
    ctx: dict[str, Any] = {}
    print(f"Phase 7A migration test {stamp}\n  source  : {src}\n  workdir : {W}\n", flush=True)

    def preflight() -> dict[str, Any]:
        problems = []
        if "onedrive" in str(work_root).lower():
            problems.append("work folder is inside OneDrive")
        if work_root == ROOT or ROOT in work_root.parents:
            problems.append("work folder is inside the project")
        if not src.exists():
            problems.append("source database not found")
        if problems:
            return {"ok": False, "problems": problems}
        W.mkdir(parents=True, exist_ok=False)
        ctx["live_start"] = file_facts(src)
        import alembic
        import sqlalchemy

        return {"live_database": ctx["live_start"], "workdir": str(W), "python": platform.python_version(), "platform": platform.platform(),
                "sqlite": sqlite3.sqlite_version, "sqlalchemy": sqlalchemy.__version__, "alembic": alembic.__version__,
                "live_opened": "read-only (mode=ro), SQLite online backup API only"}

    if not run.step("Pre-flight: work folder outside OneDrive and project; record live database checksum", preflight)["ok"]:
        return finish(run, stamp, a, ctx)

    backup = W / "backup_pre_migration.db"
    work = W / "work.db"

    def do_backup() -> dict[str, Any]:
        online_backup(src, backup)
        with ro(src) as s, ro(backup) as b:
            cs = {t: s.execute(f'select count(*) from "{t}"').fetchone()[0] for t in BASELINE_TABLES}
            cb = {t: b.execute(f'select count(*) from "{t}"').fetchone()[0] for t in BASELINE_TABLES}
        ctx["backup"] = file_facts(backup)
        si, bi = integrity(src), integrity(backup)
        return {"ok": si == "ok" and bi == "ok" and cs == cb, "backup": ctx["backup"], "source_integrity": si, "backup_integrity": bi, "row_counts": cb}

    run.step("Verified backup of the live database (online backup API)", do_backup)

    def do_copy() -> dict[str, Any]:
        shutil.copy2(backup, work)
        f = file_facts(work)
        ctx["F0"] = fingerprint(work)
        tbl = tables(work)
        return {"ok": f["sha256"] == ctx["backup"]["sha256"] and tbl == set(BASELINE_TABLES), "work_copy": f, "tables": sorted(tbl),
                "fingerprint": ctx["F0"]}

    run.step("Working copy identical to the backup; baseline fingerprint of the 7 existing tables", do_copy)

    def schema_vs_baseline() -> dict[str, Any]:
        fresh = W / "fresh_from_0001.db"
        alembic("upgrade", fresh, BASELINE_REV)
        a_ddl, b_ddl = schema_ddl(fresh, BASELINE_TABLES), schema_ddl(work, BASELINE_TABLES)
        diffs = {t: {"revision_0001": a_ddl[t], "existing": b_ddl[t]} for t in BASELINE_TABLES if a_ddl[t] != b_ddl[t]}
        from alembic.autogenerate import compare_metadata
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine

        from src.database.models import Base

        eng = create_engine("sqlite://", creator=lambda: ro(work))  # read-only connection to the copy
        try:
            with eng.connect() as conn:
                mc = MigrationContext.configure(conn, opts={"compare_type": True})
                model_diff = [str(d) for d in compare_metadata(mc, Base.metadata)]
        finally:
            eng.dispose()
        return {"ok": not diffs and not model_diff and revision(fresh) == BASELINE_REV, "ddl_differences": diffs,
                "orm_vs_existing_differences": model_diff, "fresh_db_revision": revision(fresh), "tables_compared": list(BASELINE_TABLES)}

    run.step("Schema check: revision 0001 DDL == existing DDL, and ORM models == existing schema", schema_vs_baseline)

    def refuse_upgrade_existing() -> dict[str, Any]:
        refused, msg = False, ""
        try:
            alembic("upgrade", work, BASELINE_REV)
        except RuntimeError as e:
            refused, msg = True, str(e)
        ok_same, diff = same(fingerprint(work), ctx["F0"])
        return {"ok": refused and ok_same and revision(work) is None, "refused": refused, "message": msg[:300], "existing_tables_unchanged": ok_same, "diff": diff}

    run.step("Safety: 'upgrade 0001' on the existing database is refused (tables exist)", refuse_upgrade_existing)

    def check_state(expected_rev: str | None, history_present: bool) -> dict[str, Any]:
        ok_same, diff = same(fingerprint(work), ctx["F0"])
        tbl = tables(work)
        hist = sorted(set(HISTORY_TABLES) & tbl)
        empty = True
        if history_present:
            with ro(work) as c:
                empty = all(c.execute(f'select count(*) from "{t}"').fetchone()[0] == 0 for t in HISTORY_TABLES)
        ok = ok_same and revision(work) == expected_rev and (set(hist) == set(HISTORY_TABLES) if history_present else not hist) and empty and integrity(work) == "ok"
        return {"ok": ok, "revision": revision(work), "existing_tables_unchanged": ok_same, "changed_tables": diff,
                "history_tables_present": len(hist), "history_tables_empty": empty, "integrity": integrity(work),
                "unexpected_tables": sorted(tbl - set(BASELINE_TABLES) - set(HISTORY_TABLES) - {"alembic_version"})}

    run.step("stamp 0001_baseline on the existing copy", lambda: (alembic("stamp", work, BASELINE_REV), check_state(BASELINE_REV, False))[1])

    def up_head() -> dict[str, Any]:
        alembic("upgrade", work, "head")
        st = check_state(HEAD_REV, True)
        ctx["head_ddl"] = schema_ddl(work, HISTORY_TABLES)
        st["history_tables"] = {t: len(v) for t, v in ctx["head_ddl"].items()}
        st["history_ddl_sha256"] = hashlib.sha256(json.dumps(ctx["head_ddl"], sort_keys=True).encode()).hexdigest()
        return st

    run.step("upgrade head (0002_history_tables): 12 new empty tables, existing tables unchanged", up_head)

    def up_again() -> dict[str, Any]:
        before = file_facts(work)["sha256"]
        alembic("upgrade", work, "head")
        st = check_state(HEAD_REV, True)
        st["file_unchanged_by_noop_upgrade"] = file_facts(work)["sha256"] == before
        st["ok"] = st["ok"] and st["file_unchanged_by_noop_upgrade"]
        return st

    run.step("upgrade head again is a no-op (idempotent)", up_again)

    def probes() -> dict[str, Any]:
        probe = W / "constraint_probe.db"
        shutil.copy2(work, probe)
        r = constraint_probes(probe)
        ok_same, diff = same(fingerprint(probe), ctx["F0"])
        r["existing_tables_unchanged_on_probe"] = ok_same
        r["ok"] = r["ok"] and ok_same
        probe.unlink()
        r["probe_copy_deleted"] = True
        return r

    run.step("Constraint probes on a throw-away copy (idempotency, disappearance, corroboration, versions)", probes)

    def down_baseline() -> dict[str, Any]:
        alembic("downgrade", work, BASELINE_REV)
        return check_state(BASELINE_REV, False)

    run.step("downgrade 0001_baseline: history tables removed, existing tables unchanged", down_baseline)

    def refuse_base() -> dict[str, Any]:
        refused, msg = False, ""
        try:
            alembic("downgrade", work, "base")
        except RuntimeError as e:
            refused, msg = True, str(e)
        st = check_state(BASELINE_REV, False)
        st.update(refused=refused, message=msg[:300])
        st["ok"] = st["ok"] and refused
        return st

    run.step("Safety: 'downgrade base' (would drop production tables) is refused", refuse_base)

    def reupgrade() -> dict[str, Any]:
        alembic("upgrade", work, "head")
        st = check_state(HEAD_REV, True)
        st["history_ddl_identical_to_first_upgrade"] = schema_ddl(work, HISTORY_TABLES) == ctx["head_ddl"]
        st["ok"] = st["ok"] and st["history_ddl_identical_to_first_upgrade"]
        ctx["work_final"] = file_facts(work)
        return st

    run.step("Re-upgrade head: identical history schema, existing tables unchanged", reupgrade)

    def live_guard_real() -> dict[str, Any]:
        live = (ROOT / "data" / "platform.db").resolve()
        if src != live:
            return {"skipped": True, "reason": "source is not the project's live database path"}
        from alembic import command

        refused, msg = False, ""
        try:
            command.current(alembic_cfg(live))  # read-only command; the guard must stop it before any connection
        except Exception as e:
            refused, msg = type(e).__name__ == "LiveDatabaseRefused", str(e)
        unchanged = file_facts(live) == ctx["live_start"]
        return {"ok": refused and unchanged, "refused": refused, "message": msg[:300], "live_file_unchanged": unchanged}

    run.step("Safety: Alembic refuses to run against the live data/platform.db without explicit approval", live_guard_real)

    def restore_new() -> dict[str, Any]:
        restored = W / "restored_from_backup.db"
        bk_now = file_facts(backup)
        online_backup(backup, restored)
        ok_same, diff = same(fingerprint(restored), ctx["F0"])
        tbl = tables(restored)
        return {"ok": ok_same and integrity(restored) == "ok" and tbl == set(BASELINE_TABLES) and bk_now["sha256"] == ctx["backup"]["sha256"],
                "backup_sha256_still_matches": bk_now["sha256"] == ctx["backup"]["sha256"], "restored": file_facts(restored),
                "integrity": integrity(restored), "matches_original": ok_same, "diff": diff, "tables": sorted(tbl), "revision": revision(restored)}

    run.step("Backup restore to a new file: integrity ok, identical to the original", restore_new)

    def restore_rollback() -> dict[str, Any]:
        rb = W / "rollback_target.db"
        shutil.copy2(work, rb)  # a migrated database (at head)
        before = {"revision": revision(rb), "tables": len(tables(rb))}
        online_backup(backup, rb)  # restore the pre-migration backup over it
        ok_same, diff = same(fingerprint(rb), ctx["F0"])
        tbl = tables(rb)
        return {"ok": ok_same and tbl == set(BASELINE_TABLES) and revision(rb) is None and integrity(rb) == "ok",
                "before_restore": before, "after_restore": {"revision": revision(rb), "tables": sorted(tbl), "integrity": integrity(rb)},
                "matches_original": ok_same, "diff": diff, "file": file_facts(rb)}

    run.step("Rollback by restore: pre-migration backup restored over a migrated copy", restore_rollback)

    def live_end() -> dict[str, Any]:
        end = file_facts(src)
        ctx["live_end"] = end
        return {"ok": end == ctx["live_start"], "start": ctx["live_start"], "end": end}

    run.step("Live database unchanged (SHA-256, size, modification time)", live_end)
    return finish(run, stamp, a, ctx)


def finish(run: Run, stamp: str, a: argparse.Namespace, ctx: dict[str, Any]) -> int:
    rep = {"report": "phase7a_migration_test", "stamp": stamp, "result": "PASS" if run.ok else "FAIL", "passed": sum(s["ok"] for s in run.steps),
           "total": len(run.steps), "source": str(Path(a.source).resolve()), "workdir": str(Path(a.workdir).resolve()), "steps": run.steps}
    out = Path(a.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    j = out / f"phase7a_migration_test_{stamp}.json"
    j.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    md = [f"# Phase 7A migration test {stamp}: {rep['result']} ({rep['passed']}/{rep['total']} steps)", "",
          f"Source: `{rep['source']}`  ", f"Work folder: `{rep['workdir']}`", "", "| # | Step | Result | Seconds |", "| --- | --- | --- | --- |"]
    md += [f"| {s['step']} | {s['name']} | {'PASS' if s['ok'] else 'FAIL'} | {s['seconds']} |" for s in run.steps]
    if "live_start" in ctx:
        md += ["", f"Live database SHA-256 at start: `{ctx['live_start']['sha256']}`"]
    if "live_end" in ctx:
        md += [f"Live database SHA-256 at end:   `{ctx['live_end']['sha256']}`"]
    if "backup" in ctx:
        md += [f"Backup `{ctx['backup']['path']}` SHA-256 `{ctx['backup']['sha256']}`"]
    md += ["", f"Full details: `{j.name}`"]
    (out / f"phase7a_migration_test_{stamp}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nRESULT: {rep['result']} ({rep['passed']}/{rep['total']})\nReport: {j}", flush=True)
    return 0 if run.ok else 1


if __name__ == "__main__":
    sys.exit(main())
