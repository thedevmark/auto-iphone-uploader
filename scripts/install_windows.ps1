<#
.SYNOPSIS
  Set up Auto iPhone Uploader on this Windows PC.

.DESCRIPTION
  Idempotent: run it as often as you like. Each step is skipped when it is
  already done. It never touches the iPhone and never reads or prints your
  passcode. Steps:

    1. Python 3.11+ check, then the pinned packages from requirements.txt and
       the separate re-sign tool from requirements-resign.txt (pymobiledevice3,
       GPL-3.0, run only as a separate process; see third_party/NOTICE.md).
    2. go-ios 1.3.2 (MIT): the official Windows release zip is downloaded from
       GitHub, its SHA-256 checked against the value pinned below, and ios.exe
       unpacked into tools\go-ios\. Nothing is installed system-wide.
    3. WebDriverAgent 16.12.9 (BSD-3): the official UNSIGNED runner zip is
       downloaded, its SHA-256 checked, and repacked as wda\WebDriverAgent.ipa.
       You sign it with your own Apple ID in Sideloadly (the setup checklist
       has the steps); a signed build is never redistributed.
    4. .env: GO_IOS_PATH and WDA_IPA point at those files. Other keys are kept.
    5. A Desktop shortcut that opens the app, and a Startup entry that brings
       the app server and the phone link supervisor up at sign-in without
       opening a browser window.
    6. A read-only look at whether the USB recovery helper is installed; it is
       installed only on request (-InstallUsbHelper, administrator rights once).
    7. The setup checklist (python -m video_drop.setup_report).

.PARAMETER CheckOnly
  Report what is installed and run the checklist. Downloads nothing, writes nothing.
.PARAMETER SkipDownloads
  Do everything except the two downloads (offline). Existing files are still verified.
.PARAMETER NoStartup
  Do not create the Startup entry (the app then starts only from the shortcut).
.PARAMETER NoChecklist
  Skip the checklist at the end (used by CI).
.PARAMETER ReplaceShortcut
  Repoint an existing Desktop/Startup shortcut at this checkout.
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
    [switch]$NoStartup,
    [switch]$NoChecklist,
    [switch]$ReplaceShortcut,
    [switch]$InstallUsbHelper,
    [switch]$UninstallUsbHelper,
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

function Say([string]$text) { Write-Host $text }
function Ok([string]$text) { Write-Host "  [ok]      $text" }
function Todo([string]$text) { Write-Host "  [to do]   $text"; $script:problems.Add($text) }

function Get-Sha256([string]$path) {
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Test-Pinned([string]$path, [string]$sha256, [long]$size) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $item = Get-Item -LiteralPath $path
    if ($item.Length -ne $size) { return $false }
    return (Get-Sha256 $path) -eq $sha256
}

function Get-Download([string]$url, [string]$dest, [string]$sha256, [long]$size) {
    # Download to a temp name, verify, then move into place. A bad file is deleted, never kept.
    $temp = "$dest.download"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    Say "  downloading $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $temp -UseBasicParsing
    if (-not (Test-Pinned $temp $sha256 $size)) {
        $got = if (Test-Path -LiteralPath $temp) { Get-Sha256 $temp } else { 'missing' }
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        throw "Downloaded file does not match the pinned SHA-256.`n  expected $sha256`n  got      $got`nNothing was installed. Check your connection or a proxy, then run this script again."
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
        Say "  existing shortcut preserved (points elsewhere): $path"
        Say '  run with -ReplaceShortcut to point it at this checkout.'
        return
    }
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = (Join-Path $projectRoot 'web\logo.ico') + ',0'
    $shortcut.Description = $description
    $shortcut.Save()
    Ok "shortcut written: $path"
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
        Say "  running the helper installer ($mode) in this administrator session"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $usbHelperScript @modeArgs
        $code = $LASTEXITCODE
    } else {
        Say "  Windows will ask for administrator rights once (to $mode the USB recovery helper)."
        $proc = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru `
            -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$usbHelperScript`" $($modeArgs -join ' ')"
        $code = $proc.ExitCode
    }
    if ($code -ne 0) { throw "the USB recovery helper $mode step failed (exit $code); see the elevated window's output" }
}

