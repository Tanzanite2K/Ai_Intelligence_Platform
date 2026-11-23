# Re-run ONLY the 1b live check (one Gemini extraction on a stored article), its tests, ruff, secret scan, report.
# Read-only on the database. No crawl, no enrichment, no entity rebuild, no credential/.env changes.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$log = "docs\llm_1b.log"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:LOG_JSON = "true"; $env:LOG_LEVEL = "WARNING"
Set-Content -Path $log -Value "=== 1b re-check $(Get-Date -Format o) ===" -Encoding UTF8
filter Out-Both { $l = "$_"; Write-Host $l; Add-Content -Path $log -Value $l -Encoding UTF8 }
$R = [ordered]@{}
function Step($id, $title, [string[]]$a) {
    Add-Content -Path $log -Value "`n>>> $id $title" -Encoding UTF8
    & $py @a 2>&1 | Out-Both
    $R[$id] = @{ title = $title; exit = $LASTEXITCODE }
}
Step "1" "live check 1b only (raw response captured, redacted)" @("scripts\llm_live_check.py", "--only", "1b_direct_extraction_real_news_entities")
Step "2" "validator/orchestrator/report tests" @("-m", "pytest", "-q", "-p", "no:cacheprovider", "tests\unit\test_chunking_validators.py", "tests\unit\test_llm_orchestrator.py", "tests\unit\test_scripts.py")
Step "3" "full pytest" @("-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=docs\test-results-windows.xml")
Step "4" "ruff" @("-m", "ruff", "check", "src", "scripts", "tests")
Step "5" "secret scan" @("scripts\scan_secrets.py")
Step "6" "FINAL_REPORT.md" @("scripts\final_report.py")
Write-Host "`n======== SUMMARY ========" -ForegroundColor Cyan
foreach ($k in $R.Keys) { Write-Host ("{0} {1,-7} {2}" -f $k, $(if ($R[$k].exit -eq 0) { "OK" } else { "FAILED" }), $R[$k].title) }
Select-String -Path "FINAL_REPORT.md" -Pattern "Overall readiness" | ForEach-Object { Write-Host $_.Line }
