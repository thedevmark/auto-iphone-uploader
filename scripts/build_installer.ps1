<#
.SYNOPSIS
  Build AutoiPhoneUploader-Setup-<version>.exe, the Windows installer that needs no Python on the PC.

.DESCRIPTION
  Used by the release workflow and runnable locally. It never runs the installer and never
  touches a phone. Steps:

    1. Download the official Windows embeddable Python (pinned URL and SHA-256 below).
    2. Stage the app's files (everything git tracks or would track, minus tests and CI).
    3. Make two interpreters from that download: python\ for the app (requirements.txt plus
       tkinter for the file pickers, copied from the build machine's matching Python) and
       python-resign\ for the re-sign tool (requirements-resign.txt: pymobiledevice3,
       GPL-3.0, run only as a separate process; see third_party\NOTICE.md).
    4. Prove the staged copy works: the bundled Python imports the app and its packages.
    5. Compile installer\AutoiPhoneUploader.iss with Inno Setup (iscc).

  go-ios and WebDriverAgent are not in the installer: setup downloads them on the user's PC,
  checked against the SHA-256 pinned in scripts\install_windows.ps1.

.PARAMETER Version
  The release tag, e.g. v1.0.0-rc.5. It names the output file.
.PARAMETER HostPython
  The Python that runs pip at build time. It must be 64-bit Windows and the same minor
  version as the embeddable Python below (3.14). Defaults to python on PATH.
.PARAMETER OutDir
  Where the Setup.exe is written. Default: dist\
.PARAMETER WorkDir
  Cache and staging folder. Default: build\installer\
.PARAMETER StageOnly
  Stage and verify, but do not compile (no Inno Setup needed).
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$HostPython = 'python',
    [string]$OutDir = '',
    [string]$WorkDir = '',
    [switch]$StageOnly
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'The installer is built on Windows.' }

# ---- pinned download (third_party/NOTICE.md) ---------------------------------
$PythonVersion = '3.14.8'
$PythonUrl     = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PythonSha256  = 'A93ABE456AB01BD96D7A085B3CDB6566B3063F4241360D114142FBDB07F0A310'
$PythonSize    = 12601679

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ($OutDir -eq '') { $OutDir = Join-Path $root 'dist' }
# Absolute before use: iscc resolves a relative OutputDir against the .iss folder, PowerShell against
# the current folder, so a relative -OutDir (the release workflow passes 'dist') sent the exe elsewhere.
$OutDir = [System.IO.Path]::GetFullPath($(if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path (Get-Location).Path $OutDir }))
if ($WorkDir -eq '') { $WorkDir = Join-Path $root 'build\installer' }
$cache = Join-Path $WorkDir 'cache'
$stage = Join-Path $WorkDir 'stage'
$embedZip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
$pythonMinor = ($PythonVersion -split '\.')[0..1] -join '.'
$pythonTag = $pythonMinor -replace '\.', ''

if ($Version -notmatch '^v?\d+\.\d+\.\d+([-.][0-9A-Za-z.]+)?$') { throw "Version '$Version' does not look like a release tag such as v1.0.0-rc.5." }
$numeric = '0.0.0.0'
if ($Version -match '^v?(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?') {
    $rc = if ($Matches[4]) { $Matches[4] } else { '0' }
    $numeric = '{0}.{1}.{2}.{3}' -f $Matches[1], $Matches[2], $Matches[3], $rc
}
$tag = if ($Version.StartsWith('v')) { $Version } else { "v$Version" }
$appVersion = $tag.TrimStart('v')

