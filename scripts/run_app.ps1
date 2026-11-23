# Start the FrontierAtlas web app locally: FastAPI (read-only API, 127.0.0.1:8000) + Next.js (127.0.0.1:3000).
# No database writes, no crawl, no enrichment. Close the two server windows to stop.
#   .\RUN_APP.cmd               -> development mode (hot reload)
#   .\RUN_APP.cmd -Production   -> production build + start
#
# Safe to run more than once. For each port the launcher decides:
#   REUSED     a healthy FrontierAtlas service started by this launcher, current with the code on disk
#   RESTARTED  a FrontierAtlas service that is stale (started before the latest code change), unhealthy, or not
#              verifiably read-only; only processes positively identified as FrontierAtlas are stopped
#   STARTED    the port was free
#   FAILED     the port is used by another program (never touched) or the service did not come up
param([switch]$Production)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Fail($msg) { Write-Host "`n[X] $msg" -ForegroundColor Red; exit 1 }
function Ok($msg) { Write-Host "[OK] $msg" -ForegroundColor Green }

# 1. Node.js 20.9+ (required by Next.js 16)
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host @"

[X] Node.js was not found.
    Install Node.js 20 LTS or newer (22 LTS recommended), then open a NEW terminal and run RUN_APP.cmd again:
      Option A (winget):  winget install OpenJS.NodeJS.LTS
      Option B (manual):  download the Windows Installer (.msi) 'LTS' from https://nodejs.org/en/download
    Verify with:  node -v   (must print v20.9.0 or higher)
"@ -ForegroundColor Yellow
    exit 1
}
$ver = (& node -v).TrimStart("v")
$parts = $ver.Split(".")
if ([int]$parts[0] -lt 20 -or ([int]$parts[0] -eq 20 -and [int]$parts[1] -lt 9)) {
    Fail "Node.js $ver is too old. Install Node.js 20.9+ (winget install OpenJS.NodeJS.LTS or https://nodejs.org) and retry."
}
Ok "Node.js v$ver"

# 2. Python virtual environment
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Fail "Python venv not found at .venv. Create it first (see README: Installation)." }
Ok "Python venv"

# 3. Database present (the API opens it read-only and never creates it)
if (-not (Test-Path (Join-Path $root "data\platform.db"))) { Fail "data\platform.db not found." }
Ok "Database found (opened read-only by the API)"

# 4. Frontend config + dependencies
$fe = Join-Path $root "frontend"
$envLocal = Join-Path $fe ".env.local"
if (-not (Test-Path $envLocal)) {
    "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000" | Set-Content -Path $envLocal -Encoding UTF8
    Ok "Created frontend\.env.local (API URL only - no secrets)"
}
if (-not (Test-Path (Join-Path $fe "node_modules"))) {
    Write-Host "Installing frontend dependencies (first run, ~1-2 min)..." -ForegroundColor Cyan
    Push-Location $fe
    & npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm ci failed." }
    Pop-Location
}
Ok "Frontend dependencies"

# 5. One launcher at a time (a second double-click while this one is starting would race it)
$mutex = New-Object System.Threading.Mutex($false, "Local\FrontierAtlas_RUN_APP")
if (-not $mutex.WaitOne(0)) { Fail "Another RUN_APP is already starting the app. Wait for it to finish." }

$ApiTitle = "FrontierAtlas AI Intelligence API"
$ApiWindow = "FrontierAtlas API :8000"
$WebWindow = "FrontierAtlas Web :3000"

function Info($msg) { Write-Host "    $msg" -ForegroundColor Gray }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }

function Get-Listener([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return $null
}

# The process and its parents (python.exe in a venv runs the real interpreter as a child; the launcher's window is a
# powershell.exe further up). Returns Win32_Process objects, listening process first.
function Get-Chain([int]$procId) {
    $chain = @(); $seen = @{}
    while ($procId -gt 4 -and -not $seen.ContainsKey($procId) -and $chain.Count -lt 8) {
        $seen[$procId] = $true
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        if (-not $p) { break }
        $chain += $p
        $procId = [int]$p.ParentProcessId
    }
    return ,$chain
}

function Get-Json([string]$url) {
    try { return Invoke-RestMethod -Uri $url -TimeoutSec 3 } catch { return $null }
}

function Get-NewestWrite([string]$dir, [string[]]$include) {
    $f = Get-ChildItem -Path $dir -Recurse -File -Include $include -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch '\\(node_modules|\.next|__pycache__)\\' } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { return $f.LastWriteTime }
    return [datetime]::MinValue
}

