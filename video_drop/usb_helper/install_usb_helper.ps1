<#
.SYNOPSIS
  Install or remove the USB recovery helper (elevated part). Called by
  scripts\install_windows.ps1 -InstallUsbHelper / -UninstallUsbHelper, which
  asks for administrator rights through UAC; run it directly only from an
  administrator PowerShell.

.DESCRIPTION
  Idempotent. -Install copies usb_helper.ps1 into
  %ProgramData%\AutoIphoneUploader\usb-helper, locks the folder down (SYSTEM
  and Administrators may write; the installing user may drop request files and
  read results; other users read only), writes config.json with the installing
  user's SID, and registers the on-demand scheduled task
  \AutoIphoneUploader\UsbRecovery (runs as SYSTEM, no trigger, one instance,
  3-minute limit) whose security descriptor lets exactly that user start it.
  -Uninstall removes the task and the folder. -Status prints what is there.
  Nothing here touches the iPhone.

.PARAMETER UserSid
  SID of the (non-administrator) account that runs the app. Required for -Install.
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [string]$UserSid = ''
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$HelperVersion = 1
$TaskPath = '\AutoIphoneUploader\'
$TaskName = 'UsbRecovery'
$Root = Join-Path $env:ProgramData 'AutoIphoneUploader\usb-helper'
$Source = Join-Path $PSScriptRoot 'usb_helper.ps1'
$SystemSid = 'S-1-5-18'
$AdminsSid = 'S-1-5-32-544'
$UsersSid = 'S-1-5-32-545'

function Say([string]$text) { Write-Host $text }

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-ReparsePoint([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    return [bool]((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Set-Acl-Strict([string]$path, [string[]]$grants) {
    # Owner Administrators, inheritance cut, then exactly the grants given.
    & icacls.exe $path /setowner "*$AdminsSid" /T /C /Q | Out-Null
    & icacls.exe $path /inheritance:r /Q | Out-Null
    $icaclsArgs = @($path)
    foreach ($grant in $grants) { $icaclsArgs += @('/grant:r', $grant) }
    $icaclsArgs += '/Q'
    $out = & icacls.exe @icaclsArgs 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "icacls failed for ${path}: $out" }
}

function Get-Task {
    return Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Install-Helper {
    if (-not $UserSid) { throw '-UserSid is required for -Install (the SID of the account that runs the app)' }
    if ($UserSid -notmatch '^S-1-5-21-\d+-\d+-\d+-\d+$') {
        throw "UserSid must be a plain user account SID (S-1-5-21-...), not '$UserSid'"
    }
    if (-not (Test-Path -LiteralPath $Source)) { throw "helper source missing: $Source" }
    foreach ($path in @((Split-Path -Parent $Root), $Root, (Join-Path $Root 'requests'), (Join-Path $Root 'results'),
                        (Join-Path $Root 'usb_helper.ps1'), (Join-Path $Root 'config.json'))) {
        if (Test-ReparsePoint $path) { throw "refusing to install: $path is a junction or symlink" }
    }
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'requests') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'results') | Out-Null

    $target = Join-Path $Root 'usb_helper.ps1'
    Copy-Item -LiteralPath $Source -Destination $target -Force
    $sourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $sourceHash) { throw 'helper copy does not match its source' }

    $installedAt = (Get-Date).ToString('o')
    $configPath = Join-Path $Root 'config.json'
    if (Test-Path -LiteralPath $configPath) {
        try { $installedAt = [string](Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json).installedAt } catch { }
    }
    $config = @{ version = $HelperVersion; userSid = $UserSid; installedAt = $installedAt; updatedAt = (Get-Date).ToString('o');
                 scriptSha256 = $sourceHash } | ConvertTo-Json
    [IO.File]::WriteAllText($configPath, $config, (New-Object Text.UTF8Encoding($false)))

    # Folder: SYSTEM + Administrators full, everyone else read. The script, config and
    # log inherit that, so only an administrator can change what SYSTEM executes.
    Set-Acl-Strict $Root @("*${SystemSid}:(OI)(CI)F", "*${AdminsSid}:(OI)(CI)F", "*${UsersSid}:(OI)(CI)RX")
    # requests: the app's account may create and remove its own request files.
    Set-Acl-Strict (Join-Path $Root 'requests') @("*${SystemSid}:(OI)(CI)F", "*${AdminsSid}:(OI)(CI)F",
                                                  "*${UsersSid}:(OI)(CI)RX", "*${UserSid}:(OI)(CI)M")
    # results: written by SYSTEM, read by everyone; the helper sweeps old ones itself.
    Set-Acl-Strict (Join-Path $Root 'results') @("*${SystemSid}:(OI)(CI)F", "*${AdminsSid}:(OI)(CI)F", "*${UsersSid}:(OI)(CI)RX")

    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$target`""
    $principal = New-ScheduledTaskPrincipal -UserId $SystemSid -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -Compatibility Win8
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Principal $principal `
        -Settings $settings -Description 'Auto iPhone Uploader: restart Apple Mobile Device Service or the iPhone USB node on request (fixed commands only)' `
        -Force | Out-Null

    # Only SYSTEM, Administrators and the app's account may read/start the task.
    $scheduler = New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $task = $scheduler.GetFolder($TaskPath.TrimEnd('\')).GetTask($TaskName)
    $task.SetSecurityDescriptor("D:(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$UserSid)", 0)

    Say "  [ok]      USB recovery helper installed in $Root"
    Say "  [ok]      scheduled task $TaskPath$TaskName registered (runs as SYSTEM on demand; start right: $UserSid)"
}

function Uninstall-Helper {
    $task = Get-Task
    if ($task) {
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
        Say "  [ok]      scheduled task $TaskPath$TaskName removed"
    } else {
        Say '  [ok]      scheduled task was not registered'
    }
    try {
        $scheduler = New-Object -ComObject Schedule.Service
        $scheduler.Connect()
        $folder = $scheduler.GetFolder('\')
        if (@($folder.GetFolder($TaskPath.TrimEnd('\')).GetTasks(1)).Count -eq 0) { $folder.DeleteFolder($TaskPath.TrimEnd('\'), 0) }
    } catch { }
    if (Test-Path -LiteralPath $Root) {
        if (Test-ReparsePoint $Root) { throw "refusing to remove: $Root is a junction or symlink" }
        Remove-Item -LiteralPath $Root -Recurse -Force
        Say "  [ok]      $Root removed"
    } else {
        Say '  [ok]      helper folder was not present'
    }
}

function Show-Status {
    $script = Test-Path -LiteralPath (Join-Path $Root 'usb_helper.ps1')
    $task = [bool](Get-Task)
    Say ("  helper script: {0}" -f $(if ($script) { 'present' } else { 'missing' }))
    Say ("  scheduled task {0}{1}: {2}" -f $TaskPath, $TaskName, $(if ($task) { 'registered' } else { 'missing' }))
    if (-not ($script -and $task)) { exit 1 }
}

if ($Status) { Show-Status; exit 0 }
if (-not ($Install -or $Uninstall)) { throw 'pass -Install -UserSid <sid>, -Uninstall or -Status' }
if (-not (Test-Admin)) { throw 'administrator rights are required (scripts\install_windows.ps1 -InstallUsbHelper asks for them through UAC)' }
if ($Uninstall) { Uninstall-Helper } else { Install-Helper }
exit 0
