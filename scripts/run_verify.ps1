# Targeted verification run — NO crawling, NO entity rebuild.
#   Double-click RUN_VERIFY.cmd, or: powershell -ExecutionPolicy Bypass -File scripts\run_verify.ps1
# Steps: backup -> integrations -> Gemini model selection (+ .env GEMINI_MODEL only) -> re-verify -> live LLM checks
#        -> small LLM enrichments (16 news, products with pricing cues) -> integrity + audit -> PRIVATE Sheets export
#        with read-back -> tests -> secret scan -> FINAL_REPORT.md.  Log: docs\verify_run.log
param([switch]$Enrich)   # record-updating LLM enrichment is OPT-IN (default run modifies no records)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$log = "docs\verify_run.log"
New-Item -ItemType Directory -Force docs | Out-Null
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:LOG_JSON = "true"; $env:LOG_LEVEL = "WARNING"
Set-Content -Path $log -Value "=== verify run started $(Get-Date -Format o) ===" -Encoding UTF8
$R = [ordered]@{}
filter Out-Both { $line = "$_"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }
function Set-Result($id, $name, $status, $exit, $reason) {
    $R[$id] = [ordered]@{ name = $name; status = $status; exit_code = $exit; reason = $reason }
    $c = @{ OK = "Green"; FAILED = "Red"; SKIPPED = "Yellow" }[$status]
    Write-Host ("[{0,-7}] {1} {2} {3}" -f $status, $id, $name, $reason) -ForegroundColor $c
    Add-Content -Path $log -Value ("[{0}] {1} {2} exit={3} {4}" -f $status, $id, $name, $exit, $reason) -Encoding UTF8
}
function Invoke-Step($id, $name, [string[]]$pyArgs, [string[]]$requires = @()) {
    foreach ($q in $requires) { if (-not $R.Contains($q) -or $R[$q].status -ne "OK") { Set-Result $id $name "SKIPPED" $null "prerequisite '$q' not OK"; return } }
    Add-Content -Path $log -Value "`n>>> $id $name :: python $($pyArgs -join ' ')" -Encoding UTF8
    & $py @pyArgs 2>&1 | Out-Both
    $code = $LASTEXITCODE
    Set-Result $id $name ($(if ($code -eq 0) { "OK" } else { "FAILED" })) $code ""
}
function Integration($name) {
    if (-not (Test-Path docs\integration_report.json)) { return "" }
    $c = (Get-Content docs\integration_report.json -Raw | ConvertFrom-Json).checks | Where-Object { $_.integration -eq $name } | Select-Object -First 1
    if ($c) { return $c.result } else { return "" }
}

Invoke-Step "01" "verified DB backup" @("scripts\backup_db.py")
Invoke-Step "02" "integration verification (before model fix)" @("scripts\verify_integrations.py")
Copy-Item docs\integration_report.json docs\integration_report.before.json -ErrorAction SilentlyContinue
Invoke-Step "03" "Gemini: list models, test, set GEMINI_MODEL" @("scripts\select_gemini_model.py", "--apply")
Invoke-Step "04" "integration verification (after model fix)" @("scripts\verify_integrations.py")
$gem = Integration "Gemini"; $sa = Integration "Google service account"; $sh = Integration "Google Sheets API"
if ($gem -eq "PASS") { Set-Result "g_llm" "gate" "OK" $null "Gemini=PASS" } else { Set-Result "g_llm" "gate" "SKIPPED" $null "Gemini=$gem" }
if ($sa -eq "PASS" -and $sh -in @("PASS", "WARN")) { Set-Result "g_sheets" "gate" "OK" $null "SA=PASS Sheets=PASS" } else { Set-Result "g_sheets" "gate" "SKIPPED" $null "SA=$sa Sheets=$sh" }

Invoke-Step "05" "live LLM checks (direct, invalid-key fallback, real Groq/DeepSeek failures, chunking, hallucination probe)" @("scripts\llm_live_check.py") @("g_llm")
if ($Enrich) {
    Invoke-Step "06" "LLM: organisations for stored news without them" @("-m", "src.main", "enrich", "--news") @("01", "g_llm")
    Invoke-Step "07" "LLM: pricing for stored products with pricing cues" @("-m", "src.main", "enrich", "--limit", "150") @("01", "g_llm")
} else {
    Set-Result "06" "LLM news enrichment" "SKIPPED" $null "not requested (run with -Enrich; updates 16 news records)"
    Set-Result "07" "LLM product pricing enrichment" "SKIPPED" $null "not requested (run with -Enrich)"
}
Invoke-Step "08" "SQLite integrity_check" @("-c", "import sqlite3,sys; r=sqlite3.connect('file:data/platform.db?mode=ro',uri=True).execute('pragma integrity_check').fetchone()[0]; print('integrity:',r); sys.exit(0 if r=='ok' else 1)")
Invoke-Step "09" "data audit" @("scripts\audit_data.py")
$bk = (Get-Content docs\backup_latest.json -Raw | ConvertFrom-Json).backup
Invoke-Step "10" "compare backup vs now" @("scripts\compare_db.py", $bk, "data\platform.db") @("01")
if ($R["g_sheets"].status -eq "OK") { Invoke-Step "11" "Google Sheets export (PRIVATE) + read-back" @("-m", "src.main", "export", "--sheets") @("08") }
else { Invoke-Step "11" "CSV export only (Sheets not authenticated)" @("-m", "src.main", "export") @("08") }
if (Test-Path docs\sheets_export.json) {
    $se = Get-Content docs\sheets_export.json -Raw | ConvertFrom-Json; $v = $se.sheets_verification
    if ($se.sheets_error) { Set-Result "11b" "sheets read-back" "FAILED" $null "see docs\sheets_export.json" }
    elseif ($v) { $ok = $v.exactly_six_expected_tabs -and $v.rows_match -and ($v.public_link_reader -ne $true)
        Set-Result "11b" "sheets read-back" ($(if ($ok) { "OK" } else { "FAILED" })) $null "six_tabs=$($v.exactly_six_expected_tabs) rows_match=$($v.rows_match) public=$($v.public_link_reader)" }
}
Invoke-Step "12" "pytest (full suite)" @("-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=docs\test-results-windows.xml")
Invoke-Step "13" "ruff" @("-m", "ruff", "check", "src", "scripts", "tests")
$summary = [ordered]@{ run = "verify"; finished = (Get-Date).ToString("o"); steps = $R }
$summary | ConvertTo-Json -Depth 5 | Out-File docs\verify_run_summary.json -Encoding utf8
Invoke-Step "14" "secret scan" @("scripts\scan_secrets.py")
Invoke-Step "15" "FINAL_REPORT.md" @("scripts\final_report.py")
$summary.steps = $R; $summary | ConvertTo-Json -Depth 5 | Out-File docs\verify_run_summary.json -Encoding utf8
Write-Host "`n============ SUMMARY ============" -ForegroundColor Cyan
foreach ($k in $R.Keys) { Write-Host ("{0,-9} {1,-8} {2} {3}" -f $k, $R[$k].status, $R[$k].name, $R[$k].reason) }
