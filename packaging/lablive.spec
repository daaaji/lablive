# PyInstaller spec — onedir: lablive.exe (창) + lablive-cli.exe (명령) 가 _internal 을 공유
# 빌드: pyinstaller --noconfirm --clean lablive.spec   (Windows, 또는 wine의 Windows Python)
import os
SRC = os.path.abspath(os.path.join(SPECPATH, ".."))
HIDDEN = ["matplotlib.backends.backend_agg", "matplotlib.backends.backend_tkagg", "serial.tools.list_ports",
          "lablive.sim"]
EXCL = ["PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "jupyter", "pytest", "scipy", "pandas"]

def an(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[SRC], hiddenimports=HIDDEN, excludes=EXCL,
                    noarchive=False)

a_gui, a_cli = an("app_gui.py"), an("app_cli.py")
VER = os.path.join(SPECPATH, "version_info.txt")
ICON = os.path.join(SPECPATH, "app.ico")
exe_gui = EXE(PYZ(a_gui.pure), a_gui.scripts, [], exclude_binaries=True, name="lablive", console=False,
              icon=ICON, version=VER, upx=False)
exe_cli = EXE(PYZ(a_cli.pure), a_cli.scripts, [], exclude_binaries=True, name="lablive-cli", console=True,
              icon=ICON, version=VER, upx=False)
coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas, exe_cli, a_cli.binaries, a_cli.datas,
               name="lablive", upx=False)
