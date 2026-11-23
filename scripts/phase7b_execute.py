"""Phase 7B orchestrator. ``--target copy`` is Run A (the rehearsal); ``--target live`` (Run B) is in phase7b_live.py.

Run A never writes to the live database:
* the live file is only read: hashed, copied byte for byte, and read through SQLite read-only connections;
* every write (Alembic, backfill, probes, restores) goes to files inside the run's work or backup folder, and each
  write helper asserts that its target is inside those folders;
* Alembic is called without ``allow_live`` (migrations/env.py refuses the live file), and the backfill and restore
  scripts refuse the live file without flags this script never passes;
* the live file's SHA-256, size and modification time are compared at the start and the end.

It does not stop the application, pause OneDrive, crawl, ingest, schedule anything or enable history collection.
On the first failed check it stops, writes the report, keeps every file as it is, and restores nothing.

Usage:  python scripts/phase7b_execute.py --target copy
Report: docs/phase7b_rehearsal_<stamp>.json and .md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import db_restore  # noqa: E402
import phase7a_migration_test as p7a  # noqa: E402
import phase7b_backfill as bf  # noqa: E402

LIVE_DB = (ROOT / "data" / "platform.db").resolve()
BASELINE_TABLES = p7a.BASELINE_TABLES
HISTORY_TABLES = p7a.HISTORY_TABLES
EXPECTED = {"records": 6708, "entity_mappings": 4944, "canonical_entities": 62, "source_runs": 42, "crawl_checkpoints": 18,
            "dead_letters": 16, "seen_items": 0}
EXPECTED_BY_TYPE = {"STARTUP": 2509, "RESEARCH_PAPER": 2290, "PRODUCT": 1800, "JOB": 93, "NEWS": 16}
API_PATHS = ("/health", "/api/v1/dashboard/overview", "/api/v1/startups", "/api/v1/products", "/api/v1/papers", "/api/v1/jobs",
             "/api/v1/news", "/api/v1/intelligence/brief", "/api/v1/intelligence/topics", "/api/v1/relationships/overview",
             "/api/v1/operations/ingestion-runs")
# Timestamps of when a response or in-memory index was built; they differ between any two API starts, not with the data.
VOLATILE_KEYS = {"index_built_at", "generated_at", "generatedAt"}


class Stop(Exception):
    pass


class Runner:
    def __init__(self, allowed: list[Path]) -> None:
        self.steps: list[dict[str, Any]] = []
        self.allowed = [p.resolve() for p in allowed]

    def writable(self, p: Path) -> Path:
        """Every file this run writes passes through here: it must be inside the run's own folders, never live."""
        r = p.resolve()
        if r == LIVE_DB or not any(a == r or a in r.parents for a in self.allowed):
            raise Stop(f"write target outside the rehearsal folders refused: {r}")
        return r

    def step(self, name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"step": len(self.steps) + 1, "name": name}
        try:
            d = fn() or {}
            rec.update(ok=bool(d.pop("ok", True)), details=d)
        except Exception as e:
            rec.update(ok=False, details={"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc(limit=5)})
        rec["seconds"] = round(time.perf_counter() - t0, 3)
        self.steps.append(rec)
        print(f"[{'PASS' if rec['ok'] else 'FAIL'}] {rec['step']:>2}. {name} ({rec['seconds']} s)", flush=True)
        if not rec["ok"]:
            print("      " + json.dumps(rec["details"], default=str)[:800], flush=True)
            raise Stop(name)
        return rec["details"]


def master_rows(p: Path, names: tuple[str, ...]) -> list[tuple]:
    with p7a.ro(p) as c:
        q = f"select type, name, tbl_name, sql from sqlite_master where tbl_name in ({','.join('?' * len(names))}) order by type, name"
        return [tuple(r) for r in c.execute(q, names)]


def counts(p: Path, names: tuple[str, ...]) -> dict[str, int]:
    with p7a.ro(p) as c:
        return {t: c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in names}


def orm_differences(p: Path) -> list[str]:
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine

    from src.database.models import Base

    eng = create_engine("sqlite://", creator=lambda: p7a.ro(p))
    try:
        with eng.connect() as conn:
            mc = MigrationContext.configure(conn, opts={"compare_type": True, "include_object": _include})
            return [str(d) for d in compare_metadata(mc, Base.metadata)]
    finally:
        eng.dispose()


def _include(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    if type_ == "table" and (name in HISTORY_TABLES or name == "alembic_version"):
        return False
    t = getattr(obj, "table", None)
    return not (t is not None and t.name in HISTORY_TABLES)


def history_checks(p: Path, total: int = 6708, by_type: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Section 8 of the execution plan: every check with its required value."""
    by_type = EXPECTED_BY_TYPE if by_type is None else by_type
    b = bf.BASELINE_BATCH_ID
    out: list[dict[str, Any]] = []
    c = p7a.ro(p)
    try:
        def chk(name: str, sql: str, expected: Any, params: tuple = ()) -> None:
            rows = c.execute(sql, params).fetchall()
            got = rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else [tuple(r) for r in rows]
            out.append({"check": name, "expected": expected, "got": got, "ok": got == expected})

        chk("batch rows", "select count(*) from ingestion_batches", 1)
        chk("batch label", "select batch_id, trigger, status, scheduled_for from ingestion_batches", [(b, "baseline_backfill", "completed", bf.BASELINE_SCHEDULED_FOR)])
        chk("observations", "select count(*) from record_observations", total)
        chk("observations not baseline or other batch", "select count(*) from record_observations where observation_origin <> 'baseline_backfill' or batch_id <> ?", 0, (b,))
        chk("observations, distinct records", "select count(*) from (select distinct record_type, dedup_hash from record_observations)", total)
        chk("observations mismatching records",
            "select count(*) from record_observations o left join records r on r.record_type=o.record_type and r.dedup_hash=o.dedup_hash "
            "where r.id is null or r.source_name<>o.source_name or r.content_hash<>o.content_hash or r.collected_at<>o.observed_at "
            "or coalesce(r.published_at,'')<>coalesce(o.published_at,'')", 0)
        chk("records without an observation",
            "select count(*) from records r where not exists (select 1 from record_observations o where o.record_type=r.record_type and o.dedup_hash=r.dedup_hash)", 0)
        chk("versions", "select count(*) from record_versions", total)
        chk("versions not v1/open/baseline", "select count(*) from record_versions where version_no<>1 or valid_to is not null or version_origin<>'baseline_backfill' or batch_id<>?", 0, (b,))
        chk("versions mismatching records (content_hash, payload, valid_from)",
            "select count(*) from record_versions v left join records r on r.record_type=v.record_type and r.dedup_hash=v.dedup_hash "
            "where r.id is null or r.content_hash<>v.content_hash or r.payload<>v.payload or r.collected_at<>v.valid_from", 0)
        chk("records without a version",
            "select count(*) from records r where not exists (select 1 from record_versions v where v.record_type=r.record_type and v.dedup_hash=r.dedup_hash)", 0)
        for t, n in sorted(by_type.items()):
            chk(f"{t}: records / observations / versions",
                "select (select count(*) from records where record_type=?), (select count(*) from record_observations where record_type=?), "
                "(select count(*) from record_versions where record_type=?)", [(n, n, n)], (t, t, t))
        for t in sorted(set(HISTORY_TABLES) - {"ingestion_batches", "record_observations", "record_versions"}):
            chk(f"{t} empty", f'select count(*) from "{t}"', 0)
        chk("presence events in the baseline batch", "select count(*) from presence_events where batch_id=?", 0, (b,))
        chk("newly discovered (section 6 rule)", f"select count(*) from ({bf.NEW_DISCOVERY_SQL})", 0)
        chk("foreign_key_check", "pragma foreign_key_check", [])
        chk("integrity_check", "pragma integrity_check", "ok")
        chk("alembic revision", "select version_num from alembic_version", "0002_history_tables")
    finally:
        c.close()
    return out


def new_discovery_probe(p: Path) -> dict[str, Any]:
    """On a throw-away copy: one live observation of a baseline record + one of an unseen record -> exactly 1 new."""
    con = sqlite3.connect(p, isolation_level=None)
    try:
        con.execute("pragma foreign_keys = on")
        before = len(con.execute(bf.NEW_DISCOVERY_SQL).fetchall())
        rt, dh, sn = con.execute("select record_type, dedup_hash, source_name from records order by id limit 1").fetchone()
        live = "00000000-0000-4000-8000-00000000a001"
        con.execute("insert into ingestion_batches(batch_id,trigger,scheduled_for,started_at,status) values (?,?,?,?,?)",
                    (live, "daily_0730", "2026-11-23 02:00:00.000000", "2026-11-23 02:00:00.000000", "running"))
        ins = ("insert into record_observations(batch_id,source_name,record_type,dedup_hash,observed_at,content_hash,observation_origin) "
               "values (?,?,?,?,?,?,?)")
        con.execute(ins, (live, sn, rt, dh, "2026-11-23 02:00:00.000000", "x", "live"))
        con.execute(ins, (live, "TechCrunch", "NEWS", "phase7b-rehearsal-unseen", "2026-11-23 02:00:00.000000", "y", "live"))
        after = [tuple(r) for r in con.execute(bf.NEW_DISCOVERY_SQL).fetchall()]
    finally:
        con.close()
    ok = before == 0 and after == [("NEWS", "phase7b-rehearsal-unseen")]
    return {"ok": ok, "new_before_probe": before, "baseline_record_seen_live_again": f"{rt}/{dh[:12]}", "new_after_probe": after}


def _normalise(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _normalise(v) for k, v in sorted(o.items()) if k not in VOLATILE_KEYS}
    if isinstance(o, list):
        return [_normalise(v) for v in o]
    return o


def api_smoke(db: Path) -> dict[str, Any]:
    """Runs in a child process with DATABASE_URL pointing at a copy and API_READ_ONLY=true."""
    from fastapi.testclient import TestClient

    from src.api.app import app

    out: dict[str, Any] = {}
    with TestClient(app) as client:
        for path in API_PATHS:
            r = client.get(path)
            body: Any
            try:
                body = _normalise(r.json())
            except ValueError:
                body = r.text
            out[path] = {"status": r.status_code, "sha256": hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()}
    return out


def run_api_smoke(db: Path) -> dict[str, Any]:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db.resolve().as_posix()}", "API_READ_ONLY": "true",
           "LOG_LEVEL": "WARNING", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--api-smoke", str(db)], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(f"api smoke failed: {p.stderr[-1500:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------------------------------------------------------- Run A
def run_copy(a: argparse.Namespace) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    W = Path(a.workdir).resolve() / f"phase7b-rehearsal-{stamp}"
    B = Path(a.backup_root).resolve() / f"phase7b-rehearsal-{stamp}"
    run = Runner([W, B])
    ctx: dict[str, Any] = {"stamp": stamp, "workdir": str(W), "backup_dir": str(B), "live": str(LIVE_DB)}
    cold, online, target = B / "platform.pre7b.cold.db", B / "platform.pre7b.online.db", W / "platform.db"
    print(f"Phase 7B Run A (rehearsal on a copy) {stamp}\n  live (read only): {LIVE_DB}\n  backups : {B}\n  work    : {W}\n", flush=True)
    try:
        def preflight() -> dict[str, Any]:
            problems = []
            for label, d in (("work folder", W), ("backup folder", B)):
                if "onedrive" in str(d).lower():
                    problems.append(f"{label} is inside OneDrive")
                if ROOT in d.parents:
                    problems.append(f"{label} is inside the project")
            if not LIVE_DB.exists():
                problems.append("live database not found")
            side = [s for s in ("-journal", "-wal", "-shm") if LIVE_DB.with_name(LIVE_DB.name + s).exists()]
            if side:
                problems.append(f"live database has {side} files (a writer may be active)")
            free = shutil.disk_usage(Path(a.backup_root).anchor or "/").free
            if free < 200 * 1024 * 1024:
                problems.append(f"only {free} bytes free")
            if problems:
                return {"ok": False, "problems": problems}
            run.writable(W).mkdir(parents=True)
            run.writable(B).mkdir(parents=True)
            ctx["live_start"] = p7a.file_facts(LIVE_DB)
            import alembic
            import sqlalchemy

            return {"live": ctx["live_start"], "live_alembic_revision": p7a.revision(LIVE_DB), "live_registered_with_alembic": p7a.revision(LIVE_DB) is not None,
                    "free_bytes": free, "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "alembic": alembic.__version__,
                    "sqlalchemy": sqlalchemy.__version__, "platform": platform.platform(), "app_running_left_untouched": True,
                    "onedrive_left_running": True, "backfill_code_version": bf.code_version(),
                    "ok": p7a.revision(LIVE_DB) is None}

        run.step("Pre-flight: folders outside OneDrive/project, live read-only facts, live not registered with Alembic", preflight)

        def backups() -> dict[str, Any]:
            before = p7a.file_facts(LIVE_DB)
            shutil.copyfile(LIVE_DB, run.writable(cold))
            after = p7a.file_facts(LIVE_DB)
            p7a.online_backup(LIVE_DB, run.writable(online))  # read-only source connection
            c, o = p7a.file_facts(cold), p7a.file_facts(online)
            ctx["cold"], ctx["online"] = c, o
            ok = before == after == ctx["live_start"] and c["sha256"] == before["sha256"]
            return {"ok": ok, "cold_copy": c, "online_backup": o, "live_unchanged_during_copy": before == after,
                    "cold_sha256_equals_live": c["sha256"] == before["sha256"]}

        run.step("External backups in C:\\FrontierAtlas-backups: cold byte-for-byte copy + online backup", backups)

        def verify_backups() -> dict[str, Any]:
            integ = {"live": p7a.integrity(LIVE_DB), "cold": p7a.integrity(cold), "online": p7a.integrity(online)}
            f_live, f_cold, f_online = p7a.fingerprint(LIVE_DB), p7a.fingerprint(cold), p7a.fingerprint(online)
            ctx["F0"] = f_live
            rows = {t: v["rows"] for t, v in f_live.items()}
            (run.writable(B / "fingerprints.pre7b.json")).write_text(json.dumps(f_live, indent=2), encoding="utf-8")
            (run.writable(B / "SHA256SUMS.txt")).write_text(
                f"{ctx['live_start']['sha256']}  live data\\platform.db (at start)\n{ctx['cold']['sha256']}  platform.pre7b.cold.db\n"
                f"{ctx['online']['sha256']}  platform.pre7b.online.db\n", encoding="utf-8")
            ok = all(v == "ok" for v in integ.values()) and f_live == f_cold == f_online and rows == EXPECTED
            return {"ok": ok, "integrity": integ, "cold_matches_live": f_cold == f_live, "online_matches_live": f_online == f_live,
                    "row_counts": rows, "row_counts_as_planned": rows == EXPECTED, "fingerprints": f_live}

        run.step("Backup verification: integrity, 7-table fingerprints equal, planned row counts", verify_backups)

        def test_restore() -> dict[str, Any]:
            t = run.writable(B / "restore-test.db")
            r = db_restore.restore(cold, t, ctx["live_start"]["sha256"])
            same, diff = p7a.same(p7a.fingerprint(t), ctx["F0"])
            t.unlink()
            return {"ok": r["ok"] and same, "restore": r, "fingerprints_match": same, "diff": diff, "test_file_removed": not t.exists()}

        run.step("Test restore of the cold copy with scripts/db_restore.py (SHA-256 + fingerprints)", test_restore)

        def make_target() -> dict[str, Any]:
            shutil.copyfile(cold, run.writable(target))
            f = p7a.file_facts(target)
            ctx["master_before"] = master_rows(target, BASELINE_TABLES)
            return {"ok": f["sha256"] == ctx["live_start"]["sha256"] and p7a.revision(target) is None, "rehearsal_target": f}

        run.step("Rehearsal target = copy of the cold backup (stands in for the live file)", make_target)

        def unchanged(p: Path) -> tuple[bool, list[str]]:
            return p7a.same(p7a.fingerprint(p), ctx["F0"])

        def stamp_() -> dict[str, Any]:
            before_rev = p7a.revision(target)
            p7a.alembic("stamp", run.writable(target), "0001_baseline")
            same, diff = unchanged(target)
            return {"ok": before_rev is None and p7a.revision(target) == "0001_baseline" and same,
                    "current_before": before_rev, "current_after": p7a.revision(target), "existing_tables_unchanged": same, "diff": diff}

        run.step("alembic current (none) -> stamp 0001_baseline -> current", stamp_)

        def upgrade() -> dict[str, Any]:
            p7a.alembic("upgrade", run.writable(target), "0002_history_tables")
            same, diff = unchanged(target)
            hc = counts(target, HISTORY_TABLES)
            ctx["size_after_upgrade"] = target.stat().st_size
            return {"ok": p7a.revision(target) == "0002_history_tables" and same and set(hc) == set(HISTORY_TABLES) and not any(hc.values()),
                    "current_after": p7a.revision(target), "history_tables": hc, "existing_tables_unchanged": same, "diff": diff,
                    "bytes_after_upgrade": ctx["size_after_upgrade"]}

        run.step("alembic upgrade 0002_history_tables -> 12 empty tables", upgrade)

        def dry() -> dict[str, Any]:
            r = bf.run_backfill(run.writable(target), dry_run=True)
            hc = counts(target, HISTORY_TABLES)
            same, diff = unchanged(target)
            ok = (r["inserted"] == {"ingestion_batches": 1, "record_observations": 6708, "record_versions": 6708}
                  and r["totals_in_transaction"]["newly_discovered"] == 0 and not any(hc.values()) and same)
            return {"ok": ok, "dry_run": r, "history_rows_after_rollback": sum(hc.values()), "existing_tables_unchanged": same}

        run.step("Backfill dry run (rolled back): 1 batch, 6,708 + 6,708 rows, 0 newly discovered", dry)

        def real() -> dict[str, Any]:
            t0 = time.perf_counter()
            r = bf.run_backfill(run.writable(target))
            ctx["backfill_seconds"] = round(time.perf_counter() - t0, 3)
            return {"ok": r["inserted"] == {"ingestion_batches": 1, "record_observations": 6708, "record_versions": 6708} and r["committed"],
                    "backfill": r, "seconds": ctx["backfill_seconds"]}

        run.step("Backfill (one transaction), labelled baseline_backfill", real)

        def again() -> dict[str, Any]:
            r = bf.run_backfill(run.writable(target))
            return {"ok": r["inserted"] == {"ingestion_batches": 0, "record_observations": 0, "record_versions": 0}, "rerun": r["inserted"]}

        run.step("Backfill re-run writes 0 rows (idempotent)", again)

        def existing() -> dict[str, Any]:
            same, diff = unchanged(target)
            master_same = master_rows(target, BASELINE_TABLES) == ctx["master_before"]
            orm = orm_differences(target)
            return {"ok": same and master_same and not orm, "fingerprints_unchanged": same, "diff": diff,
                    "sqlite_master_unchanged": master_same, "orm_vs_schema_differences": orm,
                    "fingerprints_after": p7a.fingerprint(target)}

        run.step("Existing 7 tables unchanged: schema, row counts, row checksums, ORM comparison", existing)

        def hist() -> dict[str, Any]:
            checks = history_checks(target)
            return {"ok": all(c["ok"] for c in checks), "passed": sum(c["ok"] for c in checks), "total": len(checks), "checks": checks}

        run.step("History verification: counts, cross-checks with records, foreign keys, integrity", hist)

        def probe() -> dict[str, Any]:
            pc = run.writable(W / "new_discovery_probe.db")
            shutil.copyfile(target, pc)
            r = new_discovery_probe(pc)
            pc.unlink()
            return r

        run.step("New-discovery rule: baseline record seen again = not new; unseen record = new (throw-away copy)", probe)

        def api() -> dict[str, Any]:
            before, after = run_api_smoke(cold), run_api_smoke(target)
            diffs = [p for p in API_PATHS if before.get(p) != after.get(p)]
            all200 = all(v["status"] == 200 for v in after.values())
            return {"ok": not diffs and all200, "endpoints": len(API_PATHS), "all_status_200": all200, "differing_endpoints": diffs,
                    "ignored_timestamp_keys": sorted(VOLATILE_KEYS),
                    "before_pre_migration_copy": before, "after_migrated_copy": after}

        run.step("Read-only API on the migrated copy returns the same responses as on the original", api)

        def rb_schema() -> dict[str, Any]:
            rb = run.writable(W / "rollback_schema.db")
            shutil.copyfile(target, rb)
            from alembic import command

            p7a.alembic("downgrade", rb, "0001_baseline")
            command.stamp(p7a.alembic_cfg(rb), "base", purge=True)
            same, diff = p7a.same(p7a.fingerprint(rb), ctx["F0"])
            left = sorted(p7a.tables(rb) & set(HISTORY_TABLES))
            with p7a.ro(rb) as c:
                av = c.execute("select count(*) from alembic_version").fetchone()[0]
            return {"ok": same and not left and av == 0, "existing_tables_match_original": same, "diff": diff,
                    "history_tables_left": left, "alembic_version_rows": av, "file": p7a.file_facts(rb)}

        run.step("Rollback rehearsal 1: downgrade 0001_baseline + stamp --purge base", rb_schema)

        def rb_full() -> dict[str, Any]:
            rb = run.writable(W / "rollback_full.db")
            shutil.copyfile(target, rb)
            r = db_restore.restore(cold, rb, ctx["live_start"]["sha256"])
            same, diff = p7a.same(p7a.fingerprint(rb), ctx["F0"])
            return {"ok": r["ok"] and same and r["sha256"] == ctx["live_start"]["sha256"], "restore": r,
                    "byte_identical_to_original_live": r["sha256"] == ctx["live_start"]["sha256"], "fingerprints_match": same}

        run.step("Rollback rehearsal 2: full restore of the cold copy over a migrated copy (byte-identical)", rb_full)

        def measure() -> dict[str, Any]:
            size = target.stat().st_size
            return {"live_bytes": ctx["live_start"]["bytes"], "after_upgrade_bytes": ctx["size_after_upgrade"], "after_backfill_bytes": size,
                    "growth_bytes": size - ctx["live_start"]["bytes"], "after_backfill_mib": round(size / 1048576, 2),
                    "backfill_seconds": ctx["backfill_seconds"], "rehearsal_target": p7a.file_facts(target)}

        ctx["measurements"] = run.step("Measurements: database size and backfill time", measure)

        def live_end() -> dict[str, Any]:
            end = p7a.file_facts(LIVE_DB)
            ctx["live_end"] = end
            return {"ok": end == ctx["live_start"] and p7a.revision(LIVE_DB) is None, "start": ctx["live_start"], "end": end,
                    "live_registered_with_alembic": p7a.revision(LIVE_DB) is not None}

        run.step("Live database unchanged (SHA-256, size, mtime) and still not registered with Alembic", live_end)
        ctx["result"] = "PASS"
    except Stop:
        ctx["result"] = "FAIL"
        ctx["on_failure"] = "Stopped at the first failed check. All files were kept as they were. Nothing was restored."
        if "live_start" in ctx:
            ctx["live_end"] = p7a.file_facts(LIVE_DB)
            ctx["live_unchanged"] = ctx["live_end"] == ctx["live_start"]
    return report(run, ctx)


def report(run: Runner, ctx: dict[str, Any]) -> int:
    rep = {"report": "phase7b_rehearsal (Run A)", **ctx, "passed": sum(s["ok"] for s in run.steps), "steps_run": len(run.steps), "steps": run.steps}
    out = ROOT / "docs"
    j = out / f"phase7b_rehearsal_{ctx['stamp']}.json"
    j.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    md = [f"# Phase 7B Run A (rehearsal on a copy) {ctx['stamp']}: {ctx['result']} ({rep['passed']}/{rep['steps_run']} steps)", "",
          f"Live database (read only): `{ctx['live']}`  ", f"Backups: `{ctx['backup_dir']}`  ", f"Work folder: `{ctx['workdir']}`", "",
          "| # | Step | Result | Seconds |", "| --- | --- | --- | --- |"]
    md += [f"| {s['step']} | {s['name']} | {'PASS' if s['ok'] else 'FAIL'} | {s['seconds']} |" for s in run.steps]
    for k in ("live_start", "live_end", "cold", "online"):
        if k in ctx:
            md.append(f"\n{k}: SHA-256 `{ctx[k]['sha256']}`, {ctx[k]['bytes']} bytes")
    if ctx.get("on_failure"):
        md += ["", f"**{ctx['on_failure']}**"]
    md += ["", f"Full details: `{j.name}`"]
    (out / f"phase7b_rehearsal_{ctx['stamp']}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nRESULT: {ctx['result']} ({rep['passed']}/{rep['steps_run']})\nReport: {j}", flush=True)
    return 0 if ctx["result"] == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 7B orchestrator (Run A only)")
    ap.add_argument("--target", choices=["copy", "live"])
    ap.add_argument("--workdir", default=r"C:\FrontierAtlas-test" if os.name == "nt" else str(Path.home() / "FrontierAtlas-test"))
    ap.add_argument("--backup-root", default=r"C:\FrontierAtlas-backups" if os.name == "nt" else str(Path.home() / "FrontierAtlas-backups"))
    ap.add_argument("--api-smoke", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.api_smoke:
        print(json.dumps(api_smoke(Path(a.api_smoke))))
        return 0
    if a.target == "live":
        print("Run B is started with RUN_PHASE7B_LIVE.cmd (scripts/phase7b_live.py), never from this script.")
        return 2
    if a.target != "copy":
        ap.error("--target copy is required")
    return run_copy(a)


if __name__ == "__main__":
    sys.exit(main())