function Say([string]$text) { Write-Host $text }
function Test-Native([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)." } }

# ---- 1. embeddable Python, checked ----------------------------------------------
New-Item -ItemType Directory -Force -Path $cache | Out-Null
function Test-Pinned([string]$path) {
    return (Test-Path -LiteralPath $path) -and (Get-Item -LiteralPath $path).Length -eq $PythonSize -and
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToUpperInvariant() -eq $PythonSha256
}
if (-not (Test-Pinned $embedZip)) {
    Say "downloading $PythonUrl"
    $temp = "$embedZip.download"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $PythonUrl -OutFile $temp -UseBasicParsing
    Move-Item -LiteralPath $temp -Destination $embedZip -Force
    if (-not (Test-Pinned $embedZip)) {
        $got = (Get-FileHash -LiteralPath $embedZip -Algorithm SHA256).Hash
        Remove-Item -LiteralPath $embedZip -Force
        throw "The embeddable Python is not the pinned file (expected SHA-256 $PythonSha256, got $got). Nothing was built."
    }
}
Say "embeddable Python $PythonVersion verified (SHA-256 $PythonSha256)"

# ---- the build machine's Python must match the embeddable one --------------------
$hostInfo = (& $HostPython -c "import sys, struct; print(f'{sys.version_info.major}.{sys.version_info.minor} {struct.calcsize(chr(80))*8} {sys.base_prefix}')") -split ' ', 3
Test-Native 'Running the build Python'
if ($hostInfo[0] -ne $pythonMinor -or $hostInfo[1] -ne '64') {
    throw "The build Python must be 64-bit $pythonMinor (it is $($hostInfo[0]), $($hostInfo[1])-bit). Pass -HostPython <python.exe>."
}
$hostBase = $hostInfo[2].Trim()

# ---- 2. stage the app -------------------------------------------------------------
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Push-Location $root
try {
    $files = & git ls-files --cached --others --exclude-standard -z | ForEach-Object { $_ -split "`0" } | Where-Object { $_ }
    Test-Native 'git ls-files'
} finally { Pop-Location }
$kept = 0
foreach ($rel in $files) {
    if ($rel -match '^(tests|\.github|installer)/' -or $rel -eq 'pytest.ini' -or $rel -eq 'requirements-test.txt') { continue }
    $src = Join-Path $root $rel
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { continue }
    $dest = Join-Path $stage $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath $src -Destination $dest -Force
    $kept++
}
Say "staged $kept app files"
foreach ($required in 'launch_video_drop.py', 'web\logo.ico', 'LICENSE', 'third_party\NOTICE.md', 'scripts\install_windows.ps1', 'requirements.txt') {
    if (-not (Test-Path -LiteralPath (Join-Path $stage $required))) { throw "The staged app is missing $required." }
}

# ---- 3. two interpreters -------------------------------------------------------------
function New-Interpreter([string]$name, [string[]]$pth) {
    $dir = Join-Path $stage $name
    Expand-Archive -LiteralPath $embedZip -DestinationPath $dir -Force
    # A ._pth file puts Python in isolated mode: PYTHONPATH and the script's folder are
    # ignored, so every search path is listed here (relative to this file). "import site"
    # turns on site-packages.
    [IO.File]::WriteAllLines((Join-Path $dir "python$pythonTag._pth"), [string[]]$pth, (New-Object Text.UTF8Encoding($false)))
    New-Item -ItemType Directory -Force -Path (Join-Path $dir 'Lib\site-packages') | Out-Null
    return $dir
}
function Install-Packages([string]$dir, [string]$requirements, [bool]$wheelsOnly) {
    $target = Join-Path $dir 'Lib\site-packages'
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location', '--ignore-installed',
                 '--target', $target, '-r', (Join-Path $root $requirements))
    if ($wheelsOnly) { $pipArgs += '--only-binary=:all:' }
    & $HostPython @pipArgs
    Test-Native "pip install -r $requirements"
    # Console-script launchers hard-code the build machine's paths; nothing here uses them.
    foreach ($junk in 'bin', 'Scripts') {
        $path = Join-Path $target $junk
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
}

$appPython = New-Interpreter 'python' @("python$pythonTag.zip", '.', 'Lib\site-packages', '..', 'import site')
Install-Packages $appPython 'requirements.txt' $true

# tkinter backs the file and folder pickers (scripts\pick_video.py, pick_folder.py); the
# embeddable Python leaves it out, so copy it from the build machine's matching Python.
$hostDlls = Join-Path $hostBase 'DLLs'
# Python 3.11-3.13 ship Tcl/Tk 8.6 (tcl86t.dll, tk86t.dll); 3.14 can ship 9.0 (tcl90.dll, tcl9tk90.dll).
# Copy whichever this build Python has; _tkinter.pyd plus one tcl and one tk DLL are required.
$tkFiles = @(Get-ChildItem -LiteralPath $hostDlls -File | Where-Object { $_.Name -match '^(_tkinter\.pyd|tcl\d+t?\.dll|(tcl\d+)?tk\d+t?\.dll|zlib1\.dll)$' })
foreach ($need in '^_tkinter\.pyd$', '^tcl\d+t?\.dll$', '^(tcl\d+)?tk\d+t?\.dll$') {
    if (-not ($tkFiles | Where-Object { $_.Name -match $need })) {
        throw "The build Python has no file matching $need in $hostDlls; use a Python with tcl/tk (the python.org installer, or setup-python)."
    }
}
foreach ($file in $tkFiles) { Copy-Item -LiteralPath $file.FullName -Destination $appPython -Force }
Copy-Item -LiteralPath (Join-Path $hostBase 'Lib\tkinter') -Destination (Join-Path $appPython 'Lib\site-packages\tkinter') -Recurse -Force
foreach ($src in @(Get-ChildItem -LiteralPath (Join-Path $hostBase 'tcl') -Directory | Where-Object { $_.Name -match '^(tcl|tk)\d' })) {
    Copy-Item -LiteralPath $src.FullName -Destination (Join-Path $appPython $src.Name) -Recurse -Force
}
Get-ChildItem -LiteralPath $appPython -Directory | Where-Object { $_.Name -match '^tk\d' } | ForEach-Object { Remove-Item -LiteralPath (Join-Path $_.FullName 'demos') -Recurse -Force -ErrorAction SilentlyContinue }

# The re-sign tool gets its own interpreter and never sees the app: no ".." on its path.
$resignPython = New-Interpreter 'python-resign' @("python$pythonTag.zip", '.', 'Lib\site-packages', 'import site')
Install-Packages $resignPython 'requirements-resign.txt' $false

# ---- 4. prove the staged copy works (nothing is installed; no phone is touched) ----
$neutral = Join-Path $WorkDir 'neutral'
New-Item -ItemType Directory -Force -Path $neutral | Out-Null
Push-Location $neutral
try {
    # Tcl/Tk is found the way scripts\pick_video.py finds it: TCL_LIBRARY and TK_LIBRARY next to python.exe.
    $check = "import PIL, numpy, requests, tzdata, tkinter, _tkinter, os, sys, video_drop.server; " +
             "b = os.path.dirname(sys.executable); os.environ['TCL_LIBRARY'] = os.path.join(b, 'tcl8.6'); os.environ['TK_LIBRARY'] = os.path.join(b, 'tk8.6'); " +
             "tkinter.Tcl().eval('info patchlevel'); " +
             "assert os.path.samefile(os.path.dirname(os.path.dirname(video_drop.server.__file__)), r'$stage'); print('app python ok')"
    & (Join-Path $appPython 'python.exe') -c $check
    Test-Native 'The bundled Python could not import the app'
    & (Join-Path $resignPython 'python.exe') -c "import pymobiledevice3.lockdown, pymobiledevice3.services.misagent; print('re-sign python ok')"
    Test-Native 'The bundled re-sign Python could not import pymobiledevice3'
    & (Join-Path $resignPython 'python.exe') -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('video_drop') is None else 1)"
    Test-Native 'The re-sign Python can see the app'
} finally { Pop-Location }
foreach ($pycache in Get-ChildItem -LiteralPath $stage -Recurse -Directory -Filter '__pycache__' -Force) {
    Remove-Item -LiteralPath $pycache.FullName -Recurse -Force -ErrorAction SilentlyContinue
}
$staged = (Get-ChildItem -LiteralPath $stage -Recurse -File | Measure-Object -Property Length -Sum).Sum
Say ("staged payload {0:N0} MB at {1}" -f ($staged / 1MB), $stage)
if ($StageOnly) { return }

# ---- 5. compile -------------------------------------------------------------------------
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($dir in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if (-not $dir) { continue }
        $candidate = Join-Path $dir 'Inno Setup 6\ISCC.exe'
        if (Test-Path -LiteralPath $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) { throw 'Inno Setup 6 (iscc) was not found. Install it (winget install JRSoftware.InnoSetup, or choco install innosetup), then run this again.' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
& $iscc "/DAppVersion=$appVersion" "/DTag=$tag" "/DNumericVersion=$numeric" "/DStage=$stage" "/DOutDir=$OutDir" (Join-Path $root 'installer\AutoiPhoneUploader.iss')
Test-Native 'iscc'
$exe = Join-Path $OutDir "AutoiPhoneUploader-Setup-$tag.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "iscc finished but $exe was not written." }
$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
Say ''
Say ("built {0} ({1:N1} MB)" -f $exe, ((Get-Item -LiteralPath $exe).Length / 1MB))
Say "SHA-256 $hash"
