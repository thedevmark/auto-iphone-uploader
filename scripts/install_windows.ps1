<#
.SYNOPSIS
  Set up Auto iPhone Uploader on this Windows PC: one command, then sign once.

.DESCRIPTION
  Run it as often as you like: every step is skipped when it is already done.
  It never touches the iPhone and never reads or prints your passcode. Steps:

    1. Python 3.11+ check, then the pinned packages from requirements.txt and
       the separate re-sign tool from requirements-resign.txt (pymobiledevice3,
       GPL-3.0, run only as a separate process; see third_party/NOTICE.md).
    2. The iPhone connector, go-ios 1.3.2 (MIT): the official Windows release
       zip is downloaded from GitHub, its SHA-256 checked against the value
       pinned below, and ios.exe unpacked into tools\go-ios\. Nothing is
       installed system-wide.
    3. The phone control app, WebDriverAgent 16.12.9 (BSD-3): the official
       UNSIGNED runner zip is downloaded, its SHA-256 checked, and repacked as
       wda\WebDriverAgent.ipa. You sign it once with your own Apple ID in
       Sideloadly (the app walks you through it); a signed build is never
       redistributed.
    4. .env: GO_IOS_PATH and WDA_IPA point at those files. Other keys are kept.
       When no passcode is saved yet, it offers to save one now (hidden prompt,
       scripts\set_passcode.py).
    5. A Desktop shortcut that opens the app, and a Startup entry that brings
       the app and the iPhone connection up at sign-in without opening a
       browser window.
    6. The USB recovery helper: read-only check, then an offer to install it
       (recommended; Windows asks for administrator rights once).
    7. The setup checklist (python -m video_drop.setup_report), then the app
       opens so you can finish the one-time signing from its first-run screens.

.PARAMETER CheckOnly
  Report what is installed and run the checklist. Downloads nothing, writes nothing, asks nothing.
.PARAMETER SkipDownloads
  Do everything except the two downloads (offline). Existing files are still verified.
.PARAMETER NoPrompt
  Never ask anything (passcode, USB helper): skip those offers and list them at the end.
.PARAMETER NoLaunch
  Do not open the app when setup finishes.
.PARAMETER NoStartup
  Do not create the Startup entry (the app then starts only from the shortcut).
.PARAMETER NoChecklist
  Skip the checklist at the end (used by CI).
.PARAMETER ReplaceShortcut
  Repoint an existing Desktop/Startup shortcut at this checkout.
.PARAMETER Python
  Use this python.exe instead of the one on PATH. The Windows installer
  (AutoiPhoneUploader-Setup-<version>.exe) passes the Python it ships.
.PARAMETER PackagesBundled
  The packages are already installed inside the Python given with -Python (the Windows
  installer ships them), so skip pip and only check they import. The re-sign tool is
  then the separate interpreter in python-resign\ next to this folder's python\.
.PARAMETER NoDesktopShortcut
  Do not create the Desktop shortcut (the Windows installer creates it as an optional task).
.PARAMETER DesktopDir, StartupDir
  Where to put the shortcuts; defaults to this user's Desktop and Startup folders.
.PARAMETER InstallUsbHelper
  Install the USB recovery helper and exit (idempotent). Windows asks for administrator
  rights once (UAC); the helper is an on-demand scheduled task running as SYSTEM that can
  only restart Apple Mobile Device Service and reset the iPhone's USB port, so the app can
  recover a stalled USB link without a replug. See docs\usb-recovery-helper.md.
.PARAMETER UninstallUsbHelper
  Remove the USB recovery helper (its task and folder) and exit. Asks for administrator rights.
#>
param(
    [switch]$CheckOnly,
    [switch]$SkipDownloads,
    [switch]$NoPrompt,
    [switch]$NoLaunch,
    [switch]$NoStartup,
    [switch]$NoChecklist,
    [switch]$ReplaceShortcut,
    [switch]$InstallUsbHelper,
    [switch]$UninstallUsbHelper,
    [switch]$PackagesBundled,
    [switch]$NoDesktopShortcut,
    [string]$Python = '',
    [string]$DesktopDir = '',
    [string]$StartupDir = ''
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'This setup script runs on Windows. On another system, use python -m video_drop.server.'
}

