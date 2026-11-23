Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
Name "Meshpool"
OutFile "Meshpool Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\Meshpool"
InstallDirRegKey HKCU "Software\Meshpool" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64

; The version placeholders are filled in by packaging/build_all.py from app/meshpool_harness/__init__.py.
BrandingText "Meshpool @VERSION@@BETA@"
VIProductVersion "@VERSION_NUM@.0"
VIAddVersionKey "ProductName" "Meshpool"
VIAddVersionKey "FileDescription" "Meshpool Setup"
VIAddVersionKey "FileVersion" "@VERSION@"
VIAddVersionKey "ProductVersion" "@VERSION@"
VIAddVersionKey "CompanyName" "Meshpool"
VIAddVersionKey "LegalCopyright" "Copyright 2026 Meshpool."

!define MUI_ICON "Meshpool\Meshpool.ico"
!define MUI_UNICON "Meshpool\Meshpool.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Install Meshpool"
!define MUI_WELCOMEPAGE_TEXT "Meshpool is a coding and cowork agent that runs on your computer with your Meshpool API key.$\r$\n$\r$\nEverything is included: the app, the Meshpool Code, Meshpool Work, Pi and Hermes agents (LibreOffice is inside, nothing downloads later; Codex and OpenCode run on demand and download when first used), browser use, computer use, Office files, connectors for GitHub, Jira, Linear, Notion and more, and the meshpool command for your terminal. Nothing else to install, and no administrator rights needed."
!define MUI_FINISHPAGE_RUN "$INSTDIR\Meshpool.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open Meshpool now"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!include "winsetup.nsh"

; folder swap helpers for the staged install (see the main section)
; IfErrors also CLEARS the error flag, so each step records a failure in $R9 (checked once after every swap):
; relying on the flag alone lost a failed rename and left a mix of old and new folders.
!macro MP_SWAP N
  RMDir /r "$INSTDIR\${N}.old"
  ClearErrors
  IfFileExists "$INSTDIR\${N}\*.*" 0 +2
    Rename "$INSTDIR\${N}" "$INSTDIR\${N}.old"
  IfErrors 0 +3
    StrCpy $R9 "1"
    Goto +2
  Rename "$INSTDIR.new\${N}" "$INSTDIR\${N}"
  IfErrors 0 +2
    StrCpy $R9 "1"
!macroend
!macro MP_DROP_OLD N
  RMDir /r "$INSTDIR\${N}.old"
!macroend
!macro MP_ROLLBACK N
  IfFileExists "$INSTDIR\${N}.old\*.*" 0 +3
    RMDir /r "$INSTDIR\${N}"
    Rename "$INSTDIR\${N}.old" "$INSTDIR\${N}"
!macroend

