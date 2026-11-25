"""PyInstaller 진입점: lablive-cli.exe <명령> [옵션]  (설치 폴더의 led-*.bat가 이걸 부름)"""
import multiprocessing
import sys

from lablive import __version__
from lablive.cli import check, gui, iv, live, profile, selftest

CMDS = {"live": live, "iv": iv, "profile": profile, "check": check, "selftest": selftest, "gui": gui}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(f"lablive {__version__}\n사용: lablive-cli <{'|'.join(CMDS)}> [옵션]   (각 명령 -h)\n"
              "  설치 폴더의 led-live.bat, led-iv.bat, led-profile.bat, led-check.bat, lablive-selftest.bat 도 같은 것")
        return 2
    cmd = sys.argv.pop(1)
    sys.argv[0] = f"led-{cmd}" if cmd != "selftest" else "lablive-selftest"
    CMDS[cmd].entry()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
