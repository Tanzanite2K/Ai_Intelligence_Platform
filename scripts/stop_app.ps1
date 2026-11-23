# Stop the FrontierAtlas API (:8000) and web app (:3000) and make sure no other FrontierAtlas process can write the database.
# Only processes positively identified as FrontierAtlas are stopped. Anything else on those ports, and any other
# FrontierAtlas Python process (crawl, export, scripts, celery), is reported and left running: the script then fails.
#   -CheckOnly   report what would be stopped, stop nothing
# Exit code 0 = ports free and no other writer; 1 = something must be closed by hand first.
param([switch]$CheckOnly)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$fe = Join-Path $root "frontend"
$ApiWindow = "FrontierAtlas API :8000"
$WebWindow = "FrontierAtlas Web :3000"

function Say($m, $c = "Gray") { Write-Host $m -ForegroundColor $c }
function Fail($m) { Write-Host "[STOP] $m" -ForegroundColor Red; exit 1 }

function Get-Listener([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return $null
}

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

function Wait-PortFree([int]$port, [int]$seconds) {
    for ($i = 0; $i -lt ($seconds * 4); $i++) {
        if (-not (Get-Listener $port)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Stop-Service-OnPort([int]$port, [scriptblock]$isOurs, [scriptblock]$isServerProc, [string]$windowMarker, [string]$label) {
    $listener = Get-Listener $port
    if (-not $listener) { Say "[OK] $label (port $port): not running" Green; return }
    $chain = Get-Chain $listener
    $cmds = ($chain | ForEach-Object { [string]$_.CommandLine }) -join "`n"
    if (-not (& $isOurs $cmds)) {
        Fail "port $port is used by another program: $($chain[0].Name) (PID $listener). It was NOT stopped. Close it, then retry."
    }
    foreach ($p in $chain) {
        $cmd = [string]$p.CommandLine
        $isWindow = ($p.Name -match '^(powershell|pwsh)\.exe$') -and $cmd.Contains($windowMarker)
        if ((& $isServerProc $p) -or $isWindow) {
            if ($CheckOnly) { Say "    would stop PID $($p.ProcessId) ($($p.Name))" Yellow }
            else { Say "    stopping PID $($p.ProcessId) ($($p.Name))"; Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    if ($CheckOnly) { Say "[CHECK] $label (port $port) is a FrontierAtlas process and would be stopped" Yellow; return }
    if (-not (Wait-PortFree $port 20)) { Fail "port $port is still in use after stopping the FrontierAtlas $label. Close its window and retry." }
    Say "[OK] $label (port $port): stopped" Green
}

Say "=== Stopping FrontierAtlas before Phase 7B ===" Cyan
Stop-Service-OnPort 8000 `
    { param($c) ($c -match 'src\.main["'']?\s+serve|src\.api\.app') -and $c.ToLower().Contains($root.ToLower()) } `
    { param($p) ($p.Name -match '^python(w)?\.exe$') -and ([string]$p.CommandLine -match 'src\.main["'']?\s+serve|src\.api\.app') } `
    $ApiWindow "API"
Stop-Service-OnPort 3000 `
    { param($c) $c.ToLower().Contains(($fe + "\node_modules\next").ToLower()) -or ($c.Contains($WebWindow) -and $c -match '\bnext\b') } `
    { param($p) ($p.Name -match '^node\.exe$') -and ([string]$p.CommandLine).ToLower().Contains($fe.ToLower()) } `
    $WebWindow "Web app"

# Any other FrontierAtlas Python process could write the database (crawl, export, scripts, celery): report, never stop.
$others = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cl = [string]$_.CommandLine; $exe = [string]$_.ExecutablePath
    ($cl.ToLower().Contains($root.ToLower()) -or $exe.ToLower().StartsWith(($root + "\.venv").ToLower())) -and $_.ProcessId -ne $PID
}
if ($others) {
    foreach ($o in $others) { Say ("    PID {0}: {1}" -f $o.ProcessId, ([string]$o.CommandLine).Substring(0, [Math]::Min(160, ([string]$o.CommandLine).Length))) Yellow }
    if ($CheckOnly) { Say "[CHECK] the FrontierAtlas Python processes above would block Run B" Yellow; exit 1 }
    Fail "other FrontierAtlas Python processes are running (listed above). They were NOT stopped. Let them finish or close them, then retry."
}
Say "[OK] no other FrontierAtlas Python process is running" Green
if ($CheckOnly) { Say "Check only: nothing was stopped." Cyan }
exit 0
