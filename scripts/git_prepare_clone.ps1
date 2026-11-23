# Prepare (NOT commit, NOT push) the GitHub submission in a fresh clone at C:\FrontierAtlas-git.
# Uses your existing Windows Git authentication. Output: docs\git_review_<stamp>\ (log, status, staged file list, full diff, scans, tests).
$ErrorActionPreference = "Continue"
$proj = Split-Path -Parent $PSScriptRoot
$clone = "C:\FrontierAtlas-git"
$repo = "https://github.com/Tanzanite2K/Ai_Intelligence_Platform"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$rev = Join-Path $proj "docs\git_review_$stamp"
New-Item -ItemType Directory -Path $rev -Force | Out-Null
$log = Join-Path $rev "prepare.log"
$py = Join-Path $proj ".venv\Scripts\python.exe"
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONUTF8 = "1"; $env:GIT_TERMINAL_PROMPT = "1"
function L($m) { ("[{0}Z] {1}" -f (Get-Date).ToUniversalTime().ToString("HH:mm:ss"), $m) | Tee-Object -FilePath $log -Append }
function Run($title, [scriptblock]$cmd) { L "=== $title ==="; & $cmd 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append; L "--- exit $LASTEXITCODE" }

L "Git submission preparation $stamp ; project $proj ; clone $clone"
Run "git version" { git --version }
$gl = Get-Command gitleaks -ErrorAction SilentlyContinue
L ("gitleaks: " + $(if ($gl) { $gl.Source } else { "NOT INSTALLED" }))

if (Test-Path $clone) { L "STOP: $clone already exists - not touching it."; exit 2 }
Run "clone" { git clone $repo $clone }
if (-not (Test-Path (Join-Path $clone ".git"))) { L "STOP: clone failed (authentication or network). Nothing else done."; exit 3 }
Set-Location $clone
Run "remote" { git remote -v }
Run "branch" { git branch -a }
Run "current branch / HEAD" { git rev-parse --abbrev-ref HEAD; git log --oneline -5 }
Run "status before copy" { git status --short }
Run "tracked files before copy (count)" { (git ls-files | Measure-Object).Count }

# Copy project -> clone. Keeps files that exist only in the repo. README.md, LICENSE*, .github\ in the repo are not overwritten.
$xd = @(".git", ".venv", "venv", "node_modules", ".next", ".pytest_cache", ".ruff_cache", "__pycache__", (Join-Path $proj "credentials"), (Join-Path $proj "data\exports"), ".github", "htmlcov", "git_review_*")
$xf = @(".env", ".env.local", ".env.production", ".env.development", "*.db", "*.db-journal", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3", "*.pyc",
        "*.log", "service-account*.json", "*.json.key", "*.pem", "*.key", "*.p12", "*.pfx", "README.md", "LICENSE", "LICENSE.*", "LICENSE.md",
        "hf_org_cache.json", "source_check_state.json", "run_*.json", "*.tsbuildinfo", "RUN_GIT_PREPARE.cmd", "RUN_GIT_PUSH.cmd")
L "=== robocopy project -> clone (excluding secrets, databases, logs, caches, venv, node_modules, README/LICENSE/.github) ==="
robocopy $proj $clone /E /XD @xd /XF @xf /NFL /NDL /NJH /NP /R:1 /W:1 2>&1 | Tee-Object -FilePath $log -Append
L "--- robocopy exit $LASTEXITCODE (0-7 = success)"
# README: report the difference instead of overwriting
if (Test-Path (Join-Path $clone "README.md")) {
    git diff --no-index --stat -- (Join-Path $clone "README.md") (Join-Path $proj "README.md") 2>&1 | Tee-Object -FilePath (Join-Path $rev "readme_repo_vs_project.txt") | Out-Null
    git diff --no-index -- (Join-Path $clone "README.md") (Join-Path $proj "README.md") 2>&1 | Out-File -FilePath (Join-Path $rev "readme_repo_vs_project.diff") -Encoding utf8
    L "README.md: repo version kept; difference saved to readme_repo_vs_project.diff"
} else { L "README.md: repo has none (project README not copied; decide before commit)" }

Run "git add -A (staging only, no commit)" { git add -A }
Run "status after staging" { git status --short }
git status --short | Out-File -FilePath (Join-Path $rev "status.txt") -Encoding utf8
git diff --cached --name-status | Out-File -FilePath (Join-Path $rev "staged_files.txt") -Encoding utf8
git diff --cached --stat | Out-File -FilePath (Join-Path $rev "staged_stat.txt") -Encoding utf8
git diff --cached | Out-File -FilePath (Join-Path $rev "staged.diff") -Encoding utf8
Run "staged summary" { git diff --cached --shortstat }
Run "staged file count by status" { git diff --cached --name-status | ForEach-Object { $_.Substring(0,1) } | Group-Object | ForEach-Object { "$($_.Name): $($_.Count)" } }
Run "ignored-but-present check (must list .env, credentials, dbs)" { git status --ignored --short | Select-String -Pattern '^!!' | Select-Object -First 40 }

Set-Location $proj
Run "staged-file secret + forbidden-path scan" { & $py scripts\git_stage_scan.py $clone (Join-Path $rev "stage_scan.json") }
if ($gl) {
    Run "gitleaks: staged changes" { gitleaks protect --staged --source $clone --redact --no-banner --report-format json --report-path (Join-Path $rev "gitleaks_staged.json") }
    Run "gitleaks: full repository history" { gitleaks detect --source $clone --redact --no-banner --report-format json --report-path (Join-Path $rev "gitleaks_history.json") }
} else { L "gitleaks not installed: history scan NOT run (staged files scanned by git_stage_scan.py)" }

# Tests on the staged tree (backend uses the project's venv; frontend node_modules are not in the clone)
Set-Location $clone
Run "pytest in clone" { & $py -m pytest -q -p no:cacheprovider }
Run "ruff in clone" { & $py -m ruff check src tests scripts migrations }
Set-Location $proj
L "DONE - nothing committed, nothing pushed. Review folder: $rev"
