; CortexHarness Windows Installer Script
; Generates professional setup.exe with context menu integration

[Setup]
; Installer configuration
AppName=CortexHarness
AppVersion=1.0.0
AppPublisher=CortexHarness Project
AppPublisherURL=https://github.com/your-org/cortex-harness
AppSupportURL=https://github.com/your-org/cortex-harness/issues
AppUpdatesURL=https://github.com/your-org/cortex-harness/releases

; Default installation directory
DefaultDirName={pf}\CortexHarness
DefaultGroupName=CortexHarness

; Output file configuration
OutputBaseFilename=cortex-harness-setup
OutputDir=..\..\dist

; Compression and optimization
Compression=lzma2
SolidCompression=yes
InternalCompressLevel=max

; Installer options
AllowNoIcons=yes
AlwaysShowDirOnReadyPage=yes
AlwaysShowGroupOnReadyPage=yes
AppendDefaultDirName=yes
AppendDefaultGroupName=yes
DisableDirPage=no
DisableProgramGroupPage=no
DisableStartupPrompt=yes
DisableWelcomePage=no
LicenseFile=..\..\LICENSE.txt

; Privileges and permissions
PrivilegesRequired=admin
MinVersion=6.1sp1
ArchitectureAllowed=x64
ArchitectureInstallIn64BitMode=x64

; Wizard interface
WizardStyle=modern
WizardImageFile=installer-sidebar.bmp
WizardSmallImageFile=installer-small.bmp
SetupIconFile=app.ico
UninstallDisplayIcon={app}\app.ico

; Messages and localization
ShowLanguageDialog=no
LanguageDetectionMethod=uilocale

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"
Name: "quicklaunchicon"; Description: "Create a &Quick Launch icon"; GroupDescription: "Additional icons:"; OnlyBelowVersion: 6.1
Name: "contextmenu"; Description: "Add to &Windows Explorer context menu"; GroupDescription: "Integration:"

[Files]
; Main application files
Source: "..\..\cortex_harness\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__"
Source: "..\..\cli\*"; DestDir: "{app}\cli"; Flags: recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__"
Source: "..\..\code-tiny\*"; DestDir: "{app}\code-tiny"; Flags: recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__,.venv"
Source: "..\..\doc-tiny\*"; DestDir: "{app}\doc-tiny"; Flags: recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__,.venv"
Source: "..\..\harness\*"; DestDir: "{app}\harness"; Flags: recursesubdirs createallsubdirs; Excludes: "*.pyc,__pycache__"

; Context menu wrapper scripts
Source: "..\scripts\wrapper.bat"; DestDir: "{app}\scripts"; Flags: ignoreversion

; Configuration files
Source: "..\..\pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\*.md"; DestDir: "{app}"; Flags: ignoreversion

; Documentation
Source: "..\..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
; Program shortcuts
Name: "{group}\CortexHarness CLI"; Filename: "{cmd}"; Parameters: "/K ""cd /d ""{app}"" && .venv\Scripts\python.exe cortex_harness\dev.py --help"""; IconFilename: "{app}\app.ico"; Comment: "Open CortexHarness CLI"
Name: "{group}\CortexHarness Documentation"; Filename: "{app}\docs\README.md"; IconFilename: "{app}\app.ico"; Comment: "View documentation"
Name: "{group}\Uninstall CortexHarness"; Filename: "{uninstallexe}"; IconFilename: "{app}\app.ico"

; Desktop and Quick Launch icons
Name: "{userdesktop}\CortexHarness CLI"; Filename: "{cmd}"; Parameters: "/K ""cd /d ""{app}"" && .venv\Scripts\python.exe cortex_harness\dev.py --help""; IconFilename: "{app}\app.ico"; Tasks: desktopicon; Comment: "Open CortexHarness CLI"
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\CortexHarness CLI"; Filename: "{cmd}"; Parameters: "/K ""cd /d ""{app}"" && .venv\Scripts\python.exe cortex_harness\dev.py --help""; IconFilename: "{app}\app.ico"; Tasks: quicklaunchicon

[Registry]
; Context menu integration for directory right-click
Root: HKCR; Subkey: "Directory\shell\CortexHarness"; ValueType: string; ValueName: ""; ValueData: "CortexHarness"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\CortexHarness"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\app.ico"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\CortexHarness"; ValueType: string; ValueName: "Position"; ValueData: "bottom"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\CortexHarness"; ValueType: string; ValueName: "MUIVerb"; ValueData: "CortexHarness"; Flags: uninsdeletekey; Tasks: contextmenu

; Context menu subcommands
Root: HKCR; Subkey: "Directory\shell\CortexHarness\shell\sync_code"; ValueType: string; ValueName: ""; ValueData: "Sync Code"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\CortexHarness\shell\sync_code\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""sync code"" ""%1"""; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCR; Subkey: "Directory\shell\CortexHarness\shell\sync_doc"; ValueType: string; ValueName: ""; ValueData: "Sync Documents"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\CortexHarness\shell\sync_doc\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""sync doc"" ""%1"""; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCR; Subkey: "Directory\shell\CortexHarness\shell\run_harness"; ValueType: string; ValueName: ""; ValueData: "Run Harness"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\CortexHarness\shell\run_harness\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""harness run"" ""%1"""; Flags: uninsdeletekey; Tasks: contextmenu

; Context menu integration for directory background right-click
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness"; ValueType: string; ValueName: ""; ValueData: "CortexHarness"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\app.ico"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness"; ValueType: string; ValueName: "Position"; ValueData: "bottom"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness"; ValueType: string; ValueName: "MUIVerb"; ValueData: "CortexHarness"; Flags: uninsdeletekey; Tasks: contextmenu

; Context menu subcommands for background
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness\shell\sync_code"; ValueType: string; ValueName: ""; ValueData: "Sync Code"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness\shell\sync_code\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""sync code"" ""%V"""; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness\shell\sync_doc"; ValueType: string; ValueName: ""; ValueData: "Sync Documents"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness\shell\sync_doc\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""sync doc"" ""%V"""; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness\shell\run_harness"; ValueType: string; ValueName: ""; ValueData: "Run Harness"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\CortexHarness\shell\run_harness\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""harness run"" ""%V"""; Flags: uninsdeletekey; Tasks: contextmenu

