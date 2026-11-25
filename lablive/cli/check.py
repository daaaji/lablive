"""led-check — 장비 연결 확인 / 비상 OFF / 1936-R 설정 복구 (LED를 켜지 않음)

  led-check                     포트 자동 탐색, *IDN?, 1936-R 현재 설정, 오류 큐, 2400 출력 상태
  led-check --scan              모든 COM 포트에 무엇이 붙어 있는지 표 (어댑터 이름, *IDN?)
  led-check --off               2400 OUTPUT OFF (확인까지 재시도)
  led-check --pm-save-default   지금 1936-R 설정을 '평소 설정'으로 저장 (<home>/pm_default.json)
  led-check --pm-restore        1936-R을 '평소 설정'으로 복구 (저장된 것이 없으면 이 연구실 원래 설정)
"""
import json
import os

from .. import config
from ..newport1936 import STATE_QUERIES
from . import common

# 처음 받았을 때의 1936-R 전면 설정 (2026-11 확인)
FACTORY = {"PM:MODE?": "0", "PM:UNITs?": "3", "PM:Lambda?": "275", "PM:AUTO?": "1", "PM:RANge?": "0",
           "PM:FILTer?": "3", "PM:ANALOGFILTER?": "4", "PM:DIGITALFILTER?": "10000", "PM:DS:INTerval?": "1"}


def _default_path():
    return os.path.join(config.home(), "pm_default.json")


def build_parser():
    ap = common.parser("led-check", "장비 연결 확인 / 비상 OFF / 1936-R 복구")
    ap.add_argument("--off", action="store_true", help="2400 OUTPUT OFF")
    ap.add_argument("--scan", action="store_true", help="모든 COM 포트 확인 (장비 연결 전 진단용)")
    ap.add_argument("--pm-save-default", action="store_true", help="현재 1936-R 설정을 평소 설정으로 저장")
    ap.add_argument("--pm-restore", action="store_true", help="1936-R을 평소 설정으로 복구")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    common.setup(a)
    from ..keithley2400 import Keithley2400
    from ..newport1936 import Newport1936
    if a.scan:
        from ..newport1936 import echo_off
        from ..serialdev import kick, scan_instruments
        res = scan_instruments([("1936-R", 38400, "NEWPORT", echo_off),
                                ("2400", 9600, "KEITHLEY", kick, lambda d: d.write("*CLS", 0.1))],
                               roles={"1936-R": "pm", "2400": "k"})
        for r in res:
            print(f"{r['port']:8s} {r['kind'] or '-':7s} {r['desc'][:40]:40s} {r['idn'] or '응답 없음'}")
        return 0 if any(r["kind"] for r in res) else 1
    pm = k = None
    ok = True
    try:
        try:
            pm = Newport1936(a.pm_port)
            print(f"1936-R  {pm.port}  {pm.idn()}")
        except RuntimeError as e:
            ok = False
            print(f"1936-R  없음 — {e}")
        try:
            k = Keithley2400(a.k_port, exclude=(pm.port,) if pm else ())
            print(f"2400    {k.port}  {k.idn()}")
        except RuntimeError as e:
            ok = False
            print(f"2400    없음 — {e}")

        if k:
            if a.off:
                print(f"  OUTPUT OFF: {'확인' if k.safe_off() else '실패 — 전면 OUTPUT 버튼'}")
            print(f"  출력 {'ON !' if k.output_is_on() else 'OFF'}   오류 큐 {k.errors() or '비어 있음'}")
        if pm:
            st = pm.save_state()
            print("  설정 " + "  ".join(f"{q[3:-1]}={v}" for q, v in st.items()))
            errs = pm.errors()
            print(f"  오류 큐 {errs or '비어 있음'}")
            if a.pm_save_default:
                with open(_default_path(), "w", encoding="utf-8") as f:
                    json.dump(st, f, indent=1)
                print(f"  평소 설정 저장 → {_default_path()}")
            if a.pm_restore:
                ref = FACTORY
                if os.path.exists(_default_path()):
                    with open(_default_path(), encoding="utf-8") as f:
                        ref = json.load(f)
                pm.restore_state(ref)
                after = pm.save_state()
                skip = {"PM:RANge?"} if str(ref.get("PM:AUTO?", "")).startswith("1") else set()
                diff = {q: (ref[q], after.get(q)) for q in STATE_QUERIES if q not in skip and q in ref
                        and str(after.get(q)).strip() != str(ref[q]).strip()}
                print(f"  복구 {'OK' if not diff else f'차이 {diff}'}")
                ok &= not diff
    finally:
        for d in (pm, k):
            if d:
                d.close()
    return 0 if ok else 1


def entry():
    raise SystemExit(common.run(main, "check"))


if __name__ == "__main__":
    entry()
