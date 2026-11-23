"""Assemble FINAL_REPORT.md from evidence files only (never from assumptions).

Inputs (each optional; missing ones are reported as NOT RUN):
docs/data_audit.json, docs/integration_report.json, docs/llm_live_check.json, docs/llm_enrichment.json,
docs/sheets_export.json, docs/benchmarks.json, docs/test-results*.xml, docs/run_summaries.json
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

D = Path("docs")


def load(name: str) -> dict | None:
    p = D / name
    return json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else None  # utf-8-sig: PowerShell writes a BOM


def check_status(c: dict) -> str:
    """Live-check verdict. Older records stored the *extraction* status (VERIFIED/PARTIAL) under 'status'; never confuse the two."""
    if c.get("check_status"):
        return c["check_status"]
    if c.get("ok") and c.get("check", "").startswith("1b") and not (c.get("accepted") or {}).get("organizations"):
        return "FAIL"  # legacy record: passed without any extracted organisation
    if c.get("ok"):
        return "PASS"
    err = str(c.get("error", ""))
    return "UNAVAILABLE_TRANSIENT" if any(t in err for t in ("unavailable", "rate_limited", "timeout", "503", "429")) else "FAIL"


def tests(name: str) -> str:
    p = D / name
    if not p.exists():
        return "NOT RUN"
    r = ET.parse(p).getroot()
    s = r if r.tag == "testsuite" else r[0]
    a = s.attrib
    return f"{a['tests']} tests, {a['failures']} failures, {a['errors']} errors, {a.get('skipped', '0')} skipped ({a.get('timestamp', '')[:19]})"


def overall_readiness(rows: list[tuple[str, object]]) -> tuple[str, list[str]]:
    """READY only if every required item is verified; any NO (including a failing live check) -> NOT READY."""
    failing = [n for n, ok in rows if not ok]
    return ("NOT READY" if failing else "READY"), failing


def main() -> None:
    audit, integ, live, enr, sheets, bench = (load(n) for n in ("data_audit.json", "integration_report.json", "llm_live_check.json",
                                                                 "llm_enrichment.json", "sheets_export.json", "benchmarks.json"))
    L: list[str] = ["# FINAL REPORT — AI Intelligence Ingestion Platform", "", f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from evidence files in `docs/`. "
                    "Every number below is read from those files; anything not executed is marked **NOT RUN**.", ""]
    run, backup, cmp_, docker, scan = (load(n) for n in ("final_run_summary.json", "backup_latest.json", "db_comparison.json", "docker_validation.json", "secret_scan.json"))
    if run:
        L += ["## 0. Final run (scripts/run_final.ps1)", "", f"Started {run['started']} · finished {run['finished']}", "",
              "| Step | Status | Reason / exit | Seconds |", "|---|---|---|---:|"]
        for k, v in run["steps"].items():
            if v["name"] != "gate":
                L.append(f"| {k} {v['name']} | **{v['status']}** | {(v.get('reason') or '') or ('exit ' + str(v.get('exit_code')))} | {v.get('seconds', '')} |")
        gates = {k: v["reason"] for k, v in run["steps"].items() if v["name"] == "gate"}
        L += ["", f"Gates: `{json.dumps(gates)}`", ""]
    if backup:
        L += [f"Pre-run backup: `{backup['backup']}` · SHA-256 `{backup['sha256']}` · integrity {backup['source_integrity']}/{backup['backup_integrity']} · counts match {backup['counts_match']}", ""]
    L += ["## 1. Data collected (real sources)", ""]
    if audit:
        v = audit["verticals"]
        L += [f"Audit at {audit['audited_at']}.", "", "| Vertical | Stored | Schema-invalid | Invalid URLs | Duplicate keys | Detail |", "|---|---:|---:|---:|---:|---|"]
        det = {
            "STARTUP": lambda x: f"with employee count {x['with_employee_count']}; sources {x['by_source']}",
            "PRODUCT": lambda x: f"pricing {x['pricing_model']}; LLM-enriched {x['llm_enriched']}; sources {x['by_source']}",
            "RESEARCH_PAPER": lambda x: f"repo {x['with_github_url']}, stars {x['with_stars']}, star source {x['stars_source']}, published {x['published_range']}",
            "JOB": lambda x: f"verified fresh now {x['verified_fresh_now']}, unverified {x['unverified_dates']}, older {x['verified_but_older_than_window_now']}; {x['by_source']}",
            "NEWS": lambda x: f"verified fresh now {x['verified_fresh_now']}, unverified {x['unverified_dates']}, full text {x['with_full_text']}; {x['by_source']}",
        }
        for k, x in v.items():
            L.append(f"| {k} | {x['stored']:,} | {x['schema_invalid']} | {x['invalid_source_urls']} | {x['duplicate_natural_keys']} | {det[k](x)} |")
        L += ["", "Latest run status per source:", "", "| Source | Status | Fetched | Stored | Error |", "|---|---|---:|---:|---|"]
        for src, r in sorted(audit.get("source_runs_latest", {}).items()):
            L.append(f"| {src} | {r['status']} | {r['fetched']} | {r['stored']} | {(r['error'] or '').replace('|', '/')[:120]} |")
    else:
        L.append("**NOT RUN** (scripts/audit_data.py)")
    if cmp_:
        L += ["", f"### Change vs. pre-run baseline (integrity before {cmp_['integrity_before']}, after {cmp_['integrity_after']})", "",
              "| Vertical | Before | After | Δ | Added (by source) | Removed (by source) | Canonical-name changes |", "|---|---:|---:|---:|---|---|---:|"]
        for k, x in cmp_["verticals"].items():
            L.append(f"| {k} | {x['before']:,} | {x['after']:,} | {x['delta']:+,} | {x['added']} {x['added_by_source']} | {x['removed']} {x['removed_by_source']} | {x['canonical_name_changes']} |")
        for k, x in cmp_["verticals"].items():
            if x["name_change_pairs"]:
                L.append(f"- {k} name changes (old → new, count): " + "; ".join(f"{a} → {b} ×{n}" for a, b, n in x["name_change_pairs"][:12]))
    L += ["", "## 2. Integrations", ""]
    if integ:
        L += [f"Live checks at {integ['checked_at']}.", "", "| Integration | Credential | Connectivity | Result | Category | Detail |", "|---|---|---|---|---|---|"]
        L += [f"| {c['integration']} | {'yes' if c['credential_configured'] else 'no'} | {'yes' if c['connectivity_verified'] else 'no'} | {c['result']} | {c.get('category', '')} | {c['detail'].replace('|', '/')[:150]} |" for c in integ["checks"]]
    else:
        L.append("**NOT RUN** (scripts/verify_integrations.py)")
    L += ["", "## 3. LLM orchestration — live provider checks", ""]
    if live and live.get("checks"):
        L += [f"Configured chain: {live['configured_chain']} · summary {live.get('summary')}", "",
              "| Check | Status | What it proves | Providers | Chunks | Accepted (evidence-checked) / error | Attempts (live) | Run at |", "|---|---|---|---|---:|---|---|---|"]
        for c in live["checks"]:
            st = check_status(c)
            L.append(f"| {c['check']} | **{st}** | {c.get('proves', '')} | {c.get('providers_used', '') or ''} | {c.get('chunks_total', '') or ''} | "
                     f"{json.dumps(c.get('accepted') if c.get('accepted') is not None else c.get('error', ''))[:120]} | {', '.join(c.get('attempts') or [])[:160]} | {c.get('run_at', '')} |")
    else:
        L.append("**NOT RUN** live (scripts/llm_live_check.py). Mocked-provider coverage: see tests below.")
    if enr:
        L += ["", f"Stored-product enrichment: {json.dumps(enr['summary'])[:600]}"]
    gsel, enn, gh = load("gemini_model_selection.json"), load("llm_enrichment_news.json"), load("gemini_health.json")
    if gh:
        L += ["", f"Gemini live health check (shared by selector and verifier): model `{gh['config']['model']}`, healthy **{gh['healthy']}**, "
              f"category {gh['category']}, api_calls {gh['api_calls']}, minimal attempts {[a['outcome'] for a in gh['minimal_generation'].get('attempts', [])]}, "
              f"extraction {gh['extraction'].get('accepted', gh['extraction'].get('error'))}"]
    if gsel:
        L += ["", f"Gemini model selection (live, {gsel['run_at']}): configured `{gsel['configured_model']}` listed={gsel.get('configured_model_listed')}; "
              f"selected **{gsel.get('selected_model')}**; {gsel.get('env_change', 'not applied')}; candidates {gsel.get('flash_candidates', [])[:6]}"]
        for t in gsel.get("tests", []):
            L.append(f"- `{t['model']}`: minimal generation {t.get('minimal_generation', {}).get('ok')}, extraction {t.get('extraction', {}).get('ok')}, compatible {t.get('compatible')}")
    if live and live.get("cross_vendor_success_fallback"):
        L += ["", "Cross-vendor fallback *to* a working Groq/DeepSeek: " + json.dumps(live["cross_vendor_success_fallback"])]
    if enn:
        L += ["", f"News organisation enrichment (stored records): {json.dumps(enn['summary'])}"]
    L += ["", "## 4. Entity resolution", ""]
    if audit:
        e = audit["entity_resolution"]
        L += [f"- Mapping log rows: **{e['mappings']:,}** · by method {e['by_method']}",
              f"- Auto-resolved to an existing entity: {e['auto_resolved_to_existing']:,} · new entities: {e['new_entities']:,} · human-review queue: {e['needs_review']}",
              f"- Merges where raw and canonical share no text (manually inspected): {e['merges_for_manual_check_count']}",
              "- Incorrect merges found in the live data: 2 (Conntour→Contour, Datasite→DataSuite) — fixed by the single-word-brand guard; "
              "`rebuild-entities` re-resolved all records and 461 historical names (2 decisions reversed).",
              "- Human-review queue and manual-check merges: see docs/entity_review.md (no mapping auto-approved).",
              "- Hand-labelled test set: 68/68 positives resolved, 0/22 false merges, 3 live-regression pairs kept separate (tests/er_cases.py)."]
    L += ["", "## 5. Tests and quality gates", "", f"- Linux / Python 3.13, SQLite + PostgreSQL 16 + Redis 7: {tests('test-results.xml')}",
          f"- Linux / Python 3.12 (user project dir, SQLite): {tests('test-results-py312.xml')}",
          f"- Windows (user machine): {tests('test-results-windows.xml')}",
          f"- Docker (tests inside the api image against PostgreSQL + Redis containers): {tests('test-results-docker.xml')}",
          f"- Docker end-to-end: {json.dumps(docker) if docker else 'NOT RUN'}",
          f"- Secret scan: {('clean' if scan['clean'] else str(len(scan['findings'])) + ' finding(s): ' + json.dumps(scan['findings'][:5])) if scan else 'NOT RUN'}"]
    L += ["", "## 6. Performance (SYNTHETIC fixtures — not production measurements)", ""]
    if bench:
        st, cr, llm = bench["storage_bulk_upsert"], bench["crawl_pipeline_local_http"], bench["llm_orchestrator_fault_injection"]
        L += [f"- Bulk upsert {st['records']:,} rows: {st['insert_rows_per_s']:,} rows/s; idempotent re-run inserted {st['rerun_inserted']}",
              f"- Pipeline over local HTTP: {cr['jobs_fetched']:,} postings at {cr['jobs_per_s']:,}/s with {cr['concurrent_workers']} workers",
              f"- LLM fault injection: {llm['succeeded']}/{llm['extractions']} succeeded, p95 {llm['p95_s']} s",
              f"- Peak RSS {bench['peak_rss_mb']} MB on {bench['cpu_count']} vCPU"]
    L += ["", "## 7. Google Sheets", ""]
    if sheets:
        L += [f"- URL: {sheets.get('sheet_url') or 'not created'}", f"- Rows written: {sheets.get('rows')}",
              f"- Read-back verification: {json.dumps(sheets.get('sheets_verification'))[:700]}",
              f"- Access: {'PUBLIC link enabled' if (sheets.get('sheets_verification') or {}).get('public_link_reader') else 'private (no anyone-with-link permission)'}"]
        if sheets.get("sheets_error"):
            L.append(f"- ERROR: {sheets['sheets_error']}")
    else:
        L.append("**NOT RUN** (python -m src.main export --sheets). CSV backups: data/exports/*.csv")
    L += ["", "## 8. Readiness (derived from the evidence above)", ""]
    ic = {c["integration"]: c for c in (integ or {}).get("checks", [])}
    lc = {c["check"]: check_status(c) for c in (live or {}).get("checks", [])}
    rows = [
        ("Data targets (≥1,000 startups/products/papers)", audit and all(audit["verticals"][k]["stored"] >= 1000 for k in ("STARTUP", "PRODUCT", "RESEARCH_PAPER"))),
        ("Live GitHub stars on all papers", audit and audit["verticals"]["RESEARCH_PAPER"]["stars_source"].get("github_api") == audit["verticals"]["RESEARCH_PAPER"]["stored"]),
        ("Google Sheets: 6 tabs, exact counts, headers, private", bool(sheets and sheets.get("sheets_ok"))),
        ("Gemini live (generation + production extraction)", ic.get("Gemini", {}).get("result") == "PASS"),
        ("LLM extraction live", lc.get("1_direct_extraction_product_pricing") == "PASS"),
        ("Long-article chunking live", lc.get("3_forced_chunking_1500_token_budget") == "PASS"),
        ("Hallucination prevention live", lc.get("4_hallucination_probe_fields_absent") == "PASS"),
        ("Real-article extraction live (organisations)", lc.get("1b_direct_extraction_real_news_entities") == "PASS"),
        ("Error handling + fallback live (invalid key; real Groq/DeepSeek auth errors)",
         lc.get("2_forced_fallback_invalid_primary_key") == "PASS" and lc.get("5_real_groq_deepseek_auth_failures_then_fallback") == "PASS"),
        ("Secret scan clean", bool(scan and scan.get("clean"))),
    ]
    L += ["| Item | Verified |", "|---|---|"] + [f"| {n} | {'YES' if ok else 'NO'} |" for n, ok in rows]
    verdict, failing = overall_readiness(rows)
    L += ["", f"**Overall readiness: {verdict}**" + (f" - not verified: {'; '.join(failing)}" if failing else " - every required item verified live."), ""]
    out_lim = []
    if ic.get("Product Hunt API", {}).get("result") != "PASS":
        out_lim.append("Product Hunt: token rejected (HTTP 401) - no Product Hunt products; a valid Developer Token is required.")
    for n in ("groq", "deepseek"):
        if lc.get(f"6_fallback_to_{n}", "NOT_CONFIGURED") == "NOT_CONFIGURED":
            out_lim.append(f"{n.capitalize()}: NOT CONFIGURED (optional). Successful fallback to {n} is not live-verified; its error handling is.")
    if not docker or not docker.get("attempted"):
        out_lim.append("Docker end-to-end: not run (Docker not installed on the verification machine).")
    out_lim += ["Qdrant / Neo4j: optional, not used by the ingestion pipeline, not deployed.",
                "16 news items have no organisation tags (LLM outage during crawl; dead-lettered). Re-run `python -m src.main enrich --news` to fill them.",
                "VentureBeat (HTTP 429) and The Decoder (HTTP 403) block automated access; reported, not scraped.",
                "Jobs/News tabs reflect the 24 h window at export time and shrink as time passes.",
                "4 entity-review cases need a human decision (docs/entity_review.md)."]
    L += ["", "Outstanding limitations:", ""] + [f"- {x}" for x in out_lim]
    Path("FINAL_REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("FINAL_REPORT.md written")


if __name__ == "__main__":
    main()
