# Migration compatibility only (copies in C:\FrontierAtlas-test; production is hashed, never written).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = Join-Path $root "docs\migration_checks_windows_$stamp.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
function Write-Log($m) { $m | Tee-Object -FilePath $log -Append }
$before = (Get-FileHash (Join-Path $root "data\platform.db") -Algorithm SHA256).Hash.ToLower()
Write-Log "Migration checks $stamp ; production SHA-256 before: $before"
$pre7b = Get-ChildItem "C:\FrontierAtlas-backups\phase7b-live-*\platform.pre7b.cold.db" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$codes = @{}
if ($pre7b) {
    Write-Log "Pre-7B backup: $($pre7b.FullName) SHA-256 $((Get-FileHash $pre7b.FullName -Algorithm SHA256).Hash.ToLower())"
    Write-Log "=== phase7a_migration_test on pre-7B backup copy ==="
    & $py scripts\phase7a_migration_test.py --source $pre7b.FullName 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
    $codes["pre7b"] = $LASTEXITCODE
} else { Write-Log "pre-7B backup NOT FOUND"; $codes["pre7b"] = -1 }
Write-Log "=== post7b_schema_check on production copy ==="
& $py scripts\post7b_schema_check.py 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
$codes["post7b"] = $LASTEXITCODE
$after = (Get-FileHash (Join-Path $root "data\platform.db") -Algorithm SHA256).Hash.ToLower()
Write-Log "production SHA-256 after: $after (unchanged: $($before -eq $after))"
Write-Log ("RESULT pre7b_exit={0} post7b_exit={1} OVERALL={2}" -f $codes["pre7b"], $codes["post7b"], $(if ($codes["pre7b"] -eq 0 -and $codes["post7b"] -eq 0 -and $before -eq $after) { "PASS" } else { "FAIL" }))
