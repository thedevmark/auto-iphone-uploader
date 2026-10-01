<#
.SYNOPSIS
  USB recovery helper for Auto iPhone Uploader. Runs as SYSTEM from an on-demand
  scheduled task; executes exactly two fixed repairs for Apple iPhones.

.DESCRIPTION
  The app runs without administrator rights. When the iPhone's USB data link
  stalls (docs/link-root-cause.md) two repairs need elevation: restarting
  Apple Mobile Device Service and restarting the iPhone's USB device node.
  This script is the whole elevated surface. It takes NO parameters and reads
  NO arguments: a request is a file named <16 hex chars>.json in the requests
  folder, holding {"command": "<one of the two names below>", "nonce": ..,
  "issued": <unix seconds>}. Every field is checked against a fixed table;
  nothing from the file ever reaches a command line.

  Accepted only when ALL of these hold:
    - the file is a plain file (no reparse point), under 1 KB, named as a nonce
    - its NTFS owner is the SID recorded in config.json by the installer
    - "issued" is within the last 120 seconds
    - "command" is exactly "restart-apple-service" or "restart-usb-device"
    - at least 30 seconds passed since this helper's previous action
  The result goes to results\<nonce>.json; the request is deleted either way.

  Installed by video_drop\usb_helper\install_usb_helper.ps1 (elevated, via
  scripts\install_windows.ps1 -InstallUsbHelper) into
  %ProgramData%\AutoIphoneUploader\usb-helper, where only SYSTEM and
  Administrators can write. Security notes: docs/usb-recovery-helper.md.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$RequestsDir = Join-Path $Root 'requests'
$ResultsDir  = Join-Path $Root 'results'
$LogFile     = Join-Path $Root 'usb-helper.log'
$StampFile   = Join-Path $Root 'last-action.txt'
$ConfigFile  = Join-Path $Root 'config.json'

$MaxRequestAgeSeconds = 120
$MinSecondsBetweenActions = 30
$MaxRequestBytes = 1024
$AppleServiceName = 'Apple Mobile Device Service'
$AppleServiceImage = 'AppleMobileDeviceService.exe'
# Every iPhone enumerates as USB\VID_05AC&PID_12A8\<serial>; its interfaces carry &MI_nn.
$IphoneInstancePrefix = 'USB\VID_05AC&PID_12A8\'

function Write-Log([string]$text) {
    try {
        if ((Test-Path -LiteralPath $LogFile) -and (Get-Item -LiteralPath $LogFile).Length -gt 1MB) {
            Set-Content -LiteralPath $LogFile -Value @() -Encoding UTF8
        }
        Add-Content -LiteralPath $LogFile -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text) -Encoding UTF8
    } catch { }
}

function Write-Result([string]$nonce, [bool]$ok, [string]$command, [string]$detail, [datetime]$started) {
    $payload = @{
        nonce    = $nonce
        ok       = $ok
        command  = $command
        detail   = $detail
        started  = $started.ToString('o')
        finished = (Get-Date).ToString('o')
    } | ConvertTo-Json -Compress
    $tmp = Join-Path $ResultsDir ("{0}.tmp" -f $nonce)
    [IO.File]::WriteAllText($tmp, $payload, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination (Join-Path $ResultsDir ("{0}.json" -f $nonce)) -Force
}

function Get-OwnerSid([string]$path) {
    return (Get-Acl -LiteralPath $path).GetOwner([System.Security.Principal.SecurityIdentifier]).Value
}

function Test-RateLimit {
    if (-not (Test-Path -LiteralPath $StampFile)) { return $true }
    try {
        $last = [datetime]::ParseExact((Get-Content -LiteralPath $StampFile -Raw).Trim(), 'o', $null)
        return ((Get-Date) - $last).TotalSeconds -ge $MinSecondsBetweenActions
    } catch { return $true }
}

function Set-RateStamp {
    [IO.File]::WriteAllText($StampFile, (Get-Date).ToString('o'), (New-Object Text.UTF8Encoding($false)))
}

# ---- the two repairs: fixed commands, no caller input ------------------------------

function Restart-AppleService {
    $service = Get-Service -Name $AppleServiceName -ErrorAction SilentlyContinue
    if (-not $service) { return @{ ok = $false; detail = "service '$AppleServiceName' is not installed" } }
    $steps = New-Object System.Collections.Generic.List[string]
    if ($service.Status -ne 'Stopped') {
        Stop-Service -Name $AppleServiceName -Force -NoWait -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(20)
        while ((Get-Date) -lt $deadline) {
            $service.Refresh()
            if ($service.Status -eq 'Stopped') { break }
            Start-Sleep -Milliseconds 500
        }
        $service.Refresh()
        if ($service.Status -ne 'Stopped') {
            # A service whose device thread is wedged does not honour the stop request;
            # the process image name is fixed here, never taken from the request.
            $steps.Add('stop timed out after 20s; killing the service process')
            & "$env:SystemRoot\System32\taskkill.exe" /F /IM $AppleServiceImage 2>&1 | Out-Null
            $deadline = (Get-Date).AddSeconds(10)
            while ((Get-Date) -lt $deadline) {
                $service.Refresh()
                if ($service.Status -eq 'Stopped') { break }
                Start-Sleep -Milliseconds 500
            }
        } else {
            $steps.Add('stopped')
        }
    } else {
        $steps.Add('was already stopped')
    }
    Start-Service -Name $AppleServiceName
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        $service.Refresh()
        if ($service.Status -eq 'Running') { break }
        Start-Sleep -Milliseconds 500
    }
    $service.Refresh()
    $steps.Add("started: $($service.Status)")
    return @{ ok = ($service.Status -eq 'Running'); detail = ($steps -join '; ') }
}