; Context menu integration for drive right-click
Root: HKCR; Subkey: "Drive\shell\CortexHarness"; ValueType: string; ValueName: ""; ValueData: "CortexHarness"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Drive\shell\CortexHarness"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\app.ico"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Drive\shell\CortexHarness"; ValueType: string; ValueName: "Position"; ValueData: "bottom"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Drive\shell\CortexHarness"; ValueType: string; ValueName: "MUIVerb"; ValueData: "CortexHarness"; Flags: uninsdeletekey; Tasks: contextmenu

; Context menu subcommands for drives
Root: HKCR; Subkey: "Drive\shell\CortexHarness\shell\sync_code"; ValueType: string; ValueName: ""; ValueData: "Sync Code"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Drive\shell\CortexHarness\shell\sync_code\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""sync code"" ""%1"""; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCR; Subkey: "Drive\shell\CortexHarness\shell\sync_doc"; ValueType: string; ValueName: ""; ValueData: "Sync Documents"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Drive\shell\CortexHarness\shell\sync_doc\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""sync doc"" ""%1"""; Flags: uninsdeletekey; Tasks: contextmenu

Root: HKCR; Subkey: "Drive\shell\CortexHarness\shell\run_harness"; ValueType: string; ValueName: ""; ValueData: "Run Harness"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCR; Subkey: "Drive\shell\CortexHarness\shell\run_harness\command"; ValueType: string; ValueName: ""; ValueData: """{app}\scripts\wrapper.bat"" ""harness run"" ""%1"""; Flags: uninsdeletekey; Tasks: contextmenu

[Run]
; Post-installation operations
Filename: "{cmd}"; Parameters: "/C ""setx CORTEX_HARNESS_DIR ""{app}"""""; Description: "Set environment variables"; StatusMsg: "Setting environment variables..."; Flags: runhidden

[UninstallDelete]
; Delete files that were created during runtime
Delete: "{app}\logs\*"; Delete: "{app}\cache\*"; Delete: "{app}\temp\*"

[UninstallRun]
; Pre-uninstallation operations
Filename: "{cmd}"; Parameters: "/C ""reg delete HKCU\Environment /V CORTEX_HARNESS_DIR /f"""; RunOnceId: "CleanupEnvVar"; Flags: runhidden

; Cleanup context menu (backup first)
Filename: "{cmd}"; Parameters: "/C ""reg export HKCR\Directory\shell\CortexHarness ""{tmp}\context_menu_backup.reg"" """; RunOnceId: "BackupContext"; Flags: runhidden

[Code]
// Pascal script for custom installer actions

function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  // Check Windows version
  GetWindowsVersion(Version);
  if (Version.Major < 6) or ((Version.Major = 6) and (Version.Minor < 1)) then
  begin
    MsgBox('CortexHarness requires Windows 7 or later.', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  // Check if already installed
  if RegKeyExists(HKLM, 'SOFTWARE\CortexHarness') then
  begin
    if MsgBox('CortexHarness is already installed. Do you want to uninstall the previous version?', mbConfirmation, MB_YESNO) = IDYES then
    begin
      // Launch uninstaller
      if Exec(ExpandConstant('{uninstallexe}'), '/SILENT', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      begin
        // Uninstaller ran successfully
        Result := True;
      end
      else
      begin
        // Uninstaller failed or was cancelled
        MsgBox('Failed to uninstall previous version. Please uninstall manually and try again.', mbError, MB_OK);
        Result := False;
        Exit;
      end;
    end
    else
    begin
      // User chose not to uninstall
      Result := False;
      Exit;
    end;
  end;

  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Create desktop shortcut for CLI
    if IsTaskSelected('desktopicon') then
    begin
      CreateShortcut(
        ExpandConstant('{userdesktop}\CortexHarness CLI.lnk'),
        ExpandConstant('{cmd}'),
        '/K ""cd /d "' + ExpandConstant('{app}') + '" && .venv\Scripts\python.exe cortex_harness\dev.py --help""',
        ExpandConstant('{app}'),
        '', '', SW_SHOWNORMAL, 0);
    end;

    // Create Start Menu shortcuts
    CreateShortcut(
      ExpandConstant('{group}\CortexHarness CLI.lnk'),
      ExpandConstant('{cmd}'),
      '/K ""cd /d "' + ExpandConstant('{app}') + '" && .venv\Scripts\python.exe cortex_harness\dev.py --help""',
      ExpandConstant('{app}'),
      '', '', SW_SHOWNORMAL, 0);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // Skip license page if license file doesn't exist
  if (PageID = wpLicense) and not FileExists(ExpandConstant('{app}\LICENSE.txt')) then
    Result := True
  else
    Result := False;
end;

procedure DeinitializeSetup();
begin
  // Show completion message
  if MsgBox('CortexHarness has been installed successfully! Do you want to view the README file?', mbInformation, MB_YESNO) = IDYES then
  begin
    if not ShellExec('open', ExpandConstant('{app}\docs\README.md'), '', '', SW_SHOW, ewNoWait, ResultCode) then
    begin
      MsgBox('Failed to open README file.', mbError, MB_OK);
    end;
  end;
end;