# ---- pinned downloads (third_party/NOTICE.md) ---------------------------------
$GoIosVersion = '1.3.2'
$GoIosUrl     = "https://github.com/danielpaulus/go-ios/releases/download/v$GoIosVersion/go-ios-win.zip"
$GoIosSha256  = '939C6BCAAFED183A92AFB9F79CC11B1F935FA6389BFC94D3902E3F52C4DFF3FE'
$GoIosSize    = 8824605
$WdaVersion   = '16.12.9'
$WdaUrl       = "https://github.com/appium/WebDriverAgent/releases/download/v$WdaVersion/WebDriverAgentRunner-Runner.zip"
$WdaSha256    = '8A48EC564DA204EFA60CC4769395C95D19F58489BDE7E612BC3C869AE904F9D9'
$WdaSize      = 1127151

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$toolsDir    = Join-Path $projectRoot 'tools\go-ios'
$goIosZip    = Join-Path $toolsDir 'go-ios-win.zip'
$goIosExe    = Join-Path $toolsDir 'ios.exe'
$wdaDir      = Join-Path $projectRoot 'wda'
$wdaZip      = Join-Path $wdaDir 'WebDriverAgentRunner-Runner.zip'
$wdaIpa      = Join-Path $wdaDir 'WebDriverAgent.ipa'
$envFile     = Join-Path $projectRoot '.env'
$problems    = New-Object System.Collections.Generic.List[string]
$StepCount   = 7
# Questions are asked only in an interactive window; scripts and CI get the same run without them.
$interactive = -not $NoPrompt -and -not $CheckOnly -and [Environment]::UserInteractive

function Say([string]$text) { Write-Host $text }
function Step([int]$number, [string]$title) { Write-Host ''; Write-Host "[$number/$StepCount] $title" }
function Ok([string]$text) { Write-Host "  [ok]      $text" }
function Todo([string]$text) { Write-Host "  [to do]   $text"; $script:problems.Add($text) }
function Note([string]$text) { Write-Host "            $text" }

function Ask([string]$question) {
    # Yes unless the user types n. Only called when $interactive.
    $answer = Read-Host "  $question [Y/n]"
    return ($answer.Trim() -eq '' -or $answer.Trim().ToLowerInvariant().StartsWith('y'))
}

function Get-Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Test-Pinned([string]$path, [string]$sha256, [long]$size) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $item = Get-Item -LiteralPath $path
    if ($item.Length -ne $size) { return $false }
    return (Get-Sha256 $path) -eq $sha256
}

function Get-Download([string]$what, [string]$url, [string]$dest, [string]$sha256, [long]$size) {
    # Download to a temp name, verify, then move into place. A bad file is deleted, never kept.
    $temp = "$dest.download"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    Say "  downloading $what ($([math]::Round($size / 1MB, 1)) MB) from $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing
    } catch {
        throw "Could not download $what. Check that this PC is online (a proxy or firewall can block GitHub), then run this command again. ($($_.Exception.Message))"
    }
    if (-not (Test-Pinned $temp $sha256 $size)) {
        $got = if (Test-Path -LiteralPath $temp) { Get-Sha256 $temp } else { 'missing' }
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        throw "The downloaded $what is not the file this app expects, so it was deleted and nothing was installed.`n  expected SHA-256 $sha256`n  got               $got`nCheck your connection or a proxy, then run this command again."
    }
    Move-Item -LiteralPath $temp -Destination $dest -Force
}

function Get-EnvKeys([string]$path) {
    # Key names only. Values are never returned or printed by this script.
    $keys = @{}
    if (Test-Path -LiteralPath $path) {
        foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
            $trim = $line.Trim()
            if ($trim -eq '' -or $trim.StartsWith('#') -or -not $trim.Contains('=')) { continue }
            $name = $trim.Substring(0, $trim.IndexOf('=')).Trim()
            $keys[$name] = ($trim.Substring($trim.IndexOf('=') + 1).Trim() -ne '')
        }
    }
    return $keys
}