function Restart-IphoneUsbDevice {
    $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object {
        $_.InstanceId.StartsWith($IphoneInstancePrefix, [StringComparison]::OrdinalIgnoreCase) -and
        $_.InstanceId -notmatch '&MI_'
    })
    if ($devices.Count -eq 0) { return @{ ok = $false; detail = 'no iPhone USB device node is present' } }
    $steps = New-Object System.Collections.Generic.List[string]
    $allOk = $true
    foreach ($device in $devices) {
        # The instance id comes from Windows' own enumeration above, never from the request.
        $id = $device.InstanceId
        $serial = $id.Substring($IphoneInstancePrefix.Length)
        $output = & "$env:SystemRoot\System32\pnputil.exe" /restart-device "$id" 2>&1 | Out-String
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            $steps.Add("restarted USB node for serial ending $($serial.Substring([Math]::Max(0, $serial.Length - 4)))")
            continue
        }
        # pnputil /restart-device needs Windows 10 2004+; disable/enable is the older equivalent.
        $steps.Add("pnputil exit $code ($($output.Trim() -replace '\s+', ' ')); trying disable/enable")
        try {
            Disable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop
            Start-Sleep -Seconds 2
            Enable-PnpDevice -InstanceId $id -Confirm:$false -ErrorAction Stop
            $steps.Add('disable/enable done')
        } catch {
            $allOk = $false
            $steps.Add("disable/enable failed: $($_.Exception.Message)")
        }
    }
    return @{ ok = $allOk; detail = ($steps -join '; ') }
}

$Commands = @{
    'restart-apple-service' = { Restart-AppleService }
    'restart-usb-device'    = { Restart-IphoneUsbDevice }
}

# ---- main -------------------------------------------------------------------------------

try {
    $config = Get-Content -LiteralPath $ConfigFile -Raw | ConvertFrom-Json
    $allowedSid = [string]$config.userSid
    if (-not $allowedSid) { throw 'config.json has no userSid' }
} catch {
    Write-Log "refusing to run: $($_.Exception.Message)"
    exit 2
}

# Old results are the helper's own; sweep them so the folder never grows.
try {
    Get-ChildItem -LiteralPath $ResultsDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddMinutes(-10) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch { }

$requests = @(Get-ChildItem -LiteralPath $RequestsDir -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -First 5)
if ($requests.Count -eq 0) {
    Write-Log 'started with no request; nothing done'
    exit 0
}

foreach ($file in $requests) {
    $started = Get-Date
    $nonce = ''
    $command = ''
    $verdict = @{ ok = $false; detail = '' }
    try {
        if ($file.Name -notmatch '^([0-9a-f]{16})\.json$') { throw "request name is not a nonce: $($file.Name)" }
        $nonce = $Matches[1]
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'request is a reparse point' }
        if ($file.Length -gt $MaxRequestBytes) { throw "request is $($file.Length) bytes; limit $MaxRequestBytes" }
        $owner = Get-OwnerSid $file.FullName
        if ($owner -ne $allowedSid) { throw "request owner $owner is not the installing user" }
        $body = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
        $command = [string]$body.command
        if (-not $Commands.ContainsKey($command)) { throw "unknown command '$command'" }
        if ([string]$body.nonce -ne $nonce) { throw 'nonce in the body does not match the file name' }
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$body.issued
        if ($age -lt -30 -or $age -gt $MaxRequestAgeSeconds) { throw "request is stale ($([int]$age)s old)" }
        if (-not (Test-RateLimit)) { throw "rate limited: one action per $MinSecondsBetweenActions s" }
        Set-RateStamp
        Write-Log "running $command for request $nonce"
        $verdict = & $Commands[$command]
        Write-Log "$command -> ok=$($verdict.ok): $($verdict.detail)"
    } catch {
        $verdict = @{ ok = $false; detail = $_.Exception.Message }
        Write-Log "refused request $($file.Name): $($_.Exception.Message)"
    } finally {
        try { Remove-Item -LiteralPath $file.FullName -Force -ErrorAction SilentlyContinue } catch { }
    }
    if ($nonce) {
        try { Write-Result $nonce ([bool]$verdict.ok) $command ([string]$verdict.detail) $started } catch { Write-Log "could not write result: $($_.Exception.Message)" }
    }
}
exit 0
