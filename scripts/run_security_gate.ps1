# Final security gate before activation. No production writes: security check (read-only provider calls) +
# activation preflight (opens production read-only; writes a new verified backup under C:\FrontierAtlas-backups).
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = Join-Path $root "docs\security_gate_$stamp.log"
$py = Join-Path $root ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
function Write-Log($m) { $m | Tee-Object -FilePath $log -Append }
Write-Log "Security gate $stamp ; production SHA-256: $((Get-FileHash data\platform.db -Algorithm SHA256).Hash.ToLower())"
foreach ($s in @("security_check", "activation_preflight")) {
    Write-Log ("=== {0} started {1}Z ===" -f $s, (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss"))
    & $py "scripts\$s.py" 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
    Write-Log "--- $s exit $LASTEXITCODE"
}
Write-Log "production SHA-256 at end: $((Get-FileHash data\platform.db -Algorithm SHA256).Hash.ToLower())"
