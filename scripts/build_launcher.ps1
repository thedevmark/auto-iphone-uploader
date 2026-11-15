<#
.SYNOPSIS
  Build "Auto iPhone Uploader.exe", the app's Windows launcher, from installer\launcher\Launcher.cs.

.DESCRIPTION
  Uses the C# compiler that ships with Windows (.NET Framework 4), so nothing is installed.
  The exe goes next to launch_video_drop.py (or into -OutDir) and carries web\logo.ico.

.PARAMETER OutDir
  Folder to write the exe into; defaults to the project root.
#>
param([string]$OutDir = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ($OutDir -eq '') { $OutDir = $root }
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { throw "The .NET Framework 4 C# compiler was not found at $csc." }
$exe = Join-Path $OutDir 'Auto iPhone Uploader.exe'
& $csc /nologo /target:winexe /optimize+ "/out:$exe" "/win32icon:$(Join-Path $root 'web\logo.ico')" /reference:System.Windows.Forms.dll (Join-Path $root 'installer\launcher\Launcher.cs')
if ($LASTEXITCODE -ne 0) { throw "csc failed with exit code $LASTEXITCODE." }
Write-Output $exe
