# Final submission run (Windows PowerShell 5.1+ / 7+).
#   Double-click RUN_FINAL.cmd, or:  powershell -ExecutionPolicy Bypass -File scripts\run_final.ps1 [-SkipDocker]
#
# Safety:
# * one process at a time writes the SQLite DB (strictly sequential steps; OneDrive-safe);
# * verified backup (online backup API + integrity_check + SHA-256) before any DB-modifying step;
# * DB-modifying steps run only if backup AND the test suite pass;
# * API-dependent steps run only if that integration authenticated in step 02;
# * Google Sheets export is PRIVATE (no --share-public); public access is verified to be OFF;
# * secrets are never printed; the log is secret-scanned at the end.
# Every step is recorded in docs\final_run.log and docs\final_run_summary.json as OK / FAILED / SKIPPED (with reason).
param([switch]$SkipDocker)

$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py  = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$log = "docs\final_run.log"
New-Item -ItemType Directory -Force docs | Out-Null
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:LOG_JSON = "true"; $env:LOG_LEVEL = "WARNING"
$started = Get-Date
Set-Content -Path $log -Value "=== final run started $(Get-Date -Format o) on $env:COMPUTERNAME ===" -Encoding UTF8
$R = [ordered]@{}   # step id -> result

function Write-Log([string]$msg) { Add-Content -Path $log -Value $msg -Encoding UTF8; Write-Host $msg }
# Stream command output to console and log as UTF-8 (Tee-Object would write UTF-16 on Windows PowerShell 5.1)
filter Out-Both { $line = "$_"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

function Set-Result($id, $name, $status, $exit, $secs, $reason) {
    $R[$id] = [ordered]@{ name = $name; status = $status; exit_code = $exit; seconds = $secs; reason = $reason }
    $color = @{ OK = "Green"; FAILED = "Red"; SKIPPED = "Yellow"; UNAVAILABLE = "Yellow" }[$status]
    Write-Host ("[{0,-11}] {1} {2} {3}" -f $status, $id, $name, $reason) -ForegroundColor $color
    Add-Content -Path $log -Value ("[{0}] {1} {2} exit={3} {4}s {5}" -f $status, $id, $name, $exit, $secs, $reason) -Encoding UTF8
}

function Test-Ok([string[]]$ids) { foreach ($i in $ids) { if (-not $R.Contains($i) -or $R[$i].status -ne "OK") { return $i } }; return $null }

# Run a Python step; skipped (not run) when any prerequisite step is not OK.
function Invoke-Step($id, $name, [string[]]$pyArgs, [string[]]$requires = @()) {
    $missing = Test-Ok $requires
    if ($missing) { Set-Result $id $name "SKIPPED" $null 0 "prerequisite '$missing' is $($R[$missing].status)"; return }
    Write-Log "`n>>> $id $name :: python $($pyArgs -join ' ')"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $py @pyArgs 2>&1 | Out-Both
    $code = $LASTEXITCODE
    Set-Result $id $name ($(if ($code -eq 0) { "OK" } else { "FAILED" })) $code ([int]$sw.Elapsed.TotalSeconds) ""
}

function Set-Gate($id, [bool]$ok, $reason) { Set-Result $id "gate" ($(if ($ok) { "OK" } else { "SKIPPED" })) $null 0 $reason }

# ---------------------------------------------------------------- 00 environment
if (-not (Test-Path $py)) { Write-Host "No .venv found. Create it first: python -m venv .venv" -ForegroundColor Red; exit 2 }
Invoke-Step "00a" "python >= 3.12" @("-c", "import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3, 12) else 1)")
Invoke-Step "00b" "install pinned dependencies" @("-m", "pip", "install", "-q", "-r", "requirements-dev.txt", "ruff==0.11.10") @("00a")
Invoke-Step "00c" "pip check (dependency consistency)" @("-m", "pip", "check") @("00b")
Invoke-Step "00d" "compile all sources" @("-m", "compileall", "-q", "src", "scripts", "tests") @("00b")
Set-Gate "env" ((Test-Ok @("00a", "00b", "00d")) -eq $null) "environment usable"

# ---------------------------------------------------------------- 01 backup
Invoke-Step "01" "verified DB backup (integrity + SHA-256)" @("scripts\backup_db.py") @("env")
$baseline = $null
if ($R["01"].status -eq "OK") { $baseline = (Get-Content docs\backup_latest.json -Raw | ConvertFrom-Json).backup }

# ---------------------------------------------------------------- 02 integrations + gates
Invoke-Step "02" "integration verification (live)" @("scripts\verify_integrations.py") @("env")
$ir = @{}
if (Test-Path docs\integration_report.json) { (Get-Content docs\integration_report.json -Raw | ConvertFrom-Json).checks | ForEach-Object { $ir[$_.integration] = $_.result } }
function Is-Pass($k) { return ($ir[$k] -eq "PASS") }
Set-Gate "g_github" (Is-Pass "GitHub API") "GitHub=$($ir['GitHub API'])"
Set-Gate "g_hf" ($ir["Hugging Face Hub"] -in @("PASS", "WARN")) "HuggingFace=$($ir['Hugging Face Hub'])"
Set-Gate "g_ph" (Is-Pass "Product Hunt API") "ProductHunt=$($ir['Product Hunt API'])"
Set-Gate "g_llm" ((Is-Pass "Gemini") -or (Is-Pass "Groq") -or (Is-Pass "DeepSeek")) "Gemini=$($ir['Gemini']) Groq=$($ir['Groq']) DeepSeek=$($ir['DeepSeek'])"
Set-Gate "g_sheets" ((Is-Pass "Google service account") -and ($ir["Google Sheets API"] -in @("PASS", "WARN"))) "SA=$($ir['Google service account']) Sheets=$($ir['Google Sheets API'])"

# ---------------------------------------------------------------- 03 tests + lint
Invoke-Step "03" "pytest (full suite)" @("-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=docs\test-results-windows.xml") @("env")
Invoke-Step "03b" "ruff lint" @("-m", "ruff", "check", "src", "scripts", "tests") @("00b")
Set-Gate "db_write" ((Test-Ok @("01", "03")) -eq $null) "backup=$($R['01'].status) tests=$($R['03'].status)"

# ---------------------------------------------------------------- 04-10 data (sequential, single writer)
# YC is a single static index (fast, full). HF org/product listings need one lookup per author, so they are capped
# to roughly the volume already held (~400 orgs / ~1,900 products): enough to restore records lost to the old
# name-collision bug without a multi-hour crawl. Each source is its own command so --limit applies to that source only.
Invoke-Step "04a" "startups: Y Combinator (full)" @("-m", "src.main", "crawl", "--source", "yc_startups", "--no-llm") @("db_write")
if ($R["g_hf"].status -eq "OK") { Invoke-Step "04b" "startups: Hugging Face orgs (cap 450)" @("-m", "src.main", "crawl", "--source", "hf_orgs", "--limit", "450", "--no-llm") @("db_write") }
else { Set-Result "04b" "startups: Hugging Face orgs" "SKIPPED" $null 0 "Hugging Face not reachable" }
if ($R["g_ph"].status -eq "OK") { Invoke-Step "05a" "products: Product Hunt (official API)" @("-m", "src.main", "crawl", "--source", "producthunt", "--no-llm") @("db_write") }
else { Set-Result "05a" "products: Product Hunt" "SKIPPED" $null 0 "Product Hunt token not authenticated" }
if ($R["g_hf"].status -eq "OK") { Invoke-Step "05b" "products: Hugging Face (cap 1900)" @("-m", "src.main", "crawl", "--source", "hf_products", "--limit", "1900", "--no-llm") @("db_write") }
else { Set-Result "05b" "products: Hugging Face" "SKIPPED" $null 0 "Hugging Face not reachable" }
Invoke-Step "06" "LLM pricing enrichment (evidence-checked)" @("-m", "src.main", "enrich", "--limit", "150") @("db_write", "g_llm")
Invoke-Step "07" "refresh GitHub stars (>24h old)" @("-m", "src.main", "refresh-stars") @("db_write", "g_github")
Invoke-Step "08" "rebuild entity resolution" @("-m", "src.main", "rebuild-entities") @("db_write")
Invoke-Step "09" "fresh news (24h window)" @("-m", "src.main", "crawl", "--vertical", "news") @("db_write")
Invoke-Step "10" "fresh jobs (24h window)" @("-m", "src.main", "crawl", "--vertical", "jobs", "--no-llm") @("db_write")

# ---------------------------------------------------------------- 11-14 verification (read-only)
Invoke-Step "11" "live LLM checks (incl. forced fallback)" @("scripts\llm_live_check.py") @("env", "g_llm")
Invoke-Step "12" "SQLite integrity_check" @("-c", "import sqlite3,sys; r=sqlite3.connect('file:data/platform.db?mode=ro',uri=True).execute('pragma integrity_check').fetchone()[0]; print('integrity:',r); sys.exit(0 if r=='ok' else 1)") @("env")
Invoke-Step "13" "data integrity audit" @("scripts\audit_data.py") @("env")
if ($baseline) { Invoke-Step "14" "compare baseline vs final DB" @("scripts\compare_db.py", $baseline, "data\platform.db") @("01") }
else { Set-Result "14" "compare baseline vs final DB" "SKIPPED" $null 0 "no verified backup" }

# ---------------------------------------------------------------- 15 export (private)
if ($R["g_sheets"].status -eq "OK") { Invoke-Step "15" "export: Google Sheets (PRIVATE) + CSV" @("-m", "src.main", "export", "--sheets") @("12") }
else { Invoke-Step "15" "export: CSV only (Sheets not authenticated)" @("-m", "src.main", "export") @("12") }
if (Test-Path docs\sheets_export.json) {
    $se = Get-Content docs\sheets_export.json -Raw | ConvertFrom-Json
    $v = $se.sheets_verification
    if ($se.sheets_error) { Set-Result "15b" "sheets read-back" "FAILED" $null 0 "export error recorded in docs\sheets_export.json" }
    elseif ($v) {
        $ok = $v.exactly_six_expected_tabs -and $v.rows_match -and ($v.public_link_reader -ne $true)
        Set-Result "15b" "sheets read-back (6 tabs, rows, headers, private)" ($(if ($ok) { "OK" } else { "FAILED" })) $null 0 "six_tabs=$($v.exactly_six_expected_tabs) rows_match=$($v.rows_match) public=$($v.public_link_reader)"
    }
}

# ---------------------------------------------------------------- 16 docker (optional, does not touch the SQLite DB)
$dv = [ordered]@{ attempted = $false }
if ($SkipDocker) { Set-Result "16" "docker end-to-end" "SKIPPED" $null 0 "-SkipDocker" }
elseif (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Set-Result "16" "docker end-to-end" "UNAVAILABLE" $null 0 "docker CLI not installed"; $dv.reason = "docker CLI not installed" }
else {
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Set-Result "16" "docker end-to-end" "UNAVAILABLE" $null 0 "Docker daemon not running"; $dv.reason = "Docker daemon not running" }
    else {
        $dv.attempted = $true; $sw = [Diagnostics.Stopwatch]::StartNew()
        Write-Log "`n>>> 16 docker: build, postgres+redis, tests in container, api health"
        docker compose build api 2>&1 | Out-Both; $dv.build = $LASTEXITCODE
        docker compose up -d postgres redis 2>&1 | Out-Both
        $ready = $false; for ($i = 0; $i -lt 30 -and -not $ready; $i++) { Start-Sleep 2; docker compose exec -T postgres pg_isready -U aip *> $null; $ready = ($LASTEXITCODE -eq 0) }
        $dv.postgres_ready = $ready
        $blank = @("GITHUB_TOKEN", "HF_TOKEN", "PRODUCTHUNT_TOKEN", "GEMINI_API_KEY", "GROQ_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_SERVICE_ACCOUNT_FILE", "GOOGLE_SHEET_ID") | ForEach-Object { @("-e", "$_=") }
        docker compose run --rm -v "${PWD}\docs:/app/docs" @blank -e "TEST_DATABASE_URL=postgresql+asyncpg://aip:aip@postgres:5432/aip" -e "TEST_REDIS_URL=redis://redis:6379/1" api python -m pytest -q -p no:cacheprovider --junitxml=docs/test-results-docker.xml 2>&1 | Out-Both
        $dv.tests_exit = $LASTEXITCODE
        docker compose up -d api 2>&1 | Out-Both
        $health = $null; for ($i = 0; $i -lt 20 -and -not $health; $i++) { Start-Sleep 3; try { $health = (Invoke-WebRequest -UseBasicParsing http://localhost:8000/health -TimeoutSec 3).Content } catch {} }
        $dv.api_health = $health
        docker compose down 2>&1 | Out-Both
        $ok = ($dv.build -eq 0) -and $ready -and ($dv.tests_exit -eq 0) -and ($health -match "ok")
        Set-Result "16" "docker end-to-end" ($(if ($ok) { "OK" } else { "FAILED" })) $null ([int]$sw.Elapsed.TotalSeconds) "build=$($dv.build) pg=$ready tests=$($dv.tests_exit) health=$health"
    }
}
$dv | ConvertTo-Json | Out-File docs\docker_validation.json -Encoding utf8

# ---------------------------------------------------------------- 17-18 secret scan + report
$summary = [ordered]@{ started = $started.ToString("o"); finished = (Get-Date).ToString("o"); baseline_backup = $baseline; steps = $R }
$summary | ConvertTo-Json -Depth 5 | Out-File docs\final_run_summary.json -Encoding utf8
Invoke-Step "17" "secret scan (repo + logs)" @("scripts\scan_secrets.py") @("env")
Invoke-Step "18" "regenerate FINAL_REPORT.md" @("scripts\final_report.py") @("env")
$summary.finished = (Get-Date).ToString("o"); $summary.steps = $R
$summary | ConvertTo-Json -Depth 5 | Out-File docs\final_run_summary.json -Encoding utf8

Write-Host "`n================ SUMMARY ================" -ForegroundColor Cyan
foreach ($k in $R.Keys) { if ($R[$k].name -ne "gate") { Write-Host ("{0,-5} {1,-12} {2}  {3}" -f $k, $R[$k].status, $R[$k].name, $R[$k].reason) } }
$failed = @($R.Keys | Where-Object { $R[$_].status -eq "FAILED" })
Write-Host "`nFailed steps: $(if ($failed) { $failed -join ', ' } else { 'none' })"
Write-Host "Evidence: docs\final_run.log, docs\final_run_summary.json, FINAL_REPORT.md"
if ($failed) { exit 1 } else { exit 0 }
