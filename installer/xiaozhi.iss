#define MyAppName "小智电脑助手"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "XiaoZhi"
#define MyAppExe "runtime\pythonw.exe"

[Setup]
AppId={{E4AEF3AD-2F37-47BF-B3B6-BC4FB9A0D1C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\XiaoZhiAssistant
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=XiaoZhiSetup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\runtime\pythonw.exe
SetupLogging=yes

[Files]
Source: "..\build\stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\小智电脑助手"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\main.py"""; WorkingDir: "{app}"
Name: "{userdesktop}\小智电脑助手"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\main.py"""; WorkingDir: "{app}"

[Tasks]
Name: "startup"; Description: "登录 Windows 后自动启动小智后台"; GroupDescription: "启动选项:"; Flags: checkedonce

[Registry]
; Use the per-user Run key instead of writing a .lnk into the Startup folder.
; Some Windows installations/security policies deny writes to the Startup folder
; even for a per-user installer, causing IPersistFile::Save 0x80070005.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "XiaoZhiAssistant"; ValueData: """{app}\runtime\pythonw.exe"" ""{app}\app\main.py"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\app\main.py"""; WorkingDir: "{app}"; Description: "启动小智电脑助手"; Flags: nowait postinstall skipifsilent

[Code]
procedure RemoveLegacyStartupShortcut();
var
  LegacyLink: string;
begin
  LegacyLink := ExpandConstant('{userstartup}\小智电脑助手后台.lnk');
  if FileExists(LegacyLink) then
  begin
    { Best effort only. A protected Startup folder must never abort install/uninstall. }
    DeleteFile(LegacyLink);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    RemoveLegacyStartupShortcut();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveLegacyStartupShortcut();
end;
