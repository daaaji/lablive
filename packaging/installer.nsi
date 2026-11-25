; lablive 설치 파일 (NSIS 3, Unicode). PyInstaller 결과 dist\lablive\ 를 묶는다.
; 빌드:  makensis -INPUTCHARSET UTF8 -DVERSION=0.9.0b4 -DDIST=..\dist\lablive installer.nsi
Unicode true
!include "MUI2.nsh"
!include "x64.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef DIST
  !define DIST "..\dist\lablive"
!endif
!define APP "lablive"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP} ${VERSION}"
OutFile "lablive_Setup_${VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP}"
InstallDirRegKey HKLM "Software\${APP}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
BrandingText "${APP} ${VERSION}"
VIProductVersion "0.9.0.6"
VIAddVersionKey /LANG=1042 "ProductName" "${APP}"
VIAddVersionKey /LANG=1042 "FileDescription" "${APP} 설치"
VIAddVersionKey /LANG=1042 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1042 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1042 "LegalCopyright" " "

!define MUI_ICON "app.ico"
!define MUI_UNICON "app.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEFINISHPAGE_BITMAP "wizard.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "wizard.bmp"
!define MUI_WELCOMEPAGE_TITLE "${APP} ${VERSION} 설치"
!define MUI_WELCOMEPAGE_TEXT "Keithley 2400(LED 정전류 구동) + Newport 1936-R(광전류)로 LED 광출력 안정도를 실시간으로 판정하는 프로그램입니다.$\r$\n$\r$\n측정 데이터(프로파일, 회차)는 설치 폴더가 아니라 사용자 데이터 폴더(기본 %USERPROFILE%\lablive_data, 예전 C:\code\control\profiles가 있으면 그 폴더)에 저장되며, 프로그램의 '경로' 창에서 바꿀 수 있습니다.$\r$\n$\r$\n이전 버전이 설치돼 있으면 덮어씁니다 (데이터는 그대로)."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "${APP} 실행"
!define MUI_FINISHPAGE_RUN_FUNCTION RunApp
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\README.md"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "사용 설명 (README) 열기"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "64비트 Windows가 필요합니다."
    Abort
  ${EndIf}
  SetRegView 64
FunctionEnd

Function RunApp
  ; 설치 프로그램은 관리자 권한 → 앱은 explorer를 거쳐 일반 권한으로 실행
  Exec '"$WINDIR\explorer.exe" "$INSTDIR\lablive.exe"'
FunctionEnd

Section "lablive (필수)" SecCore
  SectionIn RO
  SetShellVarContext all
  SetOutPath "$INSTDIR"
  ; 이전 버전 라이브러리 정리 (버전이 바뀐 파일이 섞이지 않게). 사용자 데이터는 설치 폴더에 없음
  RMDir /r "$INSTDIR\_internal"
  File /r "${DIST}\*.*"
  File "app.ico"
  File "..\README.md"
  File "..\CHANGELOG.md"
  ; 명령 창용 래퍼
  FileOpen $0 "$INSTDIR\led-live.bat" w
  FileWrite $0 '@"%~dp0lablive-cli.exe" live %*$\r$\n'
  FileClose $0
  FileOpen $0 "$INSTDIR\led-iv.bat" w
  FileWrite $0 '@"%~dp0lablive-cli.exe" iv %*$\r$\n'
  FileClose $0
  FileOpen $0 "$INSTDIR\led-profile.bat" w
  FileWrite $0 '@"%~dp0lablive-cli.exe" profile %*$\r$\n'
  FileClose $0
  FileOpen $0 "$INSTDIR\led-check.bat" w
  FileWrite $0 '@"%~dp0lablive-cli.exe" check %*$\r$\n'
  FileClose $0
  FileOpen $0 "$INSTDIR\led-gui.bat" w
  FileWrite $0 '@start "" "%~dp0lablive.exe" %*$\r$\n'
  FileClose $0
  FileOpen $0 "$INSTDIR\lablive-selftest.bat" w
  FileWrite $0 '@"%~dp0lablive-cli.exe" selftest %*$\r$\n'
  FileClose $0
  FileOpen $0 "$INSTDIR\lablive-shell.bat" w
  FileWrite $0 '@echo off$\r$\nchcp 65001 >nul$\r$\nset "PATH=%~dp0;%PATH%"$\r$\nset "PYTHONUTF8=1"$\r$\ncd /d "%USERPROFILE%"$\r$\necho lablive ${VERSION}$\r$\necho   commands: led-gui  led-live  led-iv  led-profile  led-check  lablive-selftest   (add -h for help)$\r$\necho   e.g.  led-iv --unknown     led-profile list     led-check --off$\r$\n'
  FileClose $0

  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\lablive.lnk" "$INSTDIR\lablive.exe" "" "$INSTDIR\app.ico"
  CreateShortcut "$SMPROGRAMS\${APP}\lablive 명령 창.lnk" "$SYSDIR\cmd.exe" '/k ""$INSTDIR\lablive-shell.bat""' "$INSTDIR\app.ico"
  CreateShortcut "$SMPROGRAMS\${APP}\lablive 시뮬레이터 (장비 없이 연습).lnk" "$INSTDIR\lablive.exe" "--sim" "$INSTDIR\app.ico"
  CreateShortcut "$SMPROGRAMS\${APP}\lablive 자가 진단.lnk" "$SYSDIR\cmd.exe" '/k ""$INSTDIR\lablive-shell.bat" & lablive-selftest"' "$INSTDIR\app.ico"
  CreateShortcut "$SMPROGRAMS\${APP}\비상 - 2400 출력 끄기.lnk" "$SYSDIR\cmd.exe" '/k ""$INSTDIR\lablive-shell.bat" & led-check --off"' "$INSTDIR\app.ico"
  CreateShortcut "$SMPROGRAMS\${APP}\사용 설명 (README).lnk" "$INSTDIR\README.md"
  CreateShortcut "$SMPROGRAMS\${APP}\제거.lnk" "$INSTDIR\Uninstall.exe"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "lablive"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\app.ico"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" 110000
SectionEnd

Section "바탕 화면 바로가기" SecDesktop
  SetShellVarContext all
  CreateShortcut "$DESKTOP\lablive.lnk" "$INSTDIR\lablive.exe" "" "$INSTDIR\app.ico"
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore} "프로그램, 시작 메뉴 바로가기(측정 창, 명령 창, 시뮬레이터, 자가 진단, 비상 OFF), 제거 프로그램"
  !insertmacro MUI_DESCRIPTION_TEXT ${SecDesktop} "바탕 화면에 lablive 바로가기"
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Function un.onInit
  SetRegView 64
FunctionEnd

Section "Uninstall"
  SetShellVarContext all
  Delete "$DESKTOP\lablive.lnk"
  RMDir /r "$SMPROGRAMS\${APP}"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\lablive.exe"
  Delete "$INSTDIR\lablive-cli.exe"
  Delete "$INSTDIR\*.bat"
  Delete "$INSTDIR\app.ico"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\CHANGELOG.md"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "Software\${APP}"
  ; 측정 데이터(프로파일, 회차)와 %USERPROFILE%\.lablive.json 은 지우지 않음
SectionEnd