Section "Meshpool" SecMain
  InitPluginsDir
  File "/oname=$PLUGINSDIR\winsetup.ps1" "winsetup.ps1"
  ; close a running copy (the app and everything it started from this folder) so its files can be replaced
  nsExec::Exec 'taskkill /F /IM Meshpool.exe'
  Pop $0
  ; also the app's own Python and Node processes (they hold files in runtime and node open): only programs that
  ; live in the install folder, never this installer or the update helper (taskkill /T could reach those)
  nsExec::Exec `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith('$INSTDIR\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue"`
  Pop $0
  !insertmacro WinSetup "$INSTDIR" "" ""
  ; Stage the new program files beside the install folder first, then swap folder by folder (the old one is kept as
  ; <name>.old until every swap worked, and put back if one fails). A failure halfway (power loss, a locked file, a full
  ; disk) therefore leaves a working install instead of a half-deleted one. The user's settings live in %APPDATA%\Meshpool.
  RMDir /r "$INSTDIR.new"
  SetOutPath "$INSTDIR.new"
  File /r "Meshpool\*.*"
  SetOutPath "$INSTDIR"
  StrCpy $R9 "0"
  !insertmacro MP_SWAP app
  !insertmacro MP_SWAP runtime
  !insertmacro MP_SWAP git
  !insertmacro MP_SWAP node
  !insertmacro MP_SWAP harness-docs
  !insertmacro MP_SWAP bin
  StrCmp $R9 "1" swap_failed
  ; older installs had the terminal command next to Meshpool.exe; it now lives in bin
  Delete "$INSTDIR\meshpool.cmd"
  Delete "$INSTDIR\Meshpool.exe"
  Delete "$INSTDIR\Meshpool.ico"
  Delete "$INSTDIR\LICENSE.txt"
  Rename "$INSTDIR.new\Meshpool.exe" "$INSTDIR\Meshpool.exe"
  Rename "$INSTDIR.new\Meshpool.ico" "$INSTDIR\Meshpool.ico"
  IfFileExists "$INSTDIR.new\LICENSE.txt" 0 +2
    Rename "$INSTDIR.new\LICENSE.txt" "$INSTDIR\LICENSE.txt"
  RMDir /r "$INSTDIR.new"
  !insertmacro MP_DROP_OLD app
  !insertmacro MP_DROP_OLD runtime
  !insertmacro MP_DROP_OLD git
  !insertmacro MP_DROP_OLD node
  !insertmacro MP_DROP_OLD harness-docs
  !insertmacro MP_DROP_OLD bin
  Goto swap_done
  swap_failed:
    !insertmacro MP_ROLLBACK app
    !insertmacro MP_ROLLBACK runtime
    !insertmacro MP_ROLLBACK git
    !insertmacro MP_ROLLBACK node
    !insertmacro MP_ROLLBACK harness-docs
    !insertmacro MP_ROLLBACK bin
    RMDir /r "$INSTDIR.new"
    MessageBox MB_OK|MB_ICONSTOP "Meshpool could not be updated: a file in $INSTDIR is in use or the disk is full. The previous version was kept." /SD IDOK
    Abort
  swap_done:
  ; The uninstaller is written and signed on the build machine (packaging/nsis/uninstaller.nsi) and shipped here, so
  ; it carries a signature. Without the Azure account the build falls back to WriteUninstaller, which writes it on the
  ; user's machine and leaves it unsigned.
  !ifdef MP_SIGNED_UNINSTALLER
    File "/oname=Uninstall Meshpool.exe" "${MP_SIGNED_UNINSTALLER}"
  !else
    WriteUninstaller "$INSTDIR\Uninstall Meshpool.exe"
  !endif
  CreateShortcut "$SMPROGRAMS\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
  ; a silent install is an update (or a managed install): it must not bring back a Desktop shortcut the user deleted
  ${IfNot} ${Silent}
    CreateShortcut "$DESKTOP\Meshpool.lnk" "$INSTDIR\Meshpool.exe" "" "$INSTDIR\Meshpool.ico"
  ${EndIf}
  WriteRegStr HKCU "Software\Meshpool" "InstallDir" "$INSTDIR"
  ; the meshpool terminal command: only its own folder goes on the user's PATH. With the install folder itself on
  ; PATH, `meshpool` would start Meshpool.exe (Windows tries .EXE before .CMD), so an older entry for it is removed.
  !insertmacro WinSetup "" "$INSTDIR\bin" "$INSTDIR"
  !define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "@VERSION@"
  WriteRegStr HKCU "${UNINST}" "Publisher" "Meshpool"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\Meshpool.ico"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall Meshpool.exe"'
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  InitPluginsDir
  File "/oname=$PLUGINSDIR\winsetup.ps1" "winsetup.ps1"
  ; close the app and everything running from this folder (its Python, Node, Git), or their files stay locked
  nsExec::Exec 'taskkill /F /IM Meshpool.exe'
  Pop $0
  ; also the app's own Python and Node processes (they hold files in runtime and node open): only programs that
  ; live in the install folder, never this installer or the update helper (taskkill /T could reach those)
  nsExec::Exec `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith('$INSTDIR\', [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force -ErrorAction SilentlyContinue"`
  Pop $0
  !insertmacro WinSetup "$INSTDIR" "" ""
  Delete "$SMPROGRAMS\Meshpool.lnk"
  Delete "$DESKTOP\Meshpool.lnk"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\git"
  RMDir /r "$INSTDIR\node"
  RMDir /r "$INSTDIR\harness-docs"
  RMDir /r "$INSTDIR\bin"
  ; what a failed update can leave behind (see the swap in the install section)
  RMDir /r "$INSTDIR.new"
  RMDir /r "$INSTDIR\app.old"
  RMDir /r "$INSTDIR\runtime.old"
  RMDir /r "$INSTDIR\git.old"
  RMDir /r "$INSTDIR\node.old"
  RMDir /r "$INSTDIR\harness-docs.old"
  RMDir /r "$INSTDIR\bin.old"
  Delete "$INSTDIR\Meshpool.exe"
  Delete "$INSTDIR\meshpool.cmd"
  Delete "$INSTDIR\Meshpool.ico"
  Delete "$INSTDIR\LICENSE.txt"
  Delete "$INSTDIR\Uninstall Meshpool.exe"
  RMDir "$INSTDIR"
  !insertmacro WinSetup "" "" "$INSTDIR\bin;$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Meshpool"
  DeleteRegKey HKCU "Software\Meshpool"
  ; settings and sessions in %APPDATA%\Meshpool are kept on purpose
SectionEnd
