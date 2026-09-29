param(
    [switch]$CheckOnly,
    [switch]$ReplaceShortcut
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'This setup script runs on Windows. On another system, use python -m video_drop.server.'
}

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonExe = $pythonCommand.Source
$versionText = & $pythonExe -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor)); sys.exit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required. Found $versionText."
}

if ($CheckOnly) {
    Write-Host "Python $versionText found at $pythonExe"
    Write-Host "Project found at $projectRoot"
    return
}

& $pythonExe -m pip install -r (Join-Path $projectRoot 'requirements-test.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Python dependency installation failed. Resolve the pip error above and run this script again.'
}

$pythonw = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
$launcher = if (Test-Path -LiteralPath $pythonw) { $pythonw } else { $pythonExe }
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop)) {
    throw 'Windows could not locate the Desktop folder for the shortcut.'
}

$shortcutPath = Join-Path $desktop 'Automated iPhone Social Media Uploads.lnk'
$shell = New-Object -ComObject WScript.Shell
$arguments = '"' + (Join-Path $projectRoot 'launch_video_drop.py') + '"'
if ((Test-Path -LiteralPath $shortcutPath) -and -not $ReplaceShortcut) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -ne $launcher -or $existing.Arguments -ne $arguments -or
        $existing.WorkingDirectory -ne $projectRoot) {
        Write-Host "Existing Desktop shortcut preserved: $shortcutPath"
        Write-Host 'Run with -ReplaceShortcut only if you want it to point to this checkout.'
        return
    }
}
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$launcher,0"
$shortcut.Description = 'Open the local video review and phone upload app'
$shortcut.Save()

Write-Host "Desktop shortcut ready: $shortcutPath"
Write-Host 'SideTap and iPhone setup are separate; follow https://github.com/ucsandman/SideTap/blob/main/docs/setup-windows.md before phone uploads.'