function Set-EnvKey([string]$path, [string]$name, [string]$value) {
    # Replace the key's line in place, or append it; every other line stays byte for byte.
    $lines = @()
    if (Test-Path -LiteralPath $path) { $lines = @(Get-Content -LiteralPath $path -Encoding UTF8) }
    $done = $false
    $out = foreach ($line in $lines) {
        if (-not $done -and $line.Trim() -match ('^' + [regex]::Escape($name) + '\s*=')) { $done = $true; "$name=$value" }
        else { $line }
    }
    if (-not $done) { $out = @($out) + "$name=$value" }
    [IO.File]::WriteAllLines($path, [string[]]$out, (New-Object Text.UTF8Encoding($false)))
}

function Get-GoIosVersion([string]$exe) {
    if (-not (Test-Path -LiteralPath $exe)) { return '' }
    try {
        $text = (& $exe --version 2>&1 | Out-String)
        if ($text -match '"version"\s*:\s*"([^"]+)"') { return $Matches[1] }
        if ($text -match '(\d+\.\d+\.\d+)') { return $Matches[1] }
    } catch { }
    return ''
}

function New-Shortcut([string]$path, [string]$target, [string]$arguments, [string]$description) {
    $shell = New-Object -ComObject WScript.Shell
    if ((Test-Path -LiteralPath $path) -and -not $ReplaceShortcut) {
        $existing = $shell.CreateShortcut($path)
        if ($existing.TargetPath -eq $target -and $existing.Arguments -eq $arguments -and
            $existing.WorkingDirectory -eq $projectRoot) {
            Ok "shortcut already current: $path"
            return
        }
        Say "  existing shortcut kept (it points at another copy of the app): $path"
        Note 'run with -ReplaceShortcut to point it at this folder.'
        return
    }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = (Join-Path $projectRoot 'web\logo.ico') + ',0'
    $shortcut.Description = $description
    $shortcut.Save()
    Ok "shortcut created: $path"
}

# ---- 0. USB recovery helper (optional, elevated once) --------------------------
$usbHelperScript = Join-Path $projectRoot 'video_drop\usb_helper\install_usb_helper.ps1'
$usbHelperRoot = Join-Path $env:ProgramData 'AutoIphoneUploader\usb-helper'

function Test-UsbHelperInstalled {
    # Read-only: the SYSTEM-side script is in place and the on-demand task is registered.
    if (-not (Test-Path -LiteralPath (Join-Path $usbHelperRoot 'usb_helper.ps1'))) { return $false }
    # A missing task makes schtasks write to stderr, which Windows PowerShell turns into a
    # terminating error under ErrorActionPreference=Stop when redirected; probe with Continue.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & schtasks.exe /Query /TN '\AutoIphoneUploader\UsbRecovery' 2>$null | Out-Null } finally { $ErrorActionPreference = $previous }
    return ($LASTEXITCODE -eq 0)
}

