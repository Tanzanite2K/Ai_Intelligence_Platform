"""Google Sheets exporter (Sheets API v4 over aiohttp, service-account auth).

* Auth: service-account JSON (``GOOGLE_SERVICE_ACCOUNT_FILE``) -> OAuth2 access
  token via ``google-auth``. No user credentials are ever handled.
* Creates the spreadsheet (or reuses ``GOOGLE_SHEET_ID``) with exactly six tabs,
  clears each tab, then writes header + rows in batches of
  ``SHEETS_BATCH_ROWS`` via ``values:batchUpdate`` (USER_ENTERED is *not*
  used — RAW keeps URLs/ISO timestamps verbatim).
* Quota handling: 429/500/503 -> exponential backoff + jitter honouring
  ``Retry-After`` (Sheets default quota is 60 write requests/min/user).
* ``share_public=True`` adds an "anyone with the link: reader" permission via
  the Drive API — an explicit opt-in because it publishes the data.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from src.core.retry import backoff_delay, parse_retry_after
from src.exports.tabs import Tab
from src.monitoring.logging import get_logger

log = get_logger(__name__)

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
# full drive scope is needed to share a spreadsheet the service account did not create (user-owned sheet shared with it)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


class SheetsError(RuntimeError):
    pass


def load_token(service_account_file: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
    creds.refresh(Request())
    return str(creds.token)


MAX_CELLS = 10_000_000


def required_grid(tab: Tab) -> tuple[int, int]:
    """(rows, columns) needed: header + data rows; at least the widest row."""
    cols = max([len(tab.columns), *(len(r) for r in tab.rows)]) if tab.rows else len(tab.columns)
    return len(tab.rows) + 1, max(cols, 1)


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, bool | int | float):
        return v
    s = str(v)
    return s[:49_000]  # Sheets hard limit is 50,000 chars per cell


class GoogleSheetsExporter:
    def __init__(self, token: str, *, batch_rows: int = 2000, max_retries: int = 6, session: aiohttp.ClientSession | None = None, sleep: Any = asyncio.sleep) -> None:
        self.token = token
        self.batch_rows = batch_rows
        self.max_retries = max_retries
        self._session = session
        self._sleep = sleep
        self.requests_made = 0
        self.last_spreadsheet_id: str | None = None
        self.grid_changes: dict[str, dict[str, int]] = {}

    async def _req(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        assert self._session is not None
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(self.max_retries + 1):
            self.requests_made += 1
            async with self._session.request(method, url, headers=headers, **kw) as r:
                body = await r.text()
                if r.status < 400:
                    return await r.json(content_type=None) if body else {}
                if r.status in (429, 500, 502, 503) and attempt < self.max_retries:
                    delay = parse_retry_after(r.headers.get("Retry-After")) or backoff_delay(attempt, 2.0, 64.0)
                    log.warning("sheets.retry", status=r.status, delay=round(delay, 1))
                    await self._sleep(delay)
                    continue
                hint = ""
                if "storageQuotaExceeded" in body or "storage quota" in body.lower():
                    hint = (" | The service account has no Drive storage: create an empty sheet in your own Drive, share it with the"
                            " service-account e-mail as Editor and set GOOGLE_SHEET_ID")
                elif "SERVICE_DISABLED" in body or "has not been used" in body:
                    hint = " | Enable the Google Sheets/Drive API for the service account's project"
                elif r.status in (403, 404) and "/spreadsheets/" in url:
                    hint = " | Share the spreadsheet with the service-account e-mail as Editor"
                raise SheetsError(f"{method} {url.split('?')[0]} -> {r.status}: {body[:300]}{hint}")
        raise SheetsError("retries exhausted")

    async def ensure_grid(self, spreadsheet_id: str, need: dict[str, tuple[int, int]]) -> dict[str, dict[str, int]]:
        """Grow each tab's grid (never shrink) so the data fits; a fresh sheet defaults to 1,000x26 and the API
        rejects writes beyond the grid ("exceeds grid limits")."""
        meta = await self._req("GET", f"{SHEETS}/{spreadsheet_id}", params={"fields": "sheets.properties(sheetId,title,gridProperties)"})
        reqs, changes = [], {}
        for sh in meta.get("sheets", []):
            pr = sh["properties"]
            if pr["title"] not in need:
                continue
            rows, cols = need[pr["title"]]
            gp = pr.get("gridProperties") or {}
            cur_r, cur_c = int(gp.get("rowCount", 0)), int(gp.get("columnCount", 0))
            if cur_r < rows or cur_c < cols:
                new_r, new_c = max(cur_r, rows), max(cur_c, cols)
                reqs.append({"updateSheetProperties": {"properties": {"sheetId": pr["sheetId"], "gridProperties": {"rowCount": new_r, "columnCount": new_c}},
                                                       "fields": "gridProperties.rowCount,gridProperties.columnCount"}})
                changes[pr["title"]] = {"rows_before": cur_r, "rows_after": new_r, "cols_before": cur_c, "cols_after": new_c}
        if reqs:
            await self._req("POST", f"{SHEETS}/{spreadsheet_id}:batchUpdate", json={"requests": reqs})
        self.grid_changes = changes
        return changes

    async def verify(self, spreadsheet_id: str, tabs: list[Tab]) -> dict[str, Any]:
        """Read back what is actually in the spreadsheet: tab titles, data rows per tab, header row, sharing."""
        own = self._session is None
        if own:
            self._session = aiohttp.ClientSession(trust_env=True)
        try:
            meta = await self._req("GET", f"{SHEETS}/{spreadsheet_id}", params={"fields": "sheets.properties.title"})
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            ranges = [f"'{t}'!A:A" for t in titles]
            vals = await self._req("GET", f"{SHEETS}/{spreadsheet_id}/values:batchGet", params=[("ranges", r) for r in ranges] + [("majorDimension", "COLUMNS")])
            rows = {t: max(0, len((vr.get("values") or [[]])[0]) - 1) for t, vr in zip(titles, vals.get("valueRanges", []), strict=False)}
            heads = await self._req("GET", f"{SHEETS}/{spreadsheet_id}/values:batchGet", params=[("ranges", f"'{t}'!1:1") for t in titles])
            headers_ok = {t: ((vr.get("values") or [[]])[0] == next((x.columns for x in tabs if x.title == t), None)) for t, vr in zip(titles, heads.get("valueRanges", []), strict=False)}
            try:
                perms = await self._req("GET", f"{DRIVE}/{spreadsheet_id}/permissions", params={"fields": "permissions(type,role)", "supportsAllDrives": "true"})
                public = any(p.get("type") == "anyone" and p.get("role") == "reader" for p in perms.get("permissions", []))
            except SheetsError:
                public = None
            expected = {t.title: len(t.rows) for t in tabs}
            return {"tabs": titles, "exactly_six_expected_tabs": titles == [t.title for t in tabs], "data_rows": rows, "expected_rows": expected,
                    "rows_match": rows == expected, "headers_match": headers_ok, "public_link_reader": public}
        finally:
            if own and self._session:
                await self._session.close()
                self._session = None

    async def export(self, tabs: list[Tab], *, spreadsheet_id: str | None, title: str, share_public: bool = False) -> str:
        own = self._session is None
        if own:
            self._session = aiohttp.ClientSession(trust_env=True)
        try:
            need = {t.title: required_grid(t) for t in tabs}
            total = sum(r * c for r, c in need.values())
            if total > MAX_CELLS:
                raise SheetsError(f"export needs {total:,} cells; Google Sheets allows {MAX_CELLS:,} per spreadsheet")
            if spreadsheet_id and not share_public:
                # Never write into a sheet that is publicly linked when a PRIVATE export was requested:
                # the data would become public the moment it is written.
                perms = await self._req("GET", f"{DRIVE}/{spreadsheet_id}/permissions", params={"fields": "permissions(type,role)", "supportsAllDrives": "true"})
                if any(p.get("type") == "anyone" for p in perms.get("permissions", [])):
                    raise SheetsError("spreadsheet currently has anyone-with-link access; refusing a PRIVATE export into it. In Google Sheets: "
                                      "Share -> General access -> Restricted, then re-run. Nothing was written.")
            if spreadsheet_id:
                meta = await self._req("GET", f"{SHEETS}/{spreadsheet_id}", params={"fields": "sheets.properties"})
                existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
                wanted = {t.title for t in tabs}
                reqs: list[dict[str, Any]] = [
                    {"addSheet": {"properties": {"title": t.title, "gridProperties": {"rowCount": need[t.title][0], "columnCount": need[t.title][1], "frozenRowCount": 1}}}}
                    for t in tabs if t.title not in existing
                ]
                reqs += [{"deleteSheet": {"sheetId": sid}} for name, sid in existing.items() if name not in wanted]
                if reqs:
                    await self._req("POST", f"{SHEETS}/{spreadsheet_id}:batchUpdate", json={"requests": reqs})
                await self.ensure_grid(spreadsheet_id, need)
            else:
                body = {"properties": {"title": title}, "sheets": [
                    {"properties": {"title": t.title, "gridProperties": {"rowCount": need[t.title][0], "columnCount": need[t.title][1], "frozenRowCount": 1}}}
                    for t in tabs]}
                spreadsheet_id = (await self._req("POST", SHEETS, json=body))["spreadsheetId"]
            for t in tabs:
                rng = f"'{t.title}'"
                await self._req("POST", f"{SHEETS}/{spreadsheet_id}/values/{rng}:clear", json={})
                all_rows = [t.columns] + [[_cell(v) for v in r] for r in t.rows]
                for i in range(0, len(all_rows), self.batch_rows):
                    part = all_rows[i : i + self.batch_rows]
                    await self._req(
                        "POST", f"{SHEETS}/{spreadsheet_id}/values:batchUpdate",
                        json={"valueInputOption": "RAW", "data": [{"range": f"{rng}!A{i + 1}", "majorDimension": "ROWS", "values": part}]},
                    )
                log.info("sheets.tab_written", tab=t.title, rows=len(t.rows))
            if share_public:
                await self._req("POST", f"{DRIVE}/{spreadsheet_id}/permissions", json={"role": "reader", "type": "anyone"}, params={"supportsAllDrives": "true"})
            self.last_spreadsheet_id = spreadsheet_id
            return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        finally:
            if own and self._session:
                await self._session.close()
                self._session = None