if ($InstallUsbHelper -or $UninstallUsbHelper) {
    Say "Auto iPhone Uploader USB recovery helper ($projectRoot)"
    if ($InstallUsbHelper -and $UninstallUsbHelper) { throw 'pass either -InstallUsbHelper or -UninstallUsbHelper' }
    if ($InstallUsbHelper) {
        Invoke-UsbHelperInstaller 'install'
        if (Test-UsbHelperInstalled) { Ok 'USB recovery helper installed (task \AutoIphoneUploader\UsbRecovery, runs as SYSTEM on demand)' }
        else { throw 'the helper installer finished but the helper is not in place; run it again and read its output' }
    } else {
        Invoke-UsbHelperInstaller 'uninstall'
        if (-not (Test-UsbHelperInstalled)) { Ok 'USB recovery helper removed' }
    }
    exit 0
}

# ---- 1. Python ---------------------------------------------------------------
Say "Auto iPhone Uploader setup in $projectRoot"
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonExe = $pythonCommand.Source
$versionText = & $pythonExe -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor)); sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Found $versionText at $pythonExe."
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
    & $pythonExe -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('pymobiledevice3') is None)" 2>$null
    $resignOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    if ($packagesOk) { Ok 'Python packages from requirements.txt are installed' } else { Todo 'Python packages missing: run this script without -CheckOnly' }
    if ($resignOk) { Ok 're-sign tool (pymobiledevice3) is installed' } else { Todo 're-sign tool missing: pip install -r requirements-resign.txt' }
} else {
    Say 'Installing Python packages (requirements.txt)...'
    & $pythonExe -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed. Resolve the pip error above and run this script again.' }
    Say 'Installing the re-sign tool (requirements-resign.txt, separate process only)...'
    & $pythonExe -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'requirements-resign.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pymobiledevice3 installation failed. Resolve the pip error above and run this script again.' }
    Ok 'Python packages installed'
}

# ---- 2. go-ios ---------------------------------------------------------------
$goIosOk = (Get-GoIosVersion $goIosExe) -eq $GoIosVersion
if ($goIosOk) {
    Ok "go-ios $GoIosVersion at $goIosExe"
} elseif ($CheckOnly -or $SkipDownloads) {
    Todo "go-ios $GoIosVersion is not at $goIosExe (run the script without -SkipDownloads to fetch it)"
} else {
    if (-not (Test-Pinned $goIosZip $GoIosSha256 $GoIosSize)) {
        Get-Download $GoIosUrl $goIosZip $GoIosSha256 $GoIosSize
    }
    Ok "go-ios-win.zip verified (SHA-256 $GoIosSha256)"
    $unpack = Join-Path $toolsDir 'unpack'
    if (Test-Path -LiteralPath $unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force }
    Expand-Archive -LiteralPath $goIosZip -DestinationPath $unpack -Force
    $found = Get-ChildItem -LiteralPath $unpack -Recurse -Filter 'ios.exe' | Select-Object -First 1
    if (-not $found) { throw "go-ios-win.zip did not contain ios.exe" }
    Copy-Item -LiteralPath $found.FullName -Destination $goIosExe -Force
    Remove-Item -LiteralPath $unpack -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\LICENSE-go-ios') -Destination (Join-Path $toolsDir 'LICENSE') -Force
    $got = Get-GoIosVersion $goIosExe
    if ($got -ne $GoIosVersion) { throw "Unpacked ios.exe reports version '$got', expected $GoIosVersion" }
    $goIosOk = $true
    Ok "go-ios $GoIosVersion unpacked to $goIosExe"
}

# ---- 3. WebDriverAgent (unsigned) ---------------------------------------------
$wdaZipOk = Test-Pinned $wdaZip $WdaSha256 $WdaSize
$wdaOk = $wdaZipOk -and (Test-Path -LiteralPath $wdaIpa)
if ($wdaOk) {
    Ok "WebDriverAgent $WdaVersion (unsigned) at $wdaIpa"
} elseif ($CheckOnly -or $SkipDownloads) {
    if ((Test-Path -LiteralPath $wdaZip) -and -not $wdaZipOk) {
        Todo "$wdaZip is not the pinned WebDriverAgent $WdaVersion runner (SHA-256 mismatch); it will be replaced on a run without -SkipDownloads"
    } else {
        Todo "WebDriverAgent $WdaVersion is not at $wdaIpa (run the script without -SkipDownloads to fetch it)"
    }
} else {
    if (-not $wdaZipOk) {
        Get-Download $WdaUrl $wdaZip $WdaSha256 $WdaSize
    }
    Ok "WebDriverAgentRunner-Runner.zip verified (SHA-256 $WdaSha256)"
    # The release zip holds WebDriverAgentRunner-Runner.app at its root; an .ipa is the
    # same app under Payload/. Repacked by video_drop/wda_ipa.py so entry names keep
    # forward slashes (Compress-Archive on older PowerShell writes backslashes).
    Push-Location $projectRoot
    try { & $pythonExe -m video_drop.wda_ipa $wdaZip "$wdaIpa.tmp" } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Could not repack WebDriverAgent into an .ipa' }
    Move-Item -LiteralPath "$wdaIpa.tmp" -Destination $wdaIpa -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'third_party\LICENSE-WebDriverAgent') -Destination (Join-Path $wdaDir 'LICENSE') -Force
    $wdaOk = $true
    Ok "WebDriverAgent $WdaVersion repacked (unsigned) to $wdaIpa"
}

