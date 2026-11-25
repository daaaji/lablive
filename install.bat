@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul
echo === lablive 0.9.0b7 install ===
echo folder: %~dp0

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo Python 3.9+ not found. Install from python.org and check "Add python.exe to PATH".
  pause & exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 ( echo Python 3.9+ required. & pause & exit /b 1 )

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment .venv ...
  %PY% -m venv .venv
  if errorlevel 1 ( echo venv creation failed. & pause & exit /b 1 )
)
set "VPY=%~dp0.venv\Scripts\python.exe"

if exist "wheels\" (
  echo Installing from local "wheels" folder ^(offline^) ...
  "%VPY%" -m pip install --no-index --find-links wheels -e .
) else (
  "%VPY%" -m pip install --upgrade pip
  "%VPY%" -m pip install -e .
)
if errorlevel 1 ( echo pip install failed - see messages above. & pause & exit /b 1 )

rem ---- data folder (saved to %USERPROFILE%\.lablive.json; change later in led-gui "Paths") ----
for /f "usebackq delims=" %%H in (`"%VPY%" -c "from lablive import config; print(config.user_home())"`) do set "DEF=%%H"
if not exist "%USERPROFILE%\.lablive.json" if exist "C:\code\control\profiles\" set "DEF=C:\code\control"
echo.
echo Data folder (profiles, runs, logs). Existing C:\code\control data can be reused.
set "HOME_DIR="
set /p "HOME_DIR=Data folder [!DEF!]: "
if "!HOME_DIR!"=="" set "HOME_DIR=!DEF!"
if not exist "!HOME_DIR!" mkdir "!HOME_DIR!"
"%VPY%" -c "import sys; from lablive import config; config.save_user(home=sys.argv[1])" "!HOME_DIR!"

rem ---- shell launcher ----
(
echo @echo off
echo chcp 65001 ^>nul
echo set "PYTHONUTF8=1"
echo call "%~dp0.venv\Scripts\activate.bat"
echo cd /d "!HOME_DIR!"
echo echo lablive 0.9.0b7   data: !HOME_DIR!
echo echo   led-gui / led-check / led-iv / led-live / led-profile / led-pulses / lablive-selftest   ^(-h for help^)
echo cmd /k
) > "%~dp0lablive_shell.bat"
(
echo @echo off
echo start "" "%~dp0.venv\Scripts\pythonw.exe" -m lablive.cli.gui %%*
) > "%~dp0lablive_gui.bat"

echo.
echo Installed. Running selftest (simulator, about 1 min) ...
set "PYTHONUTF8=1"
"%~dp0.venv\Scripts\lablive-selftest.exe"
echo.
echo Start: double-click lablive_gui.bat (window) or lablive_shell.bat (commands)
pause
