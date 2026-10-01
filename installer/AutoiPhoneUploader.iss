; Inno Setup script for AutoiPhoneUploader-Setup-<version>.exe. Built by scripts\build_installer.ps1,
; which passes AppVersion, Tag, NumericVersion, Stage (the staged app + bundled Python) and OutDir.
; Installs for the current user only (no administrator rights) into
; %LOCALAPPDATA%\Programs\Auto iPhone Uploader.

#ifndef AppVersion
  #error Build this with scripts\build_installer.ps1
#endif

#define AppName "Auto iPhone Uploader"
#define AppUrl "https://github.com/thedevmark/auto-iphone-uploader"

[Setup]
AppId={{6F1C2B8E-3A47-4D55-9B0E-5D7A1C94E2A8}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=thedevmark
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#NumericVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=AutoiPhoneUploader-Setup-{#Tag}
SetupIconFile={#Stage}\web\logo.ico
UninstallDisplayIcon={app}\web\logo.ico
LicenseFile={#Stage}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
UsePreviousAppDir=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launch_video_drop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\web\logo.ico"
Name: "{autoprograms}\{#AppName} setup (passcode, USB helper)"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "{code:SetupArgs}"; WorkingDir: "{app}"; IconFilename: "{app}\web\logo.ico"; Comment: "Re-run setup: downloads anything missing and offers to save your iPhone passcode and install the USB recovery helper"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launch_video_drop.py"""; WorkingDir: "{app}"; IconFilename: "{app}\web\logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launch_video_drop.py"""; WorkingDir: "{app}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only what the installer or setup created. The .state folder (drafts, settings, the link's
; state, re-sign material) is never listed here, so it is left on disk.
Type: files; Name: "{userstartup}\{#AppName} (background).lnk"
Type: files; Name: "{app}\.env"
Type: filesandordirs; Name: "{app}\tools"
Type: filesandordirs; Name: "{app}\wda"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\python-resign"
Type: filesandordirs; Name: "{app}\video_drop"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\lib"
Type: filesandordirs; Name: "{app}\maps"
Type: filesandordirs; Name: "{app}\web"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\third_party"
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
function SetupArgs(Param: String): String;
begin
  Result := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}') + '\scripts\install_windows.ps1" -Python "' +
    ExpandConstant('{app}') + '\python\python.exe" -PackagesBundled -NoDesktopShortcut -ReplaceShortcut -NoLaunch';
end;

// Stops this folder's own Python processes (the app server and the phone link), and nothing
// else, so files can be replaced or removed. The iPhone connector (ios.exe) is left alone.
procedure StopAppProcesses;
var
  Dir: String;
  ResultCode: Integer;
begin
  Dir := ExpandConstant('{app}');
  StringChangeEx(Dir, '''', '''''', True);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith(''' +
    Dir + '\python'', ''OrdinalIgnoreCase'') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopAppProcesses;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Downloading the iPhone connector and the phone control app (each checked against a pinned SHA-256)...';
    WizardForm.ProgressGauge.Style := npbstMarquee;
    // -NoPrompt: nothing is asked here. The Start menu entry "setup (passcode, USB helper)"
    // asks for those. -NoChecklist: the app shows the same checklist on its first run.
    if (not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), SetupArgs('') + ' -NoPrompt -NoChecklist',
        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
      SuppressibleMsgBox('The app is installed, but its two downloads did not finish (this PC may be offline, or a proxy or firewall blocked GitHub).' + #13#10 + #13#10 +
        'Connect to the internet, then open "{#AppName} setup (passcode, USB helper)" from the Start menu. It picks up where this stopped and shows what went wrong.',
        mbInformation, MB_OK, IDOK);
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopAppProcesses
  else if CurUninstallStep = usPostUninstall then
    SuppressibleMsgBox('{#AppName} was removed.' + #13#10 + #13#10 +
      'Your data was kept: the .state folder inside ' + ExpandConstant('{app}') + ' holds your drafts, settings and the signing material. ' +
      'Delete that folder yourself if you want it gone. Your saved iPhone passcode (.env) was removed.',
      mbInformation, MB_OK, IDOK);
end;
