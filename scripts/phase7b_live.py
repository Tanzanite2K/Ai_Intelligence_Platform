"""Phase 7B Run B: the live-database execution path. Started only by RUN_PHASE7B_LIVE.cmd after explicit approval.

Order (stops at the first failed check):
  1. Approval gate: --confirm RUN-B, a passing Run A report whose backfill code version equals this one.
  2. No writer: ports 8000/3000 closed, no -journal/-wal/-shm file, an exclusive-lock probe succeeds (and changes nothing).
  3. Identity: the exact path, the expected SHA-256, no Alembic registration, exactly the 7 baseline tables, and the same
     7-table fingerprints (schema, row counts, row checksums) that Run A verified.
  4. Backups: byte-for-byte cold copy + SQLite online backup, each stored outside OneDrive and inside data\\.
  5. Backup verification: hashes, integrity, fingerprints, and a test restore.
  -- first write to the live database --
  6. alembic stamp 0001_baseline  ->  7. alembic upgrade 0002_history_tables
  8. backfill dry run (rolled back)  ->  9. backfill (one transaction, baseline_backfill)
 10. existing 7 tables unchanged   11. history checks (counts, cross-checks, 0 newly discovered, foreign keys, integrity)
 12. read-only API answers the same as on the pre-migration backup.

On failure it stops, writes the report, keeps the database exactly as the failure left it, restores nothing, and prints
the restore command for you to approve. Exit codes: 0 pass, 1 failed before any write (live untouched), 3 failed after
a write (state preserved, ask before restoring), 2 refused (gate not met).

The only operations that write to the live file are listed in LIVE_WRITE_OPERATIONS and all pass through LiveWriter.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import db_restore  # noqa: E402
import phase7a_migration_test as p7a  # noqa: E402
import phase7b_backfill as bf  # noqa: E402
import phase7b_execute as ex  # noqa: E402

LIVE_DB = (ROOT / "data" / "platform.db").resolve()
CONFIRM_WORD = "RUN-B"
EXPECTED_LIVE_SHA256 = "67e9335b6b257682357a60476b7d27690dcacff730d7bc12544c80b3801ac16d"

LIVE_WRITE_OPERATIONS = (
    "lock probe: BEGIN EXCLUSIVE; ROLLBACK  (takes the write lock for a moment, writes no data; hash re-checked after)",
    "alembic -x db_url=<live> -x allow_live=yes stamp 0001_baseline  (creates alembic_version, 1 row)",
    "alembic -x db_url=<live> -x allow_live=yes upgrade 0002_history_tables  (creates 12 empty tables + 26 indexes)",
    "phase7b_backfill --db <live> --allow-live --dry-run  (inserts inside a transaction, then ROLLBACK)",
    "phase7b_backfill --db <live> --allow-live  (1 batch + 6,708 observations + 6,708 versions, one COMMIT)",
)


class Stop(Exception):
    pass


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class LiveWriter:
    """The single place where the target database is written. Every call is printed and recorded."""

    def __init__(self, target: Path, *, is_live: bool, log: list[dict[str, Any]]) -> None:
        self.target, self.is_live, self.log = target, is_live, log
        self.first_write_done = False

    def _record(self, op: str) -> None:
        self.first_write_done = True
        entry = {"at": datetime.now(UTC).isoformat(), "operation": op, "target": str(self.target)}
        self.log.append(entry)
        print(f"      WRITE -> {op}", flush=True)

    def _cfg(self):  # type: ignore[no-untyped-def]
        return p7a.alembic_cfg(self.target, "allow_live=yes") if self.is_live else p7a.alembic_cfg(self.target)

    def stamp_baseline(self) -> None:
        from alembic import command

        self._record("alembic stamp 0001_baseline")
        command.stamp(self._cfg(), "0001_baseline")

    def upgrade_history(self) -> None:
        from alembic import command

        self._record("alembic upgrade 0002_history_tables")
        command.upgrade(self._cfg(), "0002_history_tables")

    def backfill(self, *, dry_run: bool) -> dict[str, Any]:
        self._record(f"baseline backfill{' (dry run, rolled back)' if dry_run else ' (commit)'}")
        return bf.run_backfill(self.target, dry_run=dry_run, allow_live=self.is_live)


def lock_probe(target: Path) -> dict[str, Any]:
    """BEGIN EXCLUSIVE succeeds only if no other connection holds or wants a lock. Writes no data."""
    before = p7a.sha256_file(target)
    con = sqlite3.connect(target, timeout=0.5, isolation_level=None)
    try:
        con.execute("BEGIN EXCLUSIVE")
        con.execute("ROLLBACK")
        acquired = True
    except sqlite3.OperationalError as e:
        acquired, err = False, str(e)
    finally:
        con.close()
    after = p7a.sha256_file(target)
    side = [s for s in ("-journal", "-wal", "-shm") if target.with_name(target.name + s).exists()]
    return {"acquired": acquired, "error": None if acquired else err, "file_unchanged_by_probe": before == after, "side_files_after": side}


def run_live(*, target: Path, is_live: bool, backup_root: Path, docs_dir: Path, expect_sha256: str,
             rehearsal_report: Path, check_ports: bool = True) -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target.resolve()
    data_dir = target.parent
    B = (backup_root / f"phase7b-live-{stamp}").resolve()
    cold_ext, online_ext = B / "platform.pre7b.cold.db", B / "platform.pre7b.online.db"
    cold_in, online_in = data_dir / f"platform.pre7b-{stamp}.cold.db", data_dir / f"platform.backup-{stamp}.db"
    writes: list[dict[str, Any]] = []
    writer = LiveWriter(target, is_live=is_live, log=writes)
    run = ex.Runner([B, cold_in, online_in])
    ctx: dict[str, Any] = {"stamp": stamp, "mode": "LIVE (Run B)" if is_live else "simulation on a copy", "target": str(target),
                           "backup_dir": str(B), "in_folder_backups": [str(cold_in), str(online_in)], "live_writes": writes,
                           "live_write_operations_allowed": list(LIVE_WRITE_OPERATIONS)}
    print(f"Phase 7B {ctx['mode']} {stamp}\n  target  : {target}\n  backups : {B}\n          + {cold_in.name}, {online_in.name} in {data_dir}\n", flush=True)
    try:
        def gate() -> dict[str, Any]:
            rep = json.loads(rehearsal_report.read_text(encoding="utf-8"))
            steps = {s["step"]: s for s in rep.get("steps", [])}
            code_ok = steps.get(1, {}).get("details", {}).get("backfill_code_version") == bf.code_version()
            ctx["rehearsal_fingerprints"] = steps.get(3, {}).get("details", {}).get("fingerprints")
            ok = rep.get("result") == "PASS" and code_ok and bool(ctx["rehearsal_fingerprints"])
            return {"ok": ok, "rehearsal_report": str(rehearsal_report), "rehearsal_result": rep.get("result"),
                    "rehearsal_steps": f"{rep.get('passed')}/{rep.get('steps_run')}", "backfill_code_version": bf.code_version(),
                    "backfill_code_version_matches_rehearsal": code_ok}

        run.step("Gate: Run A report PASS and the same backfill code version", gate)

        def no_writer() -> dict[str, Any]:
            ports = {p: port_open(p) for p in (8000, 3000)} if check_ports else {"skipped": True}
            side = [s for s in ("-journal", "-wal", "-shm") if target.with_name(target.name + s).exists()]
            probe = lock_probe(target)
            ok = (not check_ports or not any(ports.values())) and not side and probe["acquired"] and probe["file_unchanged_by_probe"] and not probe["side_files_after"]
            return {"ok": ok, "ports_in_use": ports, "side_files": side, "exclusive_lock_probe": probe}

        run.step("No other writer: API/web ports closed, no journal files, exclusive-lock probe", no_writer)

        def identity() -> dict[str, Any]:
            facts = p7a.file_facts(target)
            ctx["start"] = facts
            tbl = p7a.tables(target)
            fp = p7a.fingerprint(target)
            ctx["F0"] = fp
            rev = p7a.revision(target)
            fp_match = fp == ctx["rehearsal_fingerprints"]
            ok = (facts["sha256"] == expect_sha256 and rev is None and tbl == set(ex.BASELINE_TABLES) and fp_match
                  and (not is_live or target == LIVE_DB))
            with p7a.ro(target) as c:
                ctx["by_type"] = dict(c.execute("select record_type, count(*) from records group by record_type").fetchall())
            return {"ok": ok, "path": str(target), "is_project_live_path": target == LIVE_DB, "sha256": facts["sha256"],
                    "expected_sha256": expect_sha256, "sha256_matches": facts["sha256"] == expect_sha256, "alembic_revision": rev,
                    "tables": sorted(tbl), "fingerprints_match_run_a": fp_match, "rows": {t: v["rows"] for t, v in fp.items()},
                    "records_by_type": ctx["by_type"], "integrity": p7a.integrity(target)}

        run.step("Identity: path, SHA-256, not registered, 7 tables, fingerprints equal to Run A", identity)

        def backups() -> dict[str, Any]:
            run.writable(B).mkdir(parents=True)
            shutil.copyfile(target, run.writable(cold_ext))
            shutil.copyfile(target, run.writable(cold_in))
            p7a.online_backup(target, run.writable(online_ext))
            shutil.copyfile(online_ext, run.writable(online_in))
            facts = {k: p7a.file_facts(p) for k, p in (("cold_external", cold_ext), ("cold_in_data", cold_in), ("online_external", online_ext), ("online_in_data", online_in))}
            ctx["backups"] = facts
            now = p7a.file_facts(target)
            ok = (now == ctx["start"] and facts["cold_external"]["sha256"] == facts["cold_in_data"]["sha256"] == expect_sha256
                  and facts["online_external"]["sha256"] == facts["online_in_data"]["sha256"])
            return {"ok": ok, "target_unchanged_during_backup": now == ctx["start"], **facts}

        run.step("Backups: cold byte-for-byte + online, outside OneDrive and in data\\", backups)

        def verify_backups() -> dict[str, Any]:
            files = {"cold_external": cold_ext, "cold_in_data": cold_in, "online_external": online_ext, "online_in_data": online_in}
            integ = {k: p7a.integrity(p) for k, p in files.items()}
            fps = {k: p7a.fingerprint(p) == ctx["F0"] for k, p in files.items()}
            t = run.writable(B / "restore-test.db")
            r = db_restore.restore(cold_ext, t, expect_sha256)
            restored_fp = p7a.fingerprint(t) == ctx["F0"]
            t.unlink()
            (run.writable(B / "fingerprints.pre7b.json")).write_text(json.dumps(ctx["F0"], indent=2), encoding="utf-8")
            (run.writable(B / "SHA256SUMS.txt")).write_text(
                "".join(f"{ctx['backups'][k]['sha256']}  {Path(ctx['backups'][k]['path']).name}  ({k})\n" for k in ctx["backups"])
                + f"{expect_sha256}  platform.db  (live, before Phase 7B)\n", encoding="utf-8")
            ok = all(v == "ok" for v in integ.values()) and all(fps.values()) and r["ok"] and restored_fp
            return {"ok": ok, "integrity": integ, "fingerprints_equal_live": fps, "test_restore": r, "test_restore_fingerprints_match": restored_fp}

        run.step("Backup verification: integrity, fingerprints, test restore (SHA-256)", verify_backups)

        master_before = ex.master_rows(target, ex.BASELINE_TABLES)
        before = p7a.file_facts(target)
        if before != ctx["start"]:
            raise Stop("target changed before the first write")
        print("\n  --- first write to the target database starts now ---", flush=True)

        def stamp_() -> dict[str, Any]:
            writer.stamp_baseline()
            same, diff = p7a.same(p7a.fingerprint(target), ctx["F0"])
            return {"ok": p7a.revision(target) == "0001_baseline" and same, "current": p7a.revision(target), "existing_unchanged": same, "diff": diff}

        run.step("alembic stamp 0001_baseline", stamp_)

        def upgrade() -> dict[str, Any]:
            writer.upgrade_history()
            same, diff = p7a.same(p7a.fingerprint(target), ctx["F0"])
            hc = ex.counts(target, ex.HISTORY_TABLES)
            return {"ok": p7a.revision(target) == "0002_history_tables" and same and set(hc) == set(ex.HISTORY_TABLES) and not any(hc.values()),
                    "current": p7a.revision(target), "history_tables": hc, "existing_unchanged": same, "diff": diff}

        run.step("alembic upgrade 0002_history_tables (12 empty tables)", upgrade)
        total = ctx["F0"]["records"]["rows"]
        want = {"ingestion_batches": 1, "record_observations": total, "record_versions": total}

        def dry() -> dict[str, Any]:
            r = writer.backfill(dry_run=True)
            hc = ex.counts(target, ex.HISTORY_TABLES)
            return {"ok": r["inserted"] == want and r["totals_in_transaction"]["newly_discovered"] == 0 and not any(hc.values()),
                    "dry_run": r, "history_rows_after_rollback": sum(hc.values())}

        run.step("Backfill dry run (rolled back)", dry)

        def real() -> dict[str, Any]:
            t0 = time.perf_counter()
            r = writer.backfill(dry_run=False)
            return {"ok": r["inserted"] == want and r["committed"], "backfill": r, "seconds": round(time.perf_counter() - t0, 3)}

        run.step(f"Backfill: {total:,} records labelled baseline_backfill (one transaction)", real)

        def existing() -> dict[str, Any]:
            same, diff = p7a.same(p7a.fingerprint(target), ctx["F0"])
            master_same = ex.master_rows(target, ex.BASELINE_TABLES) == master_before
            orm = ex.orm_differences(target)
            return {"ok": same and master_same and not orm, "fingerprints_unchanged": same, "diff": diff,
                    "sqlite_master_unchanged": master_same, "orm_vs_schema_differences": orm, "fingerprints_after": p7a.fingerprint(target)}

        run.step("Existing 7 tables: same schemas, row counts and row checksums", existing)

        def hist() -> dict[str, Any]:
            checks = ex.history_checks(target, total, ctx["by_type"])
            return {"ok": all(c["ok"] for c in checks), "passed": sum(c["ok"] for c in checks), "total": len(checks), "checks": checks}

        run.step("History tables, 0 newly discovered, foreign keys, integrity", hist)

        def api() -> dict[str, Any]:
            before_, after_ = ex.run_api_smoke(cold_ext), ex.run_api_smoke(target)
            diffs = [p for p in ex.API_PATHS if before_.get(p) != after_.get(p)]
            all200 = all(v["status"] == 200 for v in after_.values())
            return {"ok": not diffs and all200, "endpoints": len(ex.API_PATHS), "all_status_200": all200, "differing_endpoints": diffs,
                    "ignored_timestamp_keys": sorted(ex.VOLATILE_KEYS)}

        run.step("Read-only API on the migrated database answers as before (before restart)", api)

        def final() -> dict[str, Any]:
            end = p7a.file_facts(target)
            ctx["end"] = end
            return {"ok": p7a.integrity(target) == "ok", "final": end, "integrity": p7a.integrity(target),
                    "bytes_before": ctx["start"]["bytes"], "bytes_after": end["bytes"]}

        run.step("Final state recorded", final)
        ctx["result"] = "PASS"
        code = 0
    except Exception as e:  # ex.Stop from a failed step, or anything unexpected between steps
        ctx["result"] = "FAIL"
        ctx["failed_at"] = f"{type(e).__name__}: {e}"
        ctx["after_failure"] = p7a.file_facts(target) if target.exists() else None
        if writer.first_write_done:
            ctx["state"] = "FAILED AFTER A WRITE: database preserved exactly as the failure left it. Nothing was restored."
            ctx["restore_command_requires_your_approval"] = (
                f'.venv\\Scripts\\python.exe scripts\\db_restore.py --from "{cold_ext}" --to "{target}" '
                f"--expect-sha256 {expect_sha256}" + (" --allow-live" if is_live else ""))
            code = 3
        else:
            ctx["state"] = "FAILED BEFORE ANY WRITE: the target database was not modified."
            code = 1
    write_report(run, ctx, docs_dir, is_live)
    return code


def write_report(run: Any, ctx: dict[str, Any], docs_dir: Path, is_live: bool) -> None:
    name = f"phase7b_{'live' if is_live else 'live_simulation'}_{ctx['stamp']}"
    rep = {"report": name, **ctx, "passed": sum(s["ok"] for s in run.steps), "steps_run": len(run.steps), "steps": run.steps}
    docs_dir.mkdir(parents=True, exist_ok=True)
    j = docs_dir / f"{name}.json"
    j.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    md = [f"# Phase 7B {ctx['mode']} {ctx['stamp']}: {ctx.get('result')} ({rep['passed']}/{rep['steps_run']} steps)", "",
          f"Target: `{ctx['target']}`  ", f"Backups: `{ctx['backup_dir']}` and in data\\", "", "| # | Step | Result | Seconds |", "| --- | --- | --- | --- |"]
    md += [f"| {s['step']} | {s['name']} | {'PASS' if s['ok'] else 'FAIL'} | {s['seconds']} |" for s in run.steps]
    md += ["", "Writes to the target:"] + [f"- {w['at']} {w['operation']}" for w in ctx["live_writes"]]
    if ctx.get("state"):
        md += ["", f"**{ctx['state']}**"]
    if ctx.get("restore_command_requires_your_approval"):
        md += ["", "Restore command (run only after approval):", "", "```", ctx["restore_command_requires_your_approval"], "```"]
    (docs_dir / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nRESULT: {ctx.get('result')} ({rep['passed']}/{rep['steps_run']})  {ctx.get('state', '')}\nReport: {j}", flush=True)
    if ctx.get("restore_command_requires_your_approval"):
        print("\nRestore command (DO NOT run without approval):\n  " + ctx["restore_command_requires_your_approval"], flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 7B Run B (live). Use RUN_PHASE7B_LIVE.cmd.")
    ap.add_argument("--confirm", help=f"must be {CONFIRM_WORD}")
    ap.add_argument("--expect-sha256", default=EXPECTED_LIVE_SHA256)
    ap.add_argument("--rehearsal-report", required=True)
    ap.add_argument("--backup-root", default=r"C:\FrontierAtlas-backups" if os.name == "nt" else str(Path.home() / "FrontierAtlas-backups"))
    ap.add_argument("--simulate-on", help="dress rehearsal: run the live sequence on a copy of the live file in this folder")
    a = ap.parse_args()
    if a.simulate_on:
        sim_data = Path(a.simulate_on).resolve() / f"phase7b-live-simulation-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}" / "data"
        if ROOT in sim_data.parents or "onedrive" in str(sim_data).lower():
            print("simulation folder must be outside the project and OneDrive")
            return 2
        sim_data.mkdir(parents=True)
        target = sim_data / "platform.db"
        shutil.copyfile(LIVE_DB, target)  # the live file is only read
        return run_live(target=target, is_live=False, backup_root=sim_data.parent / "backups", docs_dir=ROOT / "docs",
                        expect_sha256=a.expect_sha256, rehearsal_report=Path(a.rehearsal_report), check_ports=False)
    if a.confirm != CONFIRM_WORD:
        print(f"Refused: Run B writes to the live database and needs --confirm {CONFIRM_WORD} (given by RUN_PHASE7B_LIVE.cmd after you type it).")
        return 2
    return run_live(target=LIVE_DB, is_live=True, backup_root=Path(a.backup_root), docs_dir=ROOT / "docs",
                    expect_sha256=a.expect_sha256, rehearsal_report=Path(a.rehearsal_report))


if __name__ == "__main__":
    sys.exit(main())
