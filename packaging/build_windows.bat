@echo off
rem Rebuild the lablive installer on Windows.
rem Needs: Python 3.12 (python.org) and NSIS 3 (makensis on PATH or in the default folder)
setlocal
cd /d "%~dp0"
if not exist ".buildenv\Scripts\python.exe" (py -3.12 -m venv .buildenv || python -m venv .buildenv)
".buildenv\Scripts\python.exe" -m pip install --upgrade pip
".buildenv\Scripts\python.exe" -m pip install pyinstaller pyserial "numpy>=1.25" "matplotlib>=3.6" || goto :fail
".buildenv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath ..\dist --workpath ..\build lablive.spec || goto :fail
for /f "usebackq delims=" %%V in (`".buildenv\Scripts\python.exe" -c "import sys; sys.path.insert(0, '..'); import lablive; print(lablive.__version__)"`) do set "VER=%%V"
set "MAKENSIS=makensis"
where makensis >nul 2>nul || set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
"%MAKENSIS%" -INPUTCHARSET UTF8 -DVERSION=%VER% -DDIST=..\dist\lablive installer.nsi || goto :fail
echo.
echo Done: packaging\lablive_Setup_%VER%.exe
pause
exit /b 0
:fail
echo FAILED - see messages above
pause
exit /b 1
