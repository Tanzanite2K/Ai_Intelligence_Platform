# Gemini-only live verification. Reads no/writes no database records, runs no enrichment or crawl.
# 1) integration verifier (Gemini = shared health check: minimal generation + production extraction, bounded retries)
# 2) ONLY if Gemini still fails: model selector (tests listed text models one by one; updates GEMINI_MODEL only after a full live pass)
#    then one more Gemini verification
# 3) ONLY if Gemini passes: live LLM checks (fallback with invalid credentials, real Groq/DeepSeek auth failures, chunking, hallucination probe)
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = Join-Path (Get-Location) ".venv\Scripts\python.exe"
$log = "docs\gemini_check.log"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:LOG_JSON = "true"; $env:LOG_LEVEL = "WARNING"
Set-Content -Path $log -Value "=== gemini check $(Get-Date -Format o) ===" -Encoding UTF8
filter Out-Both { $l = "$_"; Write-Host $l; Add-Content -Path $log -Value $l -Encoding UTF8 }
function Run([string]$title, [string[]]$a) { Add-Content -Path $log -Value "`n>>> $title" -Encoding UTF8; & $py @a 2>&1 | Out-Both; return $LASTEXITCODE }
function GeminiResult { (Get-Content docs\integration_report.json -Raw | ConvertFrom-Json).checks | Where-Object { $_.integration -eq "Gemini" } | Select-Object -First 1 }

Run "verify integrations" @("scripts\verify_integrations.py") | Out-Null
$g = GeminiResult
Write-Host "Gemini: $($g.result) $($g.category) - $($g.detail)" -ForegroundColor Cyan
if ($g.result -ne "PASS") {
    Run "select model (only because Gemini failed)" @("scripts\select_gemini_model.py", "--apply") | Out-Null
    Run "verify integrations (after selection)" @("scripts\verify_integrations.py") | Out-Null
    $g = GeminiResult
    Write-Host "Gemini after selection: $($g.result) $($g.category) - $($g.detail)" -ForegroundColor Cyan
}
if ($g.result -eq "PASS") { Run "live LLM checks" @("scripts\llm_live_check.py") | Out-Null }
else { Add-Content -Path $log -Value "[SKIPPED] live LLM checks: Gemini=$($g.result) ($($g.category))" -Encoding UTF8; Write-Host "[SKIPPED] live LLM checks" -ForegroundColor Yellow }
Run "final report" @("scripts\final_report.py") | Out-Null
Write-Host "`nEvidence: docs\gemini_check.log, docs\gemini_health.json, docs\gemini_model_selection.json, docs\llm_live_check.json" -ForegroundColor Green
