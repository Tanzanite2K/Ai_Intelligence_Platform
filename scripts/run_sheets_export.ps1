# Authorized Google Sheets export with evidence log (docs\sheets_export_<stamp>.log). No database writes.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = Join-Path $root "docs\sheets_export_$stamp.log"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
"Google Sheets export log $stamp (UTC)" | Tee-Object -FilePath $log -Append
& (Join-Path $root ".venv\Scripts\python.exe") scripts\sheets_export_run.py 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
"exit code: $LASTEXITCODE" | Tee-Object -FilePath $log -Append
