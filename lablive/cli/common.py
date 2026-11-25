"""CLI 공통: --home / --sim / 포트 옵션, 장비 열기, 오류 기록."""
import argparse
import os
import sys
import traceback
from datetime import datetime

from .. import __version__, config


def _utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def parser(prog, desc, ports=True):
    ap = argparse.ArgumentParser(prog=prog, description=f"{desc}  (lablive {__version__})")
    ap.add_argument("--home", help="데이터 폴더 (기본: 환경변수 LABLIVE_HOME 또는 ~/lablive_data)")
    ap.add_argument("--sim", action="store_true",
                    help="시뮬레이터 (장비 없이 연습/설치 확인). 데이터는 <home>/sim 에 따로")
    if ports:
        ap.add_argument("--pm-port", help="1936-R 포트 (기본: *IDN? 자동 탐색)")
        ap.add_argument("--k-port", help="2400 포트 (기본: *IDN? 자동 탐색)")
    return ap


def setup(a):
    _utf8_console()
    if getattr(a, "home", None):
        config.set_home(a.home)
    if getattr(a, "sim", False):
        from .. import sim
        sim.install()
        print("*** SIMULATOR 모드 — 실제 장비를 사용하지 않음 ***")
        if not getattr(a, "home", None):
            config.set_home(os.path.join(config.home(), "sim"))   # 시뮬레이션 데이터는 실제 데이터와 분리
    print(f"데이터 폴더: {config.home()}")


def open_instruments(a, need_pm=True, need_k=True):
    from ..keithley2400 import Keithley2400
    from ..newport1936 import Newport1936
    pm = k = None
    try:
        if need_pm:
            pm = Newport1936(a.pm_port)
        if need_k:
            k = Keithley2400(a.k_port, exclude=(pm.port,) if pm else ())
    except Exception:
        if pm:
            pm.close()
        raise
    print("  ".join(x for x in (f"1936-R: {pm.port}" if pm else "", f"2400: {k.port}" if k else "") if x))
    return pm, k


class Tee:
    """콘솔 출력 + 회차 폴더의 console.log"""

    def __init__(self, dir_fn=None):
        self.dir_fn, self.buf = dir_fn, []

    def __call__(self, msg):
        print(msg, flush=True)
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}\n"
        d = self.dir_fn() if self.dir_fn else None
        if d and os.path.isdir(d):
            try:
                with open(os.path.join(d, "console.log"), "a", encoding="utf-8") as f:
                    f.write("".join(self.buf) + line)
                self.buf = []
            except OSError:
                pass
        else:
            self.buf.append(line)


def run(main, name):
    """main() 실행. 예상 못 한 예외는 logs/에 traceback을 남기고 짧게 알림."""
    _utf8_console()
    try:
        return main()
    except KeyboardInterrupt:
        print("\n중단 (Ctrl+C)")
        return 130
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as e:
        print(f"\n오류: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        p = _dump(name)
        print(f"\n예상하지 못한 오류: {type(e).__name__}: {e}\n  기록: {p}  (이 파일을 공유해 주세요)")
        return 1


def _dump(name):
    try:
        p = os.path.join(config.logs_dir(), f"{datetime.now():%Y%m%d_%H%M%S}_{name}.log")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"lablive {__version__}  {datetime.now().isoformat()}\nargv: {sys.argv}\n\n")
            traceback.print_exc(file=f)
        return p
    except OSError:
        traceback.print_exc()
        return None


def fail(msg):
    raise ValueError(msg)


def add_led_args(ap):
    ap.add_argument("--profile", help="광원 프로파일 이름 (led-profile list)")
    ap.add_argument("--no-profile", action="store_true", help="프로파일 없이 (이 경우 --i-max 필수)")
    ap.add_argument("--i-max", type=float, help="전류 상한 (A). 프로파일 값보다 낮게만 지정 가능")
    ap.add_argument("--compliance", type=float, help="전압 상한 (V). 기본: 프로파일 또는 12")
    ap.add_argument("--lambda_nm", type=int, help="기록용 파장 (nm). 기본: 프로파일 값")


def resolve_led(a):
    """→ dict(profile, i_max, compliance, lambda_nm, vf_guard)"""
    from ..profiles import Profile
    if a.profile:
        p = Profile.load(a.profile)
        pim = p.get("i_max_A")
        if not pim:
            fail(f"프로파일 {p.name}에 i_max_A가 없음 — led-profile set {p.name} --i-max <A>")
        if a.i_max and a.i_max > pim:
            fail(f"--i-max {a.i_max:g} A > 프로파일 상한 {pim:g} A (상한을 올리려면 led-profile set)")
        out = dict(profile=p, i_max=min(pim, a.i_max or pim),
                   compliance=a.compliance if a.compliance is not None else (p.get("compliance_V") or 12.0),
                   lambda_nm=a.lambda_nm or p.get("lambda_nm"), vf_guard=p.get("vf_guard_V"))
    elif a.no_profile:
        if not a.i_max:
            fail("--no-profile 이면 --i-max (A) 필수 — LED 정격을 확인해서")
        out = dict(profile=None, i_max=a.i_max, compliance=a.compliance if a.compliance is not None else 12.0,
                   lambda_nm=a.lambda_nm, vf_guard=None)
    else:
        from ..profiles import Profile as P
        have = ", ".join(P.names()) or "없음"
        fail(f"--profile <이름> 필요 (있는 것: {have}).\n"
             f"  새 LED: led-profile new <이름> --lambda <nm> --i-max <A>   (정격 확인 후)\n"
             f"  프로파일 없이 한 번만: --no-profile --i-max <A>")
    p = out["profile"]
    print(f"LED: {p.name if p else '(프로파일 없음)'}  I ≤ {out['i_max'] * 1e3:g} mA, 상한 {out['compliance']:g} V, "
          f"λ {out['lambda_nm'] or '-'} nm, 경고 전압 {out['vf_guard'] or '-'}")
    return out
