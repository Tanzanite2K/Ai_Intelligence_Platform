# Final security re-check of the prepared clone (NO commit, NO push):
#   install Gitleaks (winget, user scope) -> re-sync redacted files -> add README -> stage -> Gitleaks + stage scan -> tests.
$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $PSScriptRoot
$clone = "C:\FrontierAtlas-git"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$rev = Join-Path $proj "docs\git_review_$stamp"
New-Item -ItemType Directory -Path $rev -Force | Out-Null
$log = Join-Path $rev "recheck.log"
$py = Join-Path $proj ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"
function L($m) { ("[{0}Z] {1}" -f (Get-Date).ToUniversalTime().ToString("HH:mm:ss"), $m) | Tee-Object -FilePath $log -Append }
function Run($t, [scriptblock]$c) { L "=== $t ==="; & $c 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append; $script:rc = $LASTEXITCODE; L "--- exit $script:rc" }
if (-not (Test-Path (Join-Path $clone ".git"))) { L "STOP: clone not found"; exit 2 }

# 1. Gitleaks: official release v8.30.1 (user-approved download), SHA-256 verified against the release checksums,
#    unpacked to C:\FrontierAtlas-test\tools\gitleaks. No admin rights, no PATH change, no system install.
$ver = "8.30.1"
$tools = "C:\FrontierAtlas-test\tools\gitleaks"
$exe = Join-Path $tools "gitleaks.exe"
if (-not (Test-Path $exe)) {
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    $base = "https://github.com/gitleaks/gitleaks/releases/download/v$ver"
    $zip = Join-Path $tools "gitleaks_${ver}_windows_x64.zip"
    $sums = Join-Path $tools "gitleaks_${ver}_checksums.txt"
    L "=== download gitleaks v$ver ==="
    try {
        Invoke-WebRequest -Uri "$base/gitleaks_${ver}_windows_x64.zip" -OutFile $zip -UseBasicParsing
        Invoke-WebRequest -Uri "$base/gitleaks_${ver}_checksums.txt" -OutFile $sums -UseBasicParsing
    } catch { L "download failed: $($_.Exception.Message)" }
    if ((Test-Path $zip) -and (Test-Path $sums)) {
        $expect = ((Get-Content $sums | Where-Object { $_ -match "gitleaks_${ver}_windows_x64\.zip" }) -split "\s+")[0].ToLower()
        $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
        L "zip size $((Get-Item $zip).Length) bytes; SHA-256 $got ; expected $expect"
        if ($expect -and $got -eq $expect) { Expand-Archive -Path $zip -DestinationPath $tools -Force; L "checksum OK - unpacked" }
        else { L "CHECKSUM MISMATCH - gitleaks NOT used"; $exe = $null }
    } else { $exe = $null }
}
if ($exe -and (Test-Path $exe)) { Run "gitleaks version" { & $exe version }; L "gitleaks: $exe" } else { $exe = $null; L "gitleaks NOT available" }

# 2. Re-sync (same exclusions as the first preparation; only changed files are copied)
$xd = @(".git", ".venv", "venv", "node_modules", ".next", ".pytest_cache", ".ruff_cache", "__pycache__", (Join-Path $proj "credentials"), (Join-Path $proj "data\exports"), ".github", "htmlcov", "git_review_*")
$xf = @(".env", ".env.local", ".env.production", ".env.development", "*.db", "*.db-journal", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3", "*.pyc",
        "*.log", "service-account*.json", "*.json.key", "*.pem", "*.key", "*.p12", "*.pfx", "LICENSE", "LICENSE.*", "LICENSE.md",
        "hf_org_cache.json", "source_check_state.json", "run_*.json", "*.tsbuildinfo", "RUN_GIT_PREPARE.cmd", "RUN_GIT_PUSH.cmd", "RUN_GIT_RECHECK.cmd")
L "=== robocopy re-sync (README.md now included: the remote repository has none) ==="
robocopy $proj $clone /E /XD @xd /XF @xf /NDL /NJH /NP /R:1 /W:1 2>&1 | Tee-Object -FilePath $log -Append
L "--- robocopy exit $LASTEXITCODE (0-7 = success)"

Set-Location $clone
Run "stage" { git add -A }
git diff --cached --name-status | Out-File -FilePath (Join-Path $rev "staged_files.txt") -Encoding utf8
git diff --cached | Out-File -FilePath (Join-Path $rev "staged.diff") -Encoding utf8
Run "staged summary" { git diff --cached --shortstat }
Run "README staged?" { git diff --cached --name-only -- README.md }
Run "sensitive paths present in the clone but ignored (never staged)" { git status --ignored --short | Select-String '^!!' }
Run "remaining sheet-id / service-account references in staged files" { git grep --cached -n -I -E "spreadsheets/d/[A-Za-z0-9_-]{25,}|[a-z0-9-]+-[0-9]{6}\.iam\.gserviceaccount\.com|[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com" -- . ':!README.md' }

# 3. Scans
Set-Location $proj
Run "staged scan (forbidden paths + secret patterns + literal .env values)" { & $py scripts\git_stage_scan.py $clone (Join-Path $rev "stage_scan.json") }
if ($exe) {
    Set-Location $clone
    Run "gitleaks: staged changes (git --pre-commit --staged)" { & $exe git --pre-commit --staged --redact --no-banner --report-format json --report-path (Join-Path $rev "gitleaks_staged.json") . }
    if ($script:rc -eq 126 -or $script:rc -eq 2) { Run "gitleaks: staged changes (legacy protect --staged)" { & $exe protect --staged --source . --redact --no-banner --report-format json --report-path (Join-Path $rev "gitleaks_staged.json") } }
    Run "gitleaks: every file in the clone (dir)" { & $exe dir . --redact --no-banner --report-format json --report-path (Join-Path $rev "gitleaks_dir.json") }
    Set-Location $proj
}

# 4. Tests on the staged tree
Set-Location $clone
Run "pytest in clone" { & $py -m pytest -q -p no:cacheprovider }
Run "ruff in clone" { & $py -m ruff check src tests scripts migrations }
Set-Location $proj
L "DONE - nothing committed, nothing pushed. Review folder: $rev"