# ---- 4. .env -----------------------------------------------------------------
$keys = Get-EnvKeys $envFile
if ($CheckOnly) {
    foreach ($name in 'GO_IOS_PATH', 'WDA_IPA') {
        if ($keys[$name]) { Ok "$name is set in .env" } else { Todo "$name is not set in .env" }
    }
} else {
    if ($goIosOk) { Set-EnvKey $envFile 'GO_IOS_PATH' $goIosExe; Ok 'GO_IOS_PATH written to .env' }
    if ($wdaOk) { Set-EnvKey $envFile 'WDA_IPA' $wdaIpa; Ok 'WDA_IPA written to .env' }
    $keys = Get-EnvKeys $envFile
}
if ($keys['PHONE_PASSCODE']) { Ok 'PHONE_PASSCODE is set in .env (never shown)' }
else { Todo "PHONE_PASSCODE is not set: add the line PHONE_PASSCODE=<your iPhone passcode> to $envFile so the app can unlock the phone by itself" }

# ---- 5. shortcuts --------------------------------------------------------------
if ($DesktopDir -eq '') { $DesktopDir = [Environment]::GetFolderPath('DesktopDirectory') }
if ($StartupDir -eq '') { $StartupDir = [Environment]::GetFolderPath('Startup') }
$desktopShortcut = Join-Path $DesktopDir 'Auto iPhone Uploader.lnk'
$startupShortcut = Join-Path $StartupDir 'Auto iPhone Uploader (background).lnk'
$launchScript = '"' + (Join-Path $projectRoot 'launch_video_drop.py') + '"'
if ($CheckOnly) {
    if (Test-Path -LiteralPath $desktopShortcut) { Ok "Desktop shortcut: $desktopShortcut" } else { Todo 'Desktop shortcut not created yet' }
    if (Test-Path -LiteralPath $startupShortcut) { Ok "Startup entry: $startupShortcut" } elseif (-not $NoStartup) { Todo 'Startup entry not created yet' }
} else {
    if (-not $DesktopDir -or -not (Test-Path -LiteralPath $DesktopDir)) { throw 'Windows could not locate the Desktop folder for the shortcut.' }
    New-Shortcut $desktopShortcut $launcher $launchScript 'Open the local video review and phone upload app'
    if (-not $NoStartup) {
        if (-not $StartupDir -or -not (Test-Path -LiteralPath $StartupDir)) { throw 'Windows could not locate the Startup folder.' }
        New-Shortcut $startupShortcut $launcher "$launchScript --no-browser" 'Start Auto iPhone Uploader and its phone link supervisor at sign-in'
    }
}

# ---- 6. USB recovery helper (read-only here; installed on request) ---------------
if (Test-UsbHelperInstalled) { Ok 'USB recovery helper installed' }
else { Todo 'USB recovery helper not installed: run this script with -InstallUsbHelper (asks for administrator rights once) so a stalled USB link never needs a replug' }

# ---- 7. checklist -------------------------------------------------------------
if (-not $NoChecklist) {
    Say ''
    Say 'Setup checklist (read-only; the same rows the app shows):'
    & $pythonExe -m video_drop.setup_report
    if ($LASTEXITCODE -ne 0) { $problems.Add('the setup checklist has rows to do (see above)') }
}

Say ''
if ($problems.Count -eq 0) {
    Say 'Everything is in place. Double-click the Desktop shortcut to open the app.'
} else {
    Say "Still to do ($($problems.Count)):"
    foreach ($p in $problems) { Say "  - $p" }
    if (-not $CheckOnly) { Say 'Fix these, then run the script again; finished steps are skipped.' }
}
