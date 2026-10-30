; Aidy Voice Assistant - Inno Setup Script
; ----------------------------------------
; Build the project first (dotnet build -c Debug), then compile this script.

#define MyAppName "Aidy Voice Assistant"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Aidy"
#define MyAppExeName "WpfApp1.exe"

; Path to your build output
#define BuildOutput "bin\Debug\net8.0-windows"

[Setup]
AppId={{A1D7-V01C-3A55-1574-NT00}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Aidy
DefaultGroupName=Aidy
OutputDir=installer_output
OutputBaseFilename=AidySetup_v{#MyAppVersion}
SetupIconFile=Assets\logo.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; === Main application files ===
Source: "{#BuildOutput}\WpfApp1.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\WpfApp1.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\WpfApp1.deps.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\WpfApp1.runtimeconfig.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\WpfApp1.pdb"; DestDir: "{app}"; Flags: ignoreversion

; === Additional DLLs ===
Source: "{#BuildOutput}\System.DirectoryServices.AccountManagement.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\System.DirectoryServices.Protocols.dll"; DestDir: "{app}"; Flags: ignoreversion

; === Runtimes (native libs) ===
Source: "{#BuildOutput}\runtimes\*"; DestDir: "{app}\runtimes"; Flags: recursesubdirs ignoreversion

; === Config and data files ===
Source: "{#BuildOutput}\config.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\commands.csv"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildOutput}\apps.json"; DestDir: "{app}"; Flags: ignoreversion

; === API folder ===
Source: "{#BuildOutput}\Api\*"; DestDir: "{app}\Api"; Flags: recursesubdirs ignoreversion

; === Assets ===
Source: "{#BuildOutput}\Assets\*"; DestDir: "{app}\Assets"; Flags: recursesubdirs ignoreversion

; === PythonCore (Python backend) ===
Source: "{#BuildOutput}\PythonCore\main.py"; DestDir: "{app}\PythonCore"; Flags: ignoreversion
Source: "{#BuildOutput}\PythonCore\requirements.txt"; DestDir: "{app}\PythonCore"; Flags: ignoreversion
Source: "{#BuildOutput}\PythonCore\aidy\*"; DestDir: "{app}\PythonCore\aidy"; Flags: recursesubdirs ignoreversion

; === Bundled Python (fully standalone — no system Python needed) ===
Source: "python-embed\*"; DestDir: "{app}\python-embed"; Flags: recursesubdirs ignoreversion

; === VoiceProfiles folder (create empty, user data goes here) ===
Source: "{#BuildOutput}\VoiceProfiles\*"; DestDir: "{app}\VoiceProfiles"; Flags: recursesubdirs ignoreversion onlyifdoesntexist

[Dirs]
; Ensure VoiceProfiles directory exists even if empty
Name: "{app}\VoiceProfiles"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Aidy"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Aidy Voice Assistant"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up generated files on uninstall
Type: filesandordirs; Name: "{app}\PythonCore\aidy\__pycache__"
Type: filesandordirs; Name: "{app}\PythonCore\stderr.txt"
Type: filesandordirs; Name: "{app}\PythonCore\stdout.txt"
Type: files; Name: "{app}\voice_profiles.db"
