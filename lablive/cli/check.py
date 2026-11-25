"""led-check — 장비 연결 확인 / 비상 OFF / 1936-R 설정 복구 (LED를 켜지 않음)

  led-check                     포트 자동 탐색, *IDN?, 1936-R 현재 설정, 오류 큐, 2400 출력 상태
  led-check --scan              모든 COM 포트에 무엇이 붙어 있는지 표 (어댑터 이름, *IDN?)
  led-check --speed             통신/판독 속도 측정 (1936-R PM:P?, 2400 설정·조회·:READ? NPLC 1/0.1)
                                2400은 :READ?에 출력 ON이 필요해 0 A 정전류 + 전압 상한 2 V로 잠깐 켬 (LED에 전류 없음)
  led-check --off               2400 OUTPUT OFF (확인까지 재시도)
  led-check --pm-save-default   지금 1936-R 설정을 '평소 설정'으로 저장 (<home>/pm_default.json)
  led-check --pm-restore        1936-R을 '평소 설정'으로 복구 (저장된 것이 없으면 이 연구실 원래 설정)
"""
import json
import os
import statistics
import time
from datetime import datetime

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
    ap.add_argument("--speed", action="store_true", help="통신/판독 속도 측정 (2400은 0 A로 출력 ON)")
    ap.add_argument("-n", type=int, default=30, help="--speed 반복 횟수")
    ap.add_argument("--pm-save-default", action="store_true", help="현재 1936-R 설정을 평소 설정으로 저장")
    ap.add_argument("--pm-restore", action="store_true", help="1936-R을 평소 설정으로 복구")
    return ap


def _timeit(fn, n):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    ts.sort()
    m = statistics.fmean(ts)
    return {"n": n, "mean_ms": 1e3 * m, "median_ms": 1e3 * ts[len(ts) // 2], "p95_ms": 1e3 * ts[int(0.95 * (n - 1))],
            "max_rate_hz": 1.0 / m}


def speed_test(pm, k, n=30, log=print):
    """반환: {항목: 통계}. 2400 :READ?는 출력이 켜져 있어야 해서 0 A 정전류(전압 상한 2 V)로 잠깐 켠다."""
    res = {}
    if pm:
        log("1936-R PM:P? …")
        res["1936-R PM:P? (판독 1회)"] = _timeit(pm.read_A, n)
    if k:
        failed, errs = k.setup_current_source(2.0)
        k.set_current(0.0)
        ok, func, lev = k.verify_current(0.0)
        if failed or errs or not ok:
            raise RuntimeError(f"2400 준비 실패 (0 A 확인 안 됨): {failed} {errs} {func} {lev}")
        log("2400 *OPC? / 설정 …")
        res["2400 조회 왕복 (*OPC?)"] = _timeit(lambda: k.dev.query("*OPC?"), n)
        res["2400 설정 1회 (명령;*OPC?)"] = _timeit(lambda: k.ws(":SOUR:CURR:LEV 0"), min(n, 15))
        try:
            k.output(True)
            if abs(k.read()[1]) > 1e-9:
                raise RuntimeError("2400 출력 전류가 0이 아님 — 중단")
            for nplc in (1, 0.1):
                k.ws(f":SENS:VOLT:NPLC {nplc:g}")
                log(f"2400 :READ? NPLC {nplc:g} …")
                res[f"2400 :READ? (NPLC {nplc:g})"] = _timeit(k.read, n)
        finally:
            off = k.safe_off()
            k.ws(":SENS:VOLT:NPLC 1")
            if not off:
                log("!! 2400 OUTPUT OFF 확인 실패 — 전면 OUTPUT 버튼으로 끌 것 !!")
    return res


def main(argv=None):
    a = build_parser().parse_args(argv)
    common.setup(a)
    from ..keithley2400 import Keithley2400
    from ..newport1936 import Newport1936
    if a.speed:
        pm = k = None
        try:
            pm = Newport1936(a.pm_port)
            k = Keithley2400(a.k_port, exclude=(pm.port,))
            print(f"1936-R {pm.port} (38400 baud), 2400 {k.port} (9600 baud), {a.n}회씩")
            res = speed_test(pm, k, a.n)
        finally:
            for d in (pm, k):
                if d:
                    d.close()
        print(f"\n{'항목':28s} {'평균 ms':>8s} {'중앙 ms':>8s} {'p95 ms':>8s} {'최대 Hz':>8s}")
        for name, r in res.items():
            print(f"{name:28s} {r['mean_ms']:8.1f} {r['median_ms']:8.1f} {r['p95_ms']:8.1f} {r['max_rate_hz']:8.1f}")
        print("최대 Hz = 1/평균 (그 동작만 연속으로 할 때의 상한). lablive 기본: 1936-R 20 Hz, 2400 2 Hz")
        p = os.path.join(config.logs_dir(), f"speed_{datetime.now():%Y%m%d_%H%M%S}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"pm_port": pm.port if pm else None, "k_port": k.port if k else None, "results": res},
                      f, ensure_ascii=False, indent=1)
        print(f"→ {p}")
        return 0
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
