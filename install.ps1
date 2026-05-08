# Huntova one-shot installer for Windows.
# Usage: iwr -useb https://github.com/enzostrano/huntova-public/raw/main/install.ps1 | iex
#
# Mirrors install.sh on macOS/Linux: detects Python, bootstraps pipx,
# installs Huntova from the public repo, spawns the local server, and
# opens the dashboard in your default browser. Idempotent.
#
# IMPORTANT: this script is designed to be `iwr | iex`-d. We must never
# call `exit` because under iex `exit` terminates the user's PowerShell
# host (the window closes). All work runs inside Install-HuntovaCore;
# fatal errors throw, early non-error returns use `return`.

function Install-HuntovaCore {
    # Local to this function — restores user's preferences when we return.
    $ErrorActionPreference = 'Stop'

    # Force UTF-8 in Python child processes — pipx prints ✨ emojis and
    # legacy Windows consoles default to cp1252, which trips
    # "'charmap' codec can't encode character" on the inner pipx run.
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"

    # Modern Windows Terminal handles UTF-8; legacy console will degrade to "?".
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

    function Step($m){ Write-Host "$([char]9656) $m" -ForegroundColor Cyan }
    function Ok($m){ Write-Host "$([char]10003) $m" -ForegroundColor Green }
    function Warn($m){ Write-Host "! $m" -ForegroundColor Yellow }
    # Fail() prints + throws. Outer try/catch will display the throw and
    # exit the function gracefully without taking the user's shell with it.
    function Fail($m){ Write-Host "$([char]10007) $m" -ForegroundColor Red; throw $m }
    function Chat($m){ Write-Host "  huntova: $m" -ForegroundColor Magenta }
    function Hint($m){ Write-Host "  $m" -ForegroundColor DarkGray }

    # Why no `2>$null` or `2>&1` anywhere on native commands below:
    # In PowerShell 5.1, *any* redirection of a native exe's stderr (to
    # $null, to a file, or merged via 2>&1) wraps each stderr line as a
    # NativeCommandError ErrorRecord. Combined with $ErrorActionPreference =
    # 'Stop' that aborts the script on benign warnings (e.g. pip's "Cache
    # entry deserialization failed"). Solution: don't touch stderr.
    # Unredirected native stderr goes straight to the console — no
    # ErrorRecord wrapping, no Stop trigger.

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

    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        Fail "This installer is for Windows. On macOS/Linux use:`n  curl -fsSL https://github.com/enzostrano/huntova-public/releases/latest/download/install.sh | sh"
    }

    $pipxBinCandidates = @(
        (Join-Path $env:USERPROFILE ".local\bin"),
        (Join-Path $env:APPDATA "Python\Scripts"),
        (Join-Path $env:APPDATA "Python\Python314\Scripts"),
        (Join-Path $env:APPDATA "Python\Python313\Scripts"),
        (Join-Path $env:APPDATA "Python\Python312\Scripts"),
        (Join-Path $env:APPDATA "Python\Python311\Scripts")
    )

    # ----------------------------------------------------------------
    # 1. Python 3.11+
    # ----------------------------------------------------------------
    Step "step 1/4: looking for Python 3.11+"

    function Test-PythonOk($cmd) {
        $v = $null
        try { $v = & $cmd -c "import sys; print('%d %d' % sys.version_info[:2])" } catch { return $false }
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
        $p = ($v -split ' ')
        return ([int]$p[0] -ge 3 -and [int]$p[1] -ge 11)
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

    # Resolve to absolute path so we can pin pipx's venv to this exact
    # interpreter — protects against stale pipx state from a previous
    # install where the original Python has since moved or been removed.
    $pyPath = (Get-Command $py).Source
    $pyVer = & $pyPath -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    Ok "Python $pyVer ($pyPath)"

    # ----------------------------------------------------------------
    # 2. pipx
    # ----------------------------------------------------------------
    Step "step 2/4: installing pipx"

    $hasPipx = [bool](Get-Command pipx -ErrorAction SilentlyContinue)
    if (-not $hasPipx) {
        & $pyPath -m pip --version | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "pip is missing on this Python install — reinstall Python with the 'pip' option enabled" }

        & $pyPath -m pip install --user --quiet --upgrade pip | Out-Null
        if ($LASTEXITCODE -ne 0) { Warn "pip self-upgrade returned non-zero, continuing..." }

        & $pyPath -m pip install --user --quiet pipx | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "pip install pipx failed" }

        & $pyPath -m pipx ensurepath | Out-Null

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
    # 3. Huntova — always uninstall + fresh install pinned to current Python
    # ----------------------------------------------------------------
    Step "step 3/4: installing huntova"
    Hint "(this step downloads the package from GitHub — takes 20-40s)"

    $pkg = "git+https://github.com/enzostrano/huntova-public.git"

    # Nuke any prior install — protects against the "invalid interpreter"
    # state where pipx's registry points at a Python that no longer exists.
    # `pipx uninstall` exits non-zero when the package isn't installed; we
    # don't care, just clear state.
    & $pyPath -m pipx uninstall huntova | Out-Null
    $global:LASTEXITCODE = 0

    # Fresh install pinned to the current Python interpreter via --python.
    # Don't pipe to Out-Null — users like seeing "creating virtual env...
    # installing package..." progress, and on failure we want pipx's real
    # error to be visible.
    & $pyPath -m pipx install --python $pyPath $pkg
    if ($LASTEXITCODE -ne 0) { Fail "pipx install failed — see error above. To debug, run manually: $pyPath -m pipx install --python `"$pyPath`" `"$pkg`"" }

    # Best-effort inject of questionary for the polished TUI wizard. Don't
    # fail the whole install if this hiccups — huntova works without it.
    & $pyPath -m pipx inject huntova questionary | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Warn "questionary inject returned non-zero — TUI will use the basic prompts. Not fatal."
        $global:LASTEXITCODE = 0
    }

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
        Warn "huntova binary not found on PATH despite a successful install."
        Hint "Open a new PowerShell window and run: huntova onboard"
        return
    }

    # ----------------------------------------------------------------
    # 4. Auto-launch the dashboard
    # ----------------------------------------------------------------
    Step "step 4/4: launching the dashboard in your browser"

    $port = 5050

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

    $srvProc = Start-Process -FilePath $huntovaCmd `
        -ArgumentList @('serve','--no-browser','--port',$port) `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError "$logFile.err" `
        -PassThru

    $ready = $false
    for ($i = 1; $i -le 20; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/runtime" -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
        Start-Sleep -Seconds 1
    }

    if (-not $ready) {
        Warn "server didn't respond in 20s. Open a new PowerShell window and run: huntova onboard"
        if (Test-Path $logFile) {
            Write-Host ""
            Hint "last 20 lines of $logFile :"
            Get-Content $logFile -Tail 20
        }
        return
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
}

# Entry point. Catch anything thrown by Fail() so the user's PowerShell
# host stays alive even on failure — they can read the error and re-run.
try {
    Install-HuntovaCore
} catch {
    Write-Host ""
    Write-Host "Installation aborted. See the error message above." -ForegroundColor DarkGray
    Write-Host "Issues: https://github.com/enzostrano/huntova-public/issues" -ForegroundColor DarkGray
    Write-Host ""
}
