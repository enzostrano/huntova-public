# Huntova one-shot installer for Windows.
# Usage: iwr -useb https://github.com/enzostrano/huntova-public/raw/main/install.ps1 | iex
#
# Mirrors install.sh on macOS/Linux: detects Python, bootstraps pipx,
# installs Huntova from the public repo, spawns the local server, and
# opens the dashboard in your default browser. Idempotent.

$ErrorActionPreference = 'Stop'

# Modern Windows Terminal handles UTF-8; legacy console will degrade to "?".
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Step($m){ Write-Host "$([char]9656) $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "$([char]10003) $m" -ForegroundColor Green }
function Warn($m){ Write-Host "! $m" -ForegroundColor Yellow }
function Fail($m){ Write-Host "$([char]10007) $m" -ForegroundColor Red; exit 1 }
function Chat($m){ Write-Host "  huntova: $m" -ForegroundColor Magenta }

Write-Host ""
$logo = @'
   ##   ## ##    ## ###    ## ########  ######  ##    ##  #####
   ##   ## ##    ## ####   ##    ##    ##    ## ##    ## ##   ##
   ####### ##    ## ## ##  ##    ##    ##    ## ##    ## #######
   ##   ## ##    ## ##  ## ##    ##    ##    ##  ##  ##  ##   ##
   ##   ##  ######  ##   ####    ##     ######    ####   ##   ##
'@
Write-Host $logo -ForegroundColor Magenta
Write-Host "        local-first BYOK lead-gen agent - find clients while you sleep" -ForegroundColor DarkGray
Write-Host ""
Chat "alright let's do this - installing me on your Windows machine should take ~60s..."
Write-Host ""

# Pre-flight: PowerShell 7+ exposes $IsWindows; on PS 5.1 it's $null but we're guaranteed Windows.
if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
    Fail "This installer is for Windows. On macOS/Linux use:`n  curl -fsSL https://github.com/enzostrano/huntova-public/releases/latest/download/install.sh | sh"
}

# Common pipx bin locations on Windows — used to repair PATH after pipx install.
$pipxBinCandidates = @(
    (Join-Path $env:USERPROFILE ".local\bin"),
    (Join-Path $env:APPDATA "Python\Scripts"),
    (Join-Path $env:APPDATA "Python\Python313\Scripts"),
    (Join-Path $env:APPDATA "Python\Python312\Scripts"),
    (Join-Path $env:APPDATA "Python\Python311\Scripts")
)

# ----------------------------------------------------------------
# 1. Python 3.11+
# ----------------------------------------------------------------
Step "step 1/4: looking for Python 3.11+"

function Test-PythonOk($cmd) {
    try {
        $v = & $cmd -c "import sys; print('%d %d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
        $p = ($v -split ' ')
        return ([int]$p[0] -ge 3 -and [int]$p[1] -ge 11)
    } catch { return $false }
}

$py = $null
foreach ($c in @('python3.13','python3.12','python3.11','python','py')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) {
        if (Test-PythonOk $c) { $py = $c; break }
    }
}

if (-not $py) {
    Chat "no Python 3.11+ found - installing Python 3.13 via winget..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail @"
winget not found. Two options:
  1. Install 'App Installer' from the Microsoft Store, then re-run this script.
  2. Download Python 3.13 from https://www.python.org/downloads/windows/
     (tick 'Add Python to PATH' on the first installer screen), then re-run.
"@
    }
    & winget install --id Python.Python.3.13 --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { Fail "winget install Python.Python.3.13 failed (exit $LASTEXITCODE)" }

    # winget updates the persistent PATH but not the current session.
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path","Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path","User")
    $env:Path = "$machinePath;$userPath"

    foreach ($c in @('python3.13','python','py')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) {
            if (Test-PythonOk $c) { $py = $c; break }
        }
    }
    if (-not $py) {
        Fail "Python installed but not on PATH yet. Close and reopen PowerShell, then re-run this installer."
    }
}

$pyVer = & $py -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Ok "Python $pyVer ($py)"

# ----------------------------------------------------------------
# 2. pipx
# ----------------------------------------------------------------
Step "step 2/4: installing pipx"

$hasPipx = [bool](Get-Command pipx -ErrorAction SilentlyContinue)
if (-not $hasPipx) {
    & $py -m pip --version | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "pip is missing on this Python install — reinstall Python with the 'pip' option enabled" }

    & $py -m pip install --user --quiet --upgrade pip 2>&1 | Out-Null
    & $py -m pip install --user --quiet pipx 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "pip install pipx failed" }
    & $py -m pipx ensurepath --quiet 2>&1 | Out-Null

    foreach ($p in $pipxBinCandidates) {
        if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) {
            $env:Path = "$p;$env:Path"
        }
    }
    Ok "pipx installed"
} else {
    Ok "pipx already installed"
}

