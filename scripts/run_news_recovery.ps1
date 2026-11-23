# Targeted recovery of the news articles whose organisation extraction was dead-lettered.
# Steps stop at the first failure. Writes ONLY: a new timestamped backup, those news records' organisation fields,
# their entity mappings, and report files under docs\. Never crawls, deletes, rebuilds or touches dead letters.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = "docs\news_recovery_run_$stamp.log"
$before = "docs\news_recovery_before_$stamp.json"
$verifyOut = "docs\news_recovery_verify_$stamp.json"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:LOG_JSON = "true"; $env:LOG_LEVEL = "WARNING"
New-Item -ItemType Directory -Force -Path docs | Out-Null
Set-Content -Path $log -Value "=== news recovery $stamp ===" -Encoding UTF8
function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c; Add-Content -Path $log -Value $m -Encoding UTF8 }
function Stop-Run($m) { Say "`n[STOPPED] $m" "Red"; Say "Nothing after this point was executed. Log: $log" "Red"; exit 1 }
function Run($title, [string[]]$a) {
    Say "`n>>> $title" "Cyan"
    $ErrorActionPreference = "Continue"
    & $py @a 2>&1 | ForEach-Object { $l = "$_"; Write-Host $l; Add-Content -Path $log -Value $l -Encoding UTF8 }
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    return $code
}

if (-not (Test-Path $py)) { Stop-Run "Python venv not found (.venv)." }
if (-not (Test-Path "data\platform.db")) { Stop-Run "data\platform.db not found." }

# 1. No other process may be using the database
Say "`n>>> 1. checking for processes that could write to the database" "Cyan"
$busy = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -and ($_.CommandLine -match "src\.main|uvicorn|celery|llm_live_check|recover_news_entities|backup_db")
}
if ($busy) {
    $busy | ForEach-Object { Say ("  running: PID {0} {1}" -f $_.ProcessId, ($_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length)))) "Yellow" }
    Stop-Run "Close the API/app windows (RUN_APP) and any crawl/enrichment first, then run again."
}
foreach ($port in 8000, 3000) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { Stop-Run "Port $port is in use (is RUN_APP still running?). Close it first." }
}
foreach ($j in "data\platform.db-journal", "data\platform.db-wal") {
    if (Test-Path $j) { Stop-Run "$j exists: another SQLite writer may be active or crashed. Investigate before continuing." }
}
Say "  none found" "Green"

# 2. Scope + configuration (no API calls, no writes)
if ((Run "2. scope check (no API calls, no writes)" @("scripts\recover_news_entities.py", "--check", "--expected", "16")) -ne 0) { Stop-Run "Scope check failed or scope is not exactly 16 articles." }

# 3. Baseline fingerprint (read-only)
if ((Run "3. baseline snapshot -> $before" @("scripts\recover_news_entities.py", "--snapshot", $before)) -ne 0) { Stop-Run "Baseline snapshot failed (integrity_check not ok?)." }

# 4. Backup (SQLite online backup API + integrity_check + row counts + SHA-256)
if ((Run "4. database backup" @("scripts\backup_db.py")) -ne 0) { Stop-Run "Backup or its verification failed." }

# 5. Confirmation
Say "`nThe next step calls Gemini for the 16 articles and writes their validated organisation tags and mappings." "Yellow"
$answer = Read-Host "Type RECOVER to continue (anything else cancels)"
if ($answer -ne "RECOVER") { Stop-Run "Cancelled by user. The database was not modified (backup and snapshot were created)." }

# 6. Recovery
if ((Run "6. recovery (Gemini extraction + validation + targeted write)" @("scripts\recover_news_entities.py", "--apply", "--expected", "16")) -ne 0) {
    Stop-Run "Recovery refused or failed. Check the report in docs\ and the backup in data\ (docs\backup_latest.json)."
}

# 7. Verification against the baseline
if ((Run "7. verify only in-scope rows changed -> $verifyOut" @("scripts\recover_news_entities.py", "--verify", $before, $verifyOut)) -ne 0) {
    Stop-Run "VERIFICATION FAILED. Do not use the database; restore from the backup listed in docs\backup_latest.json if needed."
}

# 8. Tests + lint
$t = Run "8. backend tests" @("-m", "pytest", "-q", "-p", "no:cacheprovider")
$r = Run "8. ruff" @("-m", "ruff", "check", "src", "scripts", "tests")

# 9. API smoke test (read-only API on a spare port, stopped afterwards)
Say "`n>>> 9. API smoke test" "Cyan"
$env:API_HOST = "127.0.0.1"; $env:API_PORT = "8011"; $env:API_READ_ONLY = "true"
$api = Start-Process -FilePath $py -ArgumentList "-m", "src.main", "serve" -PassThru -WindowStyle Hidden
$ok = $false
try {
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 750
        try { if ((Invoke-RestMethod "http://127.0.0.1:8011/health" -TimeoutSec 2).status -eq "ok") { $ok = $true; break } } catch {}
    }
    if ($ok) {
        $ov = (Invoke-RestMethod "http://127.0.0.1:8011/api/v1/dashboard/overview").data
        $news = Invoke-RestMethod "http://127.0.0.1:8011/api/v1/news?page_size=100"
        $tagged = @($news.data | Where-Object { $_.organizations.Count -gt 0 }).Count
        Say ("  API ok: news={0}, mappings={1}, dead letters={2}, news with organisation tags={3}" -f $ov.records.news, $ov.entity_mappings, $ov.dead_letters, $tagged) "Green"
    } else { Say "  API did not start within 30 s" "Red" }
} finally {
    if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
    Remove-Item Env:API_PORT -ErrorAction SilentlyContinue
}

Say "`n======== SUMMARY ========" "Cyan"
Say ("verification: OK   tests: {0}   ruff: {1}   api: {2}" -f $(if ($t -eq 0) { "OK" } else { "FAILED" }), $(if ($r -eq 0) { "OK" } else { "FAILED" }), $(if ($ok) { "OK" } else { "FAILED" })) "Cyan"
Say "Reports: docs\news_recovery_apply_*.json, $verifyOut, docs\backup_latest.json. Log: $log" "Cyan"
