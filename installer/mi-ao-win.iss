#define AppVersion "0.2.0"
#ifndef ReleaseDir
  #define ReleaseDir "..\dist\release"
#endif
[Setup]
AppId={{F5680D2F-6E01-4148-9E98-FA2A9DB1E845}
AppName=米遥 Windows
AppVersion={#AppVersion}
AppPublisher=mi-ao-win contributors
DefaultDirName={localappdata}\Programs\MiAoWin
DefaultGroupName=米遥
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=..\assets\miao.ico
UninstallDisplayIcon={app}\米遥.exe
LicenseFile=..\LICENSE
InfoBeforeFile=..\docs\INSTALL-NOTES.txt
OutputDir={#ReleaseDir}
OutputBaseFilename=mi-ao-win-{#AppVersion}-setup-x64
Compression=lzma2
SolidCompression=yes
CloseApplications=no
RestartApplications=no
AppMutex=Local\MiAoWin.UI.v1
VersionInfoVersion={#AppVersion}
VersionInfoDescription=米遥 Windows 安装程序
UninstallDisplayName=米遥 Windows {#AppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#ReleaseDir}\米遥.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\PRIVACY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式 / Create a desktop shortcut"; Flags: checkedonce

[Icons]
Name: "{group}\米遥"; Filename: "{app}\米遥.exe"
Name: "{group}\卸载米遥"; Filename: "{uninstallexe}"
Name: "{autodesktop}\米遥"; Filename: "{app}\米遥.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\米遥.exe"; Description: "打开米遥并完成首次设置 / Open MiAo"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