function Invoke-UsbHelperInstaller([string]$mode) {
    # The app never runs elevated; only this one step does, in its own window, with UAC consent.
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $modeArgs = if ($mode -eq 'install') { @('-Install', '-UserSid', $sid) } else { @('-Uninstall') }
    $isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        Say "  running the helper installer ($mode) in this administrator window"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $usbHelperScript @modeArgs
        $code = $LASTEXITCODE
    } else {
        Say "  Windows will ask for administrator rights once (to $mode the USB recovery helper)."
        $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$usbHelperScript`" $($modeArgs -join ' ')"
        $code = $proc.ExitCode
    }
    if ($code -ne 0) { throw "The USB recovery helper could not be ${mode}ed (exit $code); read the administrator window's output, then try again." }
}

function Install-UsbHelperNow {
    Invoke-UsbHelperInstaller 'install'
    if (Test-UsbHelperInstalled) { Ok 'USB recovery helper installed (a stalled iPhone connection now fixes itself)' }
    else { throw 'The helper installer finished but the helper is not in place; run it again and read its output.' }
}

if ($InstallUsbHelper -or $UninstallUsbHelper) {
    Say "Auto iPhone Uploader USB recovery helper ($projectRoot)"
    if ($InstallUsbHelper -and $UninstallUsbHelper) { throw 'Pass either -InstallUsbHelper or -UninstallUsbHelper, not both.' }
    if ($InstallUsbHelper) {
        Install-UsbHelperNow
    } else {
        Invoke-UsbHelperInstaller 'uninstall'
        if (-not (Test-UsbHelperInstalled)) { Ok 'USB recovery helper removed' }
    }
    exit 0
}

# ---- 1. Python ---------------------------------------------------------------
Say "Auto iPhone Uploader setup in $projectRoot"
if ($CheckOnly) { Say '  check only: nothing is downloaded, written or asked.' }
Step 1 'Python and the app''s packages'
if ($Python -ne '') {
    if (-not (Test-Path -LiteralPath $Python)) { throw "The Python given with -Python was not found at $Python. Pass the path to a python.exe, then run this command again." }
    $pythonExe = (Resolve-Path -LiteralPath $Python).Path
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python was not found. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH"), open a new PowerShell window, then run this command again.'
    }
    $pythonExe = $pythonCommand.Source
}
# The Windows installer ships the re-sign tool in its own interpreter (never the app's).
$resignPython = Join-Path $projectRoot 'python-resign\python.exe'
$resignExe = if (Test-Path -LiteralPath $resignPython) { $resignPython } else { $pythonExe }
$versionText = & $pythonExe -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor)); sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required; this PC has $versionText at $pythonExe. Install a newer Python from python.org, then run this command again."
}
Ok "Python $versionText at $pythonExe"
$pythonw = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
$launcher = if (Test-Path -LiteralPath $pythonw) { $pythonw } else { $pythonExe }

if ($CheckOnly) {
    # Windows PowerShell turns a native command's stderr into a terminating error under
    # ErrorActionPreference=Stop when it is redirected, so probe with Continue.
    $ErrorActionPreference = 'Continue'
    & $pythonExe -c 'import PIL, numpy, requests, tzdata' 2>$null
    $packagesOk = ($LASTEXITCODE -eq 0)
    # Single quotes inside: Windows PowerShell strips embedded double quotes from native arguments.
    & $resignExe -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pymobiledevice3') is None)" 2>$null
    $resignOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    if ($packagesOk) { Ok 'app packages installed' } else { Todo 'app packages missing: run this command without -CheckOnly' }
    if ($resignOk) { Ok 're-sign tool installed' } else { Todo 're-sign tool missing: run this command without -CheckOnly' }
} elseif ($PackagesBundled) {
    $ErrorActionPreference = 'Continue'
    & $pythonExe -c 'import PIL, numpy, requests, tzdata' 2>$null
    $packagesOk = ($LASTEXITCODE -eq 0)
    & $resignExe -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pymobiledevice3') is None)" 2>$null
    $resignOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    if (-not $packagesOk) { throw 'The packages that ship with this app are missing or damaged. Run the installer again to repair them.' }
    if (-not $resignOk) { throw 'The re-sign tool that ships with this app is missing or damaged. Run the installer again to repair it.' }
    Ok 'app packages and re-sign tool ship with this app'
} else {
    Say '  installing the app''s packages (this can take a minute)...'
    & $pythonExe -m pip install --disable-pip-version-check -q -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'The app''s packages did not install. Read the pip error above (usually no network or an old pip: python -m pip install --upgrade pip), then run this command again.' }
    Say '  installing the re-sign tool (used only as a separate process)...'
    & $pythonExe -m pip install --disable-pip-version-check -q -r (Join-Path $projectRoot 'requirements-resign.txt')
    if ($LASTEXITCODE -ne 0) { throw 'The re-sign tool did not install. Read the pip error above, then run this command again.' }
    Ok 'app packages and re-sign tool installed'
}

# ---- 2. go-ios ---------------------------------------------------------------
Step 2 'iPhone connector (go-ios 1.3.2, checked by SHA-256)'
$goIosOk = (Get-GoIosVersion $goIosExe) -eq $GoIosVersion
if ($goIosOk) {
    Ok "iPhone connector $GoIosVersion at $goIosExe"
} elseif ($CheckOnly -or $SkipDownloads) {
    Todo "iPhone connector $GoIosVersion is not at $goIosExe (the install command fetches it)"
} else {
    if (-not (Test-Pinned $goIosZip $GoIosSha256 $GoIosSize)) {
        Get-Download 'the iPhone connector' $GoIosUrl $goIosZip $GoIosSha256 $GoIosSize
    }
    Ok "download verified (SHA-256 $GoIosSha256)"
    $unpack = Join-Path $toolsDir 'unpack'
    if (Test-Path -LiteralPath $unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force }
    Expand-Archive -LiteralPath $goIosZip -DestinationPath $unpack -Force
    $found = Get-ChildItem -LiteralPath $unpack -Recurse -Filter 'ios.exe' | Select-Object -First 1
    if (-not $found) { throw 'The connector download did not contain ios.exe. Delete tools\go-ios and run this command again.' }
    Copy-Item -LiteralPath $found.FullName -Destination $goIosExe -Force
    Remove-Item -LiteralPath $unpack -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\LICENSE-go-ios') -Destination (Join-Path $toolsDir 'LICENSE') -Force
    $got = Get-GoIosVersion $goIosExe
    if ($got -ne $GoIosVersion) { throw "The unpacked connector reports version '$got', expected $GoIosVersion. Delete tools\go-ios and run this command again." }
    $goIosOk = $true
    Ok "iPhone connector $GoIosVersion unpacked to $goIosExe"
}

# ---- 3. WebDriverAgent (unsigned) ---------------------------------------------
Step 3 'Phone control app (WebDriverAgent 16.12.9, unsigned, checked by SHA-256)'
$wdaZipOk = Test-Pinned $wdaZip $WdaSha256 $WdaSize
$wdaOk = $wdaZipOk -and (Test-Path -LiteralPath $wdaIpa)
if ($wdaOk) {
    Ok "control app $WdaVersion ready to sign at $wdaIpa"
} elseif ($CheckOnly -or $SkipDownloads) {
    if ((Test-Path -LiteralPath $wdaZip) -and -not $wdaZipOk) {
        Todo "$wdaZip is not the pinned control app $WdaVersion (SHA-256 mismatch); the install command replaces it"
    } else {
        Todo "control app $WdaVersion is not at $wdaIpa (the install command fetches it)"
    }
} else {
    if (-not $wdaZipOk) {
        Get-Download 'the phone control app' $WdaUrl $wdaZip $WdaSha256 $WdaSize
    }
    Ok "download verified (SHA-256 $WdaSha256)"
    # The release zip holds WebDriverAgentRunner-Runner.app at its root; an .ipa is the
    # same app under Payload/. Repacked by video_drop/wda_ipa.py so entry names keep
    # forward slashes (Compress-Archive on older PowerShell writes backslashes).
    Push-Location $projectRoot
    try { & $pythonExe -m video_drop.wda_ipa $wdaZip "$wdaIpa.tmp" } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'The control app could not be repacked for Sideloadly. Read the error above, then run this command again.' }
    Move-Item -LiteralPath "$wdaIpa.tmp" -Destination $wdaIpa -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\LICENSE-WebDriverAgent') -Destination (Join-Path $wdaDir 'LICENSE') -Force
    $wdaOk = $true
    Ok "control app $WdaVersion ready to sign at $wdaIpa"
}

# ---- 4. .env -----------------------------------------------------------------
Step 4 'App settings (.env)'
$keys = Get-EnvKeys $envFile
if ($CheckOnly) {
    foreach ($name in 'GO_IOS_PATH', 'WDA_IPA') {
        if ($keys[$name]) { Ok "$name is set" } else { Todo "$name is not set in .env" }
    }
} else {
    if ($goIosOk) { Set-EnvKey $envFile 'GO_IOS_PATH' $goIosExe; Ok 'connector path saved' }
    if ($wdaOk) { Set-EnvKey $envFile 'WDA_IPA' $wdaIpa; Ok 'control app path saved' }
    $keys = Get-EnvKeys $envFile
}
if ($keys['PHONE_PASSCODE']) {
    Ok 'iPhone passcode saved (never shown)'
} else {
    $saved = $false
    if ($interactive) {
        Say '  The app unlocks the iPhone before each post, so it needs the passcode. It is typed only on the'
        Say '  lock screen and never shown or logged.'
        if (Ask 'Save your iPhone passcode now?') {
            $ErrorActionPreference = 'Continue'
            & $pythonExe (Join-Path $projectRoot 'scripts\set_passcode.py')
            $saved = ($LASTEXITCODE -eq 0)
            $ErrorActionPreference = 'Stop'
        }
    }
    if ($saved) { Ok 'iPhone passcode saved (never shown)' }
    else { Todo 'save your iPhone passcode: python scripts\set_passcode.py (hidden prompt, never shown)' }
}

# ---- 5. shortcuts --------------------------------------------------------------
Step 5 'Desktop shortcut and start at sign-in'
if ($DesktopDir -eq '') { $DesktopDir = [Environment]::GetFolderPath('DesktopDirectory') }
if ($StartupDir -eq '') { $StartupDir = [Environment]::GetFolderPath('Startup') }
$desktopShortcut = Join-Path $DesktopDir 'Auto iPhone Uploader.lnk'
$startupShortcut = Join-Path $StartupDir 'Auto iPhone Uploader (background).lnk'
$launchScript = '"' + (Join-Path $projectRoot 'launch_video_drop.py') + '"'
if ($CheckOnly) {
    if (Test-Path -LiteralPath $desktopShortcut) { Ok "Desktop shortcut: $desktopShortcut" } elseif (-not $NoDesktopShortcut) { Todo 'Desktop shortcut not created yet' }
    if (Test-Path -LiteralPath $startupShortcut) { Ok "starts at sign-in: $startupShortcut" } elseif (-not $NoStartup) { Todo 'start-at-sign-in entry not created yet' }
} else {
    if (-not $DesktopDir -or -not (Test-Path -LiteralPath $DesktopDir)) { throw 'Windows could not find your Desktop folder for the shortcut. Pass -DesktopDir <folder> and run this command again.' }
    if (-not $NoDesktopShortcut) { New-Shortcut $desktopShortcut $launcher $launchScript 'Open Auto iPhone Uploader' }
    if (-not $NoStartup) {
        if (-not $StartupDir -or -not (Test-Path -LiteralPath $StartupDir)) { throw 'Windows could not find your Startup folder. Pass -StartupDir <folder> or -NoStartup and run this command again.' }
        New-Shortcut $startupShortcut $launcher "$launchScript --no-browser" 'Start Auto iPhone Uploader and its iPhone connection at sign-in'
    }
}

# ---- 6. USB recovery helper (read-only here; installed on request) ---------------
Step 6 'USB recovery helper (recommended)'
if (Test-UsbHelperInstalled) {
    Ok 'USB recovery helper installed'
} else {
    $installed = $false
    if ($interactive) {
        Say '  When the iPhone''s USB connection stalls mid-upload, this small helper restarts Apple''s driver and'
        Say '  resets the port by itself instead of asking you to replug. Windows asks for administrator rights once.'
        if (Ask 'Install the USB recovery helper now?') {
            try { Install-UsbHelperNow; $installed = $true } catch { Say "  $($_.Exception.Message)" }
        }
    }
    if (-not $installed) { Todo 'USB recovery helper (recommended): run this command again with -InstallUsbHelper' }
}

# ---- 7. checklist -------------------------------------------------------------
Step 7 'Setup checklist (read-only; the same rows the app shows)'
$checklistReady = $true
if (-not $NoChecklist) {
    $ErrorActionPreference = 'Continue'
    & $pythonExe -m video_drop.setup_report
    $checklistReady = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    if (-not $checklistReady) { $problems.Add('the setup checklist has rows to do (see above); the app walks you through them') }
}

Say ''
if ($problems.Count -eq 0) {
    Say 'Everything is in place. Double-click the Desktop shortcut whenever you want the app.'
} else {
    Say "Still to do ($($problems.Count)):"
    foreach ($p in $problems) { Say "  - $p" }
    if (-not $CheckOnly) { Say 'Finished steps are skipped when you run this command again.' }
}
if (-not $CheckOnly -and -not $NoLaunch) {
    Say ''
    Say 'Opening Auto iPhone Uploader. Its first-run screens take you through connecting the iPhone and the'
    Say 'one-time signing with your Apple ID (Sideloadly), the one step Apple does not let a script do.'
    Start-Process -FilePath $launcher -ArgumentList $launchScript -WorkingDirectory $projectRoot | Out-Null
}
# A report with steps still to do is not a failure: real errors throw above. Without this the
# script would return the exit code of its last probe (e.g. the re-sign tool check), which
# failed the release build on a clean CI machine (2026-10-01).
exit 0