# ----------------------------------------------------------------
# 3. Huntova
# ----------------------------------------------------------------
Step "step 3/4: installing huntova"

$pkg = "git+https://github.com/enzostrano/huntova-public.git"

# Use `python -m pipx` for reliability — pipx command might not be on PATH yet
# in this session even though we updated $env:Path above.
$pipxList = (& $py -m pipx list --short 2>$null)
$alreadyInstalled = $false
if ($pipxList) {
    foreach ($line in $pipxList) {
        if ($line -match "^huntova\b") { $alreadyInstalled = $true; break }
    }
}

if ($alreadyInstalled) {
    & $py -m pipx upgrade --force huntova 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & $py -m pipx install --force $pkg 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "pipx install huntova failed" }
    }
} else {
    & $py -m pipx install $pkg 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "pipx install $pkg failed" }
}
& $py -m pipx inject huntova questionary 2>&1 | Out-Null
Ok "Huntova installed"

# Find huntova binary for the launch step.
$huntovaCmd = $null
if (Get-Command huntova -ErrorAction SilentlyContinue) {
    $huntovaCmd = (Get-Command huntova).Source
} else {
    foreach ($p in $pipxBinCandidates) {
        $exe = Join-Path $p "huntova.exe"
        if (Test-Path $exe) { $huntovaCmd = $exe; break }
    }
}
if (-not $huntovaCmd) {
    Warn "huntova binary not found on PATH. Open a new PowerShell window and run: huntova onboard"
    exit 0
}

# ----------------------------------------------------------------
# 4. Auto-launch the dashboard
# ----------------------------------------------------------------
Step "step 4/4: launching the dashboard in your browser"

$port = 5050

# Free :5050 if a stale server is bound (best-effort).
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    foreach ($c in $existing) {
        try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
    Start-Sleep -Seconds 1
}

$logDir = Join-Path $env:LOCALAPPDATA "huntova"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "install-launch.log"

# Spawn detached. -WindowStyle Hidden + redirect = no console window flash.
$srvProc = Start-Process -FilePath $huntovaCmd `
    -ArgumentList @('serve','--no-browser','--port',$port) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError "$logFile.err" `
    -PassThru

# Poll /api/runtime up to 15s. Server boots in 3-5s typical, 8-10s on first run.
$ready = $false
for ($i = 1; $i -le 15; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/runtime" -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}

if (-not $ready) {
    Warn "server didn't respond in 15s. Open a new PowerShell window and run: huntova onboard"
    if (Test-Path $logFile) {
        Write-Host ""
        Write-Host "  last 20 lines of $logFile :" -ForegroundColor DarkGray
        Get-Content $logFile -Tail 20
    }
    exit 0
}

$url = "http://127.0.0.1:$port/"
Start-Process $url
Ok "browser opened at $url"

Write-Host ""
Write-Host "  +---------------------------------------------------------+" -ForegroundColor Magenta
Write-Host "  |                                                         |" -ForegroundColor Magenta
Write-Host "  |   $([char]10003) Huntova is installed and ready to hunt.             |" -ForegroundColor Magenta
Write-Host "  |                                                         |" -ForegroundColor Magenta
Write-Host "  |   The dashboard is open in your browser.                |" -ForegroundColor Magenta
Write-Host "  |   Click 'Auto Wizard' to set up your AI provider.       |" -ForegroundColor Magenta
Write-Host "  |                                                         |" -ForegroundColor Magenta
Write-Host "  |   Server running in background (PID $($srvProc.Id)).               |" -ForegroundColor Magenta
Write-Host "  |   Stop it with: Stop-Process -Id $($srvProc.Id)                 |" -ForegroundColor Magenta
Write-Host "  |                                                         |" -ForegroundColor Magenta
Write-Host "  +---------------------------------------------------------+" -ForegroundColor Magenta
Write-Host ""
Chat "13 providers ready, default is Claude. Type 'huntova chat' to talk to me directly."
Write-Host ""
Write-Host "  Docs:    https://github.com/enzostrano/huntova-public" -ForegroundColor Cyan
Write-Host "  Issues:  https://github.com/enzostrano/huntova-public/issues" -ForegroundColor Cyan
Write-Host "  Support: hello@huntova.com" -ForegroundColor Cyan
Write-Host ""