# Stop only the given processes of a chain that are positively FrontierAtlas: the interpreter/node processes running
# our server, and a powershell window this launcher opened (identified by its window title marker). A terminal the
# user opened themselves is left alone.
function Stop-Ours($chain, [scriptblock]$isServerProc, [string]$windowMarker) {
    foreach ($p in $chain) {
        $cmd = [string]$p.CommandLine
        $isWindow = ($p.Name -match '^(powershell|pwsh)\.exe$') -and $cmd.Contains($windowMarker)
        if ((& $isServerProc $p) -or $isWindow) {
            Info "stopping PID $($p.ProcessId) ($($p.Name))"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-PortFree([int]$port, [int]$seconds) {
    for ($i = 0; $i -lt ($seconds * 4); $i++) {
        if (-not (Get-Listener $port)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

$status = @{}

# 6. FastAPI on :8000 (localhost only, read-only database)
Write-Host "`n--- API (port 8000) ---" -ForegroundColor Cyan
$apiPid = Get-Listener 8000
$startApi = $true
if ($apiPid) {
    $chain = Get-Chain $apiPid
    $cmds = ($chain | ForEach-Object { [string]$_.CommandLine }) -join "`n"
    $proc = $chain[0]
    $spec = Get-Json "http://127.0.0.1:8000/openapi.json"
    $health = Get-Json "http://127.0.0.1:8000/health"
    $runsOurServer = $cmds -match 'src\.main["'']?\s+serve|src\.api\.app'
    $titleOk = $spec -and $spec.info.title -eq $ApiTitle
    $inProject = $cmds.ToLower().Contains($root.ToLower())
    if (-not ($runsOurServer -and ($titleOk -or $inProject))) {
        Fail ("Port 8000 is used by another program: $($proc.Name) (PID $apiPid). It was NOT stopped.`n" +
              "    Close that program or free port 8000, then run RUN_APP.cmd again.")
    }
    $healthy = $health -and $health.status -eq "ok"
    $fromLauncher = $cmds.Contains($ApiWindow) -and $cmds.Contains("API_READ_ONLY='true'")
    $codeTime = Get-NewestWrite (Join-Path $root "src") @("*.py")
    $startedAt = (Get-Process -Id $apiPid -ErrorAction SilentlyContinue).StartTime
    $current = $startedAt -and $startedAt -gt $codeTime
    if ($healthy -and $fromLauncher -and $current) {
        Ok "API already running and current (PID $apiPid, started $startedAt) - REUSED"
        $status.api = "REUSED"; $startApi = $false
    } else {
        $why = @()
        if (-not $healthy) { $why += "not healthy" }
        if (-not $current) { $why += "started $startedAt, before the latest code change ($codeTime)" }
        if (-not $fromLauncher) { $why += "not started by RUN_APP, so API_READ_ONLY=true cannot be confirmed" }
        Warn ("A FrontierAtlas API is running on port 8000 but " + ($why -join "; ") + ". Restarting it.")
        Stop-Ours $chain { param($p) ($p.Name -match '^python(w)?\.exe$') -and ([string]$p.CommandLine -match 'src\.main["'']?\s+serve|src\.api\.app') } $ApiWindow
        if (-not (Wait-PortFree 8000 15)) { Fail "Port 8000 is still in use after stopping the old FrontierAtlas API. Close its window and retry." }
        $status.api = "RESTARTED"
    }
}
if ($startApi) {
    $apiCmd = "Set-Location '$root'; `$env:API_HOST='127.0.0.1'; `$env:API_READ_ONLY='true'; `$env:PYTHONIOENCODING='utf-8'; `$Host.UI.RawUI.WindowTitle='$ApiWindow'; & '$py' -m src.main serve"
    Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command", $apiCmd | Out-Null
    Write-Host "Starting API on http://127.0.0.1:8000 (API_READ_ONLY=true) ..." -ForegroundColor Cyan
    $up = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 750
        $h = Get-Json "http://127.0.0.1:8000/health"
        if ($h -and $h.status -eq "ok") { $up = $true; break }
    }
    if (-not $up) { Fail "API did not become healthy within 45 s. Check the '$ApiWindow' window for the error." }
    if (-not $status.api) { $status.api = "STARTED" }
    Ok "API healthy - $($status.api)"
}
$brief = try { (Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/intelligence/brief" -UseBasicParsing -TimeoutSec 20).StatusCode } catch { $_.Exception.Response.StatusCode.value__ }
if ($brief -ne 200) { Fail "API is up but /api/v1/intelligence/brief answered HTTP $brief. Check the '$ApiWindow' window." }
Ok "API routes current (/api/v1/intelligence/brief -> 200; docs: http://127.0.0.1:8000/docs)"

# 7. Next.js on :3000
Write-Host "`n--- Web app (port 3000) ---" -ForegroundColor Cyan
$webPid = Get-Listener 3000
$startWeb = $true
if ($webPid) {
    $chain = Get-Chain $webPid
    $cmds = ($chain | ForEach-Object { [string]$_.CommandLine }) -join "`n"
    $proc = $chain[0]
    $ourNext = $cmds.ToLower().Contains(($fe + "\node_modules\next").ToLower()) -or ($cmds.Contains($WebWindow) -and $cmds -match '\bnext\b')
    if (-not $ourNext) {
        Fail ("Port 3000 is used by another program: $($proc.Name) (PID $webPid). It was NOT stopped.`n" +
              "    Close that program or free port 3000, then run RUN_APP.cmd again.")
    }
    $page = try { Invoke-WebRequest -Uri "http://127.0.0.1:3000/" -UseBasicParsing -TimeoutSec 60 } catch { $null }
    $healthy = $page -and $page.StatusCode -eq 200 -and $page.Content -match 'FrontierAtlas'
    $isDev = $cmds -match '\bnext(\.cmd|")?\s+dev\b|\bdev\s+--hostname'
    $startedAt = (Get-Process -Id $webPid -ErrorAction SilentlyContinue).StartTime
    $codeTime = Get-NewestWrite (Join-Path $fe "src") @("*.ts", "*.tsx", "*.css")
    $current = $isDev -or ($startedAt -and $startedAt -gt $codeTime)  # dev mode hot-reloads code changes
    $modeOk = ($isDev -and -not $Production) -or (-not $isDev -and $Production)
    if ($healthy -and $current -and $modeOk) {
        Ok ("Web app already running (" + $(if ($isDev) { "dev" } else { "production" }) + ", PID $webPid) - REUSED")
        $status.web = "REUSED"; $startWeb = $false
    } else {
        $why = @()
        if (-not $healthy) { $why += "not responding" }
        if (-not $current) { $why += "production build older than the latest frontend change" }
        if (-not $modeOk) { $why += "running in " + $(if ($isDev) { "dev" } else { "production" }) + " mode" }
        Warn ("A FrontierAtlas web server is running on port 3000 but " + ($why -join "; ") + ". Restarting it.")
        Stop-Ours $chain { param($p) ($p.Name -match '^node\.exe$') -and ([string]$p.CommandLine).ToLower().Contains($fe.ToLower()) } $WebWindow
        if (-not (Wait-PortFree 3000 15)) { Fail "Port 3000 is still in use after stopping the old FrontierAtlas web server. Close its window and retry." }
        $status.web = "RESTARTED"
    }
}
if ($startWeb) {
    if ($Production) {
        $feCmd = "Set-Location '$fe'; `$Host.UI.RawUI.WindowTitle='$WebWindow'; `$env:NEXT_TELEMETRY_DISABLED='1'; npm run build; if (`$LASTEXITCODE -eq 0) { npx next start --hostname 127.0.0.1 --port 3000 }"
    } else {
        $feCmd = "Set-Location '$fe'; `$Host.UI.RawUI.WindowTitle='$WebWindow'; `$env:NEXT_TELEMETRY_DISABLED='1'; npx next dev --hostname 127.0.0.1 --port 3000"
    }
    Start-Process powershell -ArgumentList "-NoExit", "-NoProfile", "-Command", $feCmd | Out-Null
    Write-Host "Starting web app on http://127.0.0.1:3000 ..." -ForegroundColor Cyan
    $up = $false
    for ($i = 0; $i -lt 240; $i++) {
        Start-Sleep -Milliseconds 750
        try { Invoke-WebRequest -Uri "http://127.0.0.1:3000/" -UseBasicParsing -TimeoutSec 5 | Out-Null; $up = $true; break } catch {}
    }
    if (-not $up) { Fail "Web app did not start within 3 minutes. Check the '$WebWindow' window." }
    if (-not $status.web) { $status.web = "STARTED" }
    Ok "Web app ready - $($status.web)"
}

$mutex.ReleaseMutex()
Write-Host "`n=== FrontierAtlas ===" -ForegroundColor Cyan
Write-Host ("  API      http://127.0.0.1:8000   " + $status.api + "   (read-only database)")
Write-Host ("  Web app  http://127.0.0.1:3000   " + $status.web)
Start-Process "http://127.0.0.1:3000/"
Write-Host "`nRunning. Close the '$ApiWindow' and '$WebWindow' windows to stop. Running RUN_APP.cmd again reuses them." -ForegroundColor Cyan
