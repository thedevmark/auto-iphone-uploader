# Offline text recognition with Windows' built-in OCR engine (Windows.Media.Ocr).
# Usage: powershell -NoProfile -File winocr.ps1 <image.png>   ->  JSON lines: {"text","x","y","w","h"} in image pixels.
#        powershell -NoProfile -File winocr.ps1 -Probe          ->  {"language": "<tag>"} or exit 2 with no engine.
param([string]$Path, [switch]$Probe)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(); $task.Result }
# The apps are driven by their English labels, so prefer an English engine over the profile language.
$engine = $null
foreach ($tag in 'en-US', 'en-GB', 'en') {
  $language = New-Object Windows.Globalization.Language $tag
  if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($language)) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language); break }
}
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { [Console]::Error.WriteLine('No Windows OCR language is installed'); exit 2 }
if ($Probe) { @{ language = $engine.RecognizerLanguage.LanguageTag } | ConvertTo-Json -Compress; exit 0 }
if (-not $Path) { [Console]::Error.WriteLine('Give an image path or -Probe'); exit 1 }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Resolve-Path $Path).Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) {
  $xs = $line.Words | ForEach-Object { $_.BoundingRect.X }; $ys = $line.Words | ForEach-Object { $_.BoundingRect.Y }
  $x2 = $line.Words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width }; $y2 = $line.Words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height }
  $x = ($xs | Measure-Object -Minimum).Minimum; $y = ($ys | Measure-Object -Minimum).Minimum
  @{ text = $line.Text; x = [int]$x; y = [int]$y; w = [int](($x2 | Measure-Object -Maximum).Maximum - $x); h = [int](($y2 | Measure-Object -Maximum).Maximum - $y) } | ConvertTo-Json -Compress
}
