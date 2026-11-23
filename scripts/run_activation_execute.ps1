# AUTHORIZED production activation (user: "AUTHORIZE PRODUCTION WRITES", 2026-11-23 14:08 IST). Full log in docs\.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = Join-Path $root "docs\activation_execution_$stamp.log"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
"Activation execution log $stamp (UTC)" | Tee-Object -FilePath $log -Append
& (Join-Path $root ".venv\Scripts\python.exe") scripts\activation_execute.py 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
"exit code: $LASTEXITCODE" | Tee-Object -FilePath $log -Append
