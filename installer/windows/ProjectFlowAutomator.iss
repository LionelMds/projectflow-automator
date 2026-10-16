#define MyAppName "ProjectFlow Automator"
#define MyAppPublisher "Balz Metal Sa"
#define MyAppExeName "ProjectFlowAutomator.exe"
#define MyAppVersion GetEnv("PROJECTFLOW_VERSION")

#if MyAppVersion == ""
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{C91D6B6C-0AC5-47F9-80EF-1665EC55D1F6}
AppName={#MyAppName}
AppPublisher={#MyAppPublisher}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\ProjectFlow Automator
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=ProjectFlowAutomatorSetup
SetupIconFile=..\..\src\projectflow\ui\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=yes
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Creer une icone sur le Bureau"; GroupDescription: "Raccourcis:"; Flags: unchecked
Name: "cleaninstall"; Description: "Nouvelle installation : tout reinitialiser (connexion Microsoft, parametres, cle Document Manager). Les dossiers projets ne sont pas touches."; GroupDescription: "Reinitialisation:"; Flags: unchecked

[Files]
Source: "..\..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Same reset as the in-app "Tout reinitialiser", run before the first start.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--reset-all"; StatusMsg: "Reinitialisation de ProjectFlow..."; Tasks: cleaninstall; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  // An instance left in the tray keeps the old version running: starting the new
  // one would only bring that old window back. In-app updates quit by themselves.
  if not WizardSilent() then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#MyAppExeName}', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
