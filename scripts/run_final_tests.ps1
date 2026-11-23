# FrontierAtlas complete Windows verification run. Changes nothing in production:
#   - backend: pytest (temporary databases only), Ruff, Phase 7A migration test replayed on the pre-7B backup + post-7B schema check (copies in C:\FrontierAtlas-test)
#   - frontend: TypeScript, ESLint and the production build in an isolated copy of frontend\ (the running dev server is untouched)
#   - database safety (production read-only; backup verification; rollback rehearsal on a disposable copy)
#   - security check (credential validity via read-only calls, secret scan; no secret is printed)
#   - activation dry run on a disposable copy (contacts the 10 selected public sources; no LLM)
#   - the live data\platform.db is only hashed (before and after) to prove it did not change
# Output: docs\final_tests_windows_<stamp>.log and .json
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = Join-Path $root "docs\final_tests_windows_$stamp.log"
$json = Join-Path $root "docs\final_tests_windows_$stamp.json"
$py = Join-Path $root ".venv\Scripts\python.exe"
$fe = Join-Path $root "frontend"
$feCopy = "C:\FrontierAtlas-test\fe-build-$stamp"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:NEXT_TELEMETRY_DISABLED = "1"
$results = [ordered]@{}

function Write-Log($m) { $m | Tee-Object -FilePath $log -Append }
function Step([string]$name, [scriptblock]$cmd) {
    Write-Log ""
    Write-Log ("=== {0} === started {1}Z" -f $name, (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss"))
    $t0 = Get-Date
    & $cmd 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
    $secs = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    $results[$name] = [ordered]@{ started_utc = $t0.ToUniversalTime().ToString("o"); exit_code = $code; seconds = $secs; result = $(if ($code -eq 0) { "PASS" } else { "FAIL" }) }
    Write-Log ("--- {0}: {1} (exit {2}, {3} s)" -f $name, $results[$name].result, $code, $secs)
}

$liveBefore = (Get-FileHash (Join-Path $root "data\platform.db") -Algorithm SHA256).Hash.ToLower()
Write-Log "FrontierAtlas final Windows tests $stamp"
Write-Log "Live database SHA-256 before: $liveBefore"
Write-Log ("Python: " + (& $py --version 2>&1)); Write-Log ("Node: " + (node -v)); Write-Log ("OS: " + [Environment]::OSVersion.VersionString)

Step "backend_pytest" { & $py -m pytest -q -p no:cacheprovider --junitxml "docs\test-results-final-windows-$stamp.xml" }
Step "backend_ruff" { & $py -m ruff check src tests scripts migrations }
# Production is already at 0002 (Phase 7B), so the 7A path (baseline -> 0002) is replayed on the verified pre-7B backup,
# and the current production schema is checked on a copy (upgrade head is a no-op, ORM == schema).
$pre7b = Get-ChildItem "C:\FrontierAtlas-backups\phase7b-live-*\platform.pre7b.cold.db" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($pre7b) {
    Write-Log "Pre-7B backup: $($pre7b.FullName) SHA-256 $((Get-FileHash $pre7b.FullName -Algorithm SHA256).Hash.ToLower())"
    Step "migration_test_pre7b_backup_copy" { & $py scripts\phase7a_migration_test.py --source $pre7b.FullName }
} else { $results["migration_test_pre7b_backup_copy"] = [ordered]@{ exit_code = -1; result = "FAIL"; note = "pre-7B backup not found" } }
Step "migration_post7b_schema_on_copy" { & $py scripts\post7b_schema_check.py }
Step "database_safety_backup_rollback" { & $py scripts\database_safety_check.py }
Step "security_check" { & $py scripts\security_check.py }
Step "activation_dry_run_disposable_copy" { & $py scripts\activation_dryrun.py }

Write-Log ""
Write-Log "=== copy frontend to $feCopy (excluding .next) ==="
robocopy $fe $feCopy /E /XD .next /NFL /NDL /NJH /NJS /NP | Out-Null
$copyOk = $LASTEXITCODE -lt 8
Write-Log "robocopy exit $LASTEXITCODE (0-7 = success)"
if ($copyOk) {
    Push-Location $feCopy
    Step "frontend_typecheck" { & npx.cmd tsc --noEmit }
    Step "frontend_eslint" { & npx.cmd eslint . }
    Step "frontend_production_build" { & npx.cmd next build }
    Pop-Location
} else {
    $results["frontend_copy"] = [ordered]@{ exit_code = $LASTEXITCODE; result = "FAIL" }
}

$liveAfter = (Get-FileHash (Join-Path $root "data\platform.db") -Algorithm SHA256).Hash.ToLower()
Write-Log ""
Write-Log "Live database SHA-256 after:  $liveAfter  (unchanged: $($liveBefore -eq $liveAfter))"
$all = ($results.Values | Where-Object { $_.result -ne "PASS" }).Count -eq 0
$summary = [ordered]@{ stamp = $stamp; overall = $(if ($all) { "PASS" } else { "FAIL" }); live_sha256_before = $liveBefore; live_sha256_after = $liveAfter;
    live_unchanged = ($liveBefore -eq $liveAfter); frontend_copy = $feCopy; steps = $results }
$summary | ConvertTo-Json -Depth 5 | Set-Content -Path $json -Encoding UTF8
Write-Log ""
Write-Log "OVERALL: $($summary.overall)   Report: $json"
