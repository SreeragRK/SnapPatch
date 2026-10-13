; Inno Setup script for SnapPatch  (compiled by build_installer.bat)
#ifndef AppVersion
  #define AppVersion "3.3"
#endif
#ifndef ExeDir
  #define ExeDir "."
#endif

[Setup]
AppId={{7E1C1F3A-5B8E-4C7F-9C2E-3D2A9B1C7F40}
AppName=SnapPatch
AppVersion={#AppVersion}
; Always a per-user install in the same place, so every upgrade lands on top of the previous one.
DefaultDirName={localappdata}\Programs\SnapPatch
DefaultGroupName=SnapPatch
UninstallDisplayIcon={app}\SnapPatch.exe
OutputBaseFilename=SnapPatch-Setup
SetupIconFile=snappatch.ico
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName=SnapPatch
AppVerName=SnapPatch {#AppVersion}
UninstallDisplayName=SnapPatch {#AppVersion}
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start SnapPatch with &Windows (minimized, hotkeys ready)"; GroupDescription: "Options:"

[Files]
Source: "{#ExeDir}\SnapPatch.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\SnapPatch"; Filename: "{app}\SnapPatch.exe"
Name: "{group}\Uninstall SnapPatch"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SnapPatch"; Filename: "{app}\SnapPatch.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SnapPatch"; ValueData: """{app}\SnapPatch.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\SnapPatch.exe"; Description: "Launch SnapPatch now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM SnapPatch.exe"; Flags: runhidden; RunOnceId: "KillSnapPatch"

[Code]
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{7E1C1F3A-5B8E-4C7F-9C2E-3D2A9B1C7F40}_is1';

// Remove an older SnapPatch that was installed in a different place (e.g. "for all users" under
// Program Files by an earlier version), so there is only ever one copy and one set of shortcuts.
procedure RemoveOtherInstall(Root: Integer);
var
  Uninst: String;
  ResultCode: Integer;
begin
  if RegQueryStringValue(Root, UninstKey, 'UninstallString', Uninst) then
  begin
    Uninst := RemoveQuotes(Uninst);
    if FileExists(Uninst) and (CompareText(ExtractFileDir(Uninst), ExpandConstant('{app}')) <> 0) then
    begin
      Log('Removing older SnapPatch at ' + Uninst);
      Exec(Uninst, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

// SnapPatch is usually running (hotkeys / start with Windows), so close it before files are replaced.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM SnapPatch.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(600);
  RemoveOtherInstall(HKLM);
  RemoveOtherInstall(HKCU);
  Result := '';
end;

// Show the installed version on the last page so an upgrade is easy to verify.
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := 'SnapPatch {#AppVersion} is installed in ' + ExpandConstant('{app}') + #13#10#13#10 +
      'Check Help -> About in the app to confirm the version.';
end;
