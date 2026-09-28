; NSIS setup for AVF — one-click installer in the electron-builder style:
; double-click, progress, "Run AI Video Factory" checked at the end. Per-user
; (no UAC prompt), desktop shortcut created, uninstaller in Apps & Features,
; and a WebView2 runtime check (pywebview renders through it).
; Compiled by packaging/build.py with @STAGE@/@OUT@/@ICO@/@HEADER@/@VERSION@.

Unicode true
ManifestDPIAware true

!define APPNAME "AI Video Factory"
Name "${APPNAME}"
OutFile "@OUT@"
InstallDir "$LOCALAPPDATA\Programs\AVF"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!define MUI_ICON "@ICO@"
!define MUI_UNICON "@ICO@"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "@HEADER@"
!define MUI_HEADERIMAGE_RIGHT
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\AVF.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Run AI Video Factory"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "@STAGE@/*.*"

  ; WebView2 runtime: pywebview renders through it. Present on current
  ; Win10/11 installs; when the registry says otherwise the bundled
  ; evergreen bootstrapper runs silently.
  ClearErrors
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  StrCmp $0 "" 0 webview_done
  DetailPrint "Installing the WebView2 runtime (rendering engine)..."
  ExecWait '"$INSTDIR\WebView2Bootstrapper.exe" /silent /install'
webview_done:

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\AI Video Factory.lnk" "$INSTDIR\AVF.exe" "" "$INSTDIR\AVF.ico" 0
  CreateDirectory "$SMPROGRAMS\AI Video Factory"
  CreateShortCut "$SMPROGRAMS\AI Video Factory\AI Video Factory.lnk" "$INSTDIR\AVF.exe" "" "$INSTDIR\AVF.ico" 0

  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "DisplayVersion" "@VERSION@"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "Publisher" "AVF"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "DisplayIcon" "$INSTDIR\AVF.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF" "NoRepair" 1
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\AI Video Factory.lnk"
  Delete "$SMPROGRAMS\AI Video Factory\AI Video Factory.lnk"
  RMDir "$SMPROGRAMS\AI Video Factory"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\AVF"
SectionEnd
