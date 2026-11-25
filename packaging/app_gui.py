"""PyInstaller 진입점: lablive.exe (창, 콘솔 없음)"""
import multiprocessing
import os
import sys


def _streams():
    """창 모드 exe는 sys.stdout/stderr가 None → 출력/traceback을 %LOCALAPPDATA%\\lablive\\lablive_gui.log 로."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "lablive")
    try:
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, "lablive_gui.log"), "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        f = open(os.devnull, "w")  # noqa: SIM115
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _streams()
    from lablive.cli.gui import entry
    entry()
