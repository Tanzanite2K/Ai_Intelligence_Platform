# Commit and push the prepared clone. Run ONLY after the user approves the review. No force push, no history rewrite.
$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $PSScriptRoot
$clone = "C:\FrontierAtlas-git"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$log = Join-Path $proj "docs\git_push_$stamp.log"
$py = Join-Path $proj ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"
function L($m) { ("[{0}Z] {1}" -f (Get-Date).ToUniversalTime().ToString("HH:mm:ss"), $m) | Tee-Object -FilePath $log -Append }
function Run($t, [scriptblock]$c) { L "=== $t ==="; & $c 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append; $script:rc = $LASTEXITCODE; L "--- exit $script:rc" }
Set-Location $clone
# The remote repository is empty, so the project README is required.
if (-not (Test-Path "README.md")) { Copy-Item (Join-Path $proj "README.md") "README.md"; L "README.md copied from project (remote repository had none)" }
Run "stage" { git add -A }
Run "staged scan (forbidden paths + secrets)" { & $py (Join-Path $proj "scripts\git_stage_scan.py") $clone (Join-Path $proj "docs\git_push_scan_$stamp.json") }
if ($script:rc -ne 0) { L "STOP: staged scan not clean - nothing committed."; exit 2 }
Run "staged summary" { git diff --cached --shortstat }
Run "commit" { git commit -m "Finalize Frontier Atlas production activation and submission" }
if ($script:rc -ne 0) { L "STOP: commit failed - nothing pushed."; exit 3 }
Run "branch" { git rev-parse --abbrev-ref HEAD }
Run "push (no force)" { git push -u origin HEAD }
if ($script:rc -ne 0) { L "STOP: push failed (see above). Local commit kept."; exit 4 }
$local = (git rev-parse HEAD).Trim()
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
$remote = ((git ls-remote origin "refs/heads/$branch") -split "\s+")[0]
L "local HEAD  $local"
L "remote $branch $remote"
L ("PUSH VERIFIED: " + ($local -eq $remote))
L "URL: https://github.com/Tanzanite2K/Ai_Intelligence_Platform/tree/$branch  commit: https://github.com/Tanzanite2K/Ai_Intelligence_Platform/commit/$local"
