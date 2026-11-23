# Final Gemini-only verification. No database writes, no crawl, no enrichment, no entity rebuild.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$log = "docs\llm_final.log"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:LOG_JSON = "true"; $env:LOG_LEVEL = "WARNING"
Set-Content -Path $log -Value "=== LLM final verification $(Get-Date -Format o) ===" -Encoding UTF8
filter Out-Both { $l = "$_"; Write-Host $l; Add-Content -Path $log -Value $l -Encoding UTF8 }
$R = [ordered]@{}
function Step($id, $title, [string[]]$a) {
    Add-Content -Path $log -Value "`n>>> $id $title" -Encoding UTF8
    & $py @a 2>&1 | Out-Both
    $R[$id] = @{ title = $title; exit = $LASTEXITCODE }
    Add-Content -Path $log -Value "[$(if ($LASTEXITCODE -eq 0) {'OK'} else {'FAILED'})] $id $title exit=$LASTEXITCODE" -Encoding UTF8
}
Step "1" "install pinned deps (chardet==5.2.0)" @("-m", "pip", "install", "-q", "-r", "requirements-dev.txt")
Step "2" "requests imports without RequestsDependencyWarning" @("-c", "import warnings; warnings.simplefilter('error'); import requests, chardet; print('requests', requests.__version__, 'chardet', chardet.__version__, '- no dependency warning')")
Step "3" "live LLM re-checks (hallucination probe, real-article extraction)" @("scripts\llm_live_check.py", "--only", "4_hallucination_probe_fields_absent", "1b_direct_extraction_real_news_entities")
Step "4" "pytest" @("-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=docs\test-results-windows.xml")
Step "5" "ruff" @("-m", "ruff", "check", "src", "scripts", "tests")
Step "6" "secret scan" @("scripts\scan_secrets.py")
Step "7" "FINAL_REPORT.md" @("scripts\final_report.py")
Write-Host "`n======== SUMMARY ========" -ForegroundColor Cyan
foreach ($k in $R.Keys) { Write-Host ("{0} {1,-7} {2}" -f $k, $(if ($R[$k].exit -eq 0) { "OK" } else { "FAILED" }), $R[$k].title) }
