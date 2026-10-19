; Dolmi installer (Inno Setup 6.7+). Build with installer\build.ps1, which fills ..\dist\Dolmi and runs:
;   ISCC /DMyAppVersion=x.y.z installer\dolmi.iss
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppName "Dolmi"
#define MyAppURL "https://dolmi.zehnix.co"
#define MyRepoURL "https://github.com/Zehnix/dolmi"
; Launcher: the bundled, PSF-signed pythonw.exe (no console window) running the app.
; Its argument is written inline as """{app}\app\src\main.py""" ("" is a literal quote in Inno).
#define Launcher "{app}\runtime\pythonw.exe"
#define AppIcon "{app}\app\assets\dolmi-2.ico"

[Setup]
; Generated once. NEVER change it: upgrades and the single Apps & features entry depend on it.
AppId={{01C6145B-761B-4EB9-A11D-36BEE004B479}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Zehnix
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyRepoURL}/issues
AppUpdatesURL={#MyRepoURL}/releases
AppCopyright=Copyright (c) 2026 Zehnix (Annas Mustafa). AGPL-3.0 or commercial licence.

; {autopf} = %LOCALAPPDATA%\Programs for per-user installs, Program Files for all users
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; per user by default (no admin prompt); "all users" is offered in a dialog
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

LicenseFile=..\LICENSE
SetupIconFile=..\assets\dolmi-2.ico
UninstallDisplayIcon={#AppIcon}
UninstallDisplayName={#MyAppName}
WizardStyle=modern dynamic
; invisible mode (screen-capture exclusion) needs Windows 10 2004 or later
MinVersion=10.0.19041
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; closes a running Dolmi (its pythonw.exe lives in {app}) before replacing files
CloseApplications=yes

OutputDir=..\dist
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german";  MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
english.Optional=Optional:
german.Optional=Optional:
english.GpuTask=GPU speech recognition for NVIDIA graphics cards (downloads about 1.3 GB)
german.GpuTask=GPU-Spracherkennung für NVIDIA-Grafikkarten (lädt ca. 1,3 GB herunter)
english.GpuStatus=Downloading GPU speech libraries (about 1.3 GB). This can take a few minutes...
german.GpuStatus=GPU-Sprachbibliotheken werden heruntergeladen (ca. 1,3 GB). Das kann einige Minuten dauern...
english.AppComment=Live meeting subtitles translated to English, on your PC
german.AppComment=Live-Untertitel für Meetings, auf Englisch übersetzt, auf Ihrem PC

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "gpu"; Description: "{cm:GpuTask}"; GroupDescription: "{cm:Optional}"; Flags: unchecked

[InstallDelete]
; upgrades start from a clean runtime and app (no stale modules); {app}\gpu is kept
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "..\dist\Dolmi\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID matches main.py, so a pinned taskbar icon groups with the running window
Name: "{autoprograms}\{#MyAppName}"; Filename: "{#Launcher}"; Parameters: """{app}\app\src\main.py"""; WorkingDir: "{app}\app"; IconFilename: "{#AppIcon}"; AppUserModelID: "Dolmi.App"; Comment: "{cm:AppComment}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{#Launcher}"; Parameters: """{app}\app\src\main.py"""; WorkingDir: "{app}\app"; IconFilename: "{#AppIcon}"; AppUserModelID: "Dolmi.App"; Comment: "{cm:AppComment}"; Tasks: desktopicon

[Run]
; optional GPU add-on: NVIDIA cuBLAS + cuDNN into {app}\gpu (on sys.path via python312._pth)
Filename: "{app}\runtime\python.exe"; Parameters: "-m pip install --disable-pip-version-check --no-warn-script-location --target ""{app}\gpu"" nvidia-cublas-cu12 ""nvidia-cudnn-cu12>=9,<10"""; StatusMsg: "{cm:GpuStatus}"; Flags: runhidden waituntilterminated; Tasks: gpu
Filename: "{#Launcher}"; Parameters: """{app}\app\src\main.py"""; WorkingDir: "{app}\app"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; everything Dolmi put in {app}. User data stays: Documents\Dolmi, %APPDATA%\Dolmi, models in ~\.cache
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\gpu"
Type: dirifempty; Name: "{app}"
