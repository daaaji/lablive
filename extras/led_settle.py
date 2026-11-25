r"""
[extras — lablive 0.9.0b5 패키지 밖의 단독 스크립트. uvc275 전용 기본값(I ≤ 0.25 A, 극성 점검 기준)이 코드에 고정됨.
 lablive_shell 창에서  python <설치폴더>\extras\led_settle.py --current 0.1  (결과는 현재 폴더의 runs\)]

led_settle.py — 2400 정전류 LED 구동 + 1936-R 광전류로 settling 측정

순서
  0) 두 장비를 *IDN?으로 자동 탐색, 1936-R 현재 설정 저장
  1) 극성/배선 확인: 1 mA로 Vf 측정 (compliance 걸리거나 Vf 비정상이면 중단)
  2) range 결정: 목표 전류로 0.6 s 펄스 → 피크 광전류로 1936-R range 고정 (auto-range OFF)
  3) dark 기준선: LED off, 필터 OFF, 10 kHz × dark_s (냉각 대기 겸용)
  4) fast: data store 10 kHz (0.1 ms)로 pre + post 구간, 중간에 2400 OUTPUT ON
  5) slow: LED 켠 채로 1936-R(디지털 필터 50 ms) + 2400 Vf를 ~10 Hz polling
  6) OUTPUT OFF → fast data store 덤프 → 분석/그림
  항상: 끝나거나 에러가 나도 2400 OUTPUT OFF, 1936-R 설정 원복

사용 예
  python led_settle.py --current 0.1                (100 mA, slow 120 s)
  python led_settle.py --current 0.25 --slow 300
  python led_settle.py --current 0.05 --range 4     (range 수동 지정)

출력: runs/<시각>_<mA>mA/  meta.json dark.csv fast.csv slow.csv summary.json plot.png
"""
import argparse
import csv
import json
import os
import re
import statistics
import time
from datetime import datetime

import serial
from serial.tools import list_ports

FS = [2.5e-9 * 10 ** k for k in range(8)]  # 1936-R 전류 range 0..7 full scale (A)
BW = ["1.2 Hz", "4.8 Hz", "48 Hz", "480 Hz", "4 kHz", "43 kHz", "170 kHz", "400 kHz"]
DT = 1e-4  # CW Continuous, DS interval 1 = 0.1 ms
FLOAT = re.compile(r"[-+]?\d+\.\d*[Ee][-+]?\d+")
I_MAX = 0.25
VERSION = "2026-11-05i"


# ---------------- serial helpers ----------------
class Dev:
    def __init__(self, port, baud, term):
        self.s = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1,
                               xonxoff=False, rtscts=False, dsrdtr=False, timeout=2)
        self.term, self.port = term, port

    def w(self, cmd, pause=0.03):
        self.s.write((cmd + self.term).encode("ascii"))
        time.sleep(pause)

    def ws(self, cmd, pause=0.0):
        """명령 + *OPC? 로 실행 완료까지 대기. 2400(9600 baud, 흐름제어 없음)은 기능 전환처럼
        오래 걸리는 명령 중에 뒤따르는 명령을 놓칠 수 있어, 설정 명령은 전부 이걸로 보냄."""
        self.s.reset_input_buffer()
        self.s.write((cmd + ";*OPC?" + self.term).encode("ascii"))
        ok = self.s.read_until(b"\r").decode("ascii", "replace").strip().endswith("1")
        if not ok:
            print(f"  경고: '{cmd}' 완료 확인 실패")
        if pause:
            time.sleep(pause)
        return ok

    def q(self, cmd):
        self.s.reset_input_buffer()
        self.s.write((cmd + self.term).encode("ascii"))
        return self.s.read_until(b"\n" if self.term != "\r" else b"\r").decode("ascii", "replace").strip()

    def read_block(self, idle=0.4, hard=30):
        """끝 표시를 모르는 응답: idle초 동안 새 바이트가 없으면 종료."""
        buf, last, t0 = bytearray(), time.time(), time.time()
        while time.time() - t0 < hard:
            n = self.s.in_waiting
            if n:
                buf += self.s.read(n)
                last = time.time()
            elif time.time() - last > idle:
                break
            else:
                time.sleep(0.005)
        return buf.decode("ascii", "replace")


def find(baud, term, key, prep=None, exclude=()):
    for p in list_ports.comports():
        if p.device in exclude:
            continue
        try:
            d = Dev(p.device, baud, term)
        except serial.SerialException:
            continue
        d.s.timeout = 0.6
        if prep:
            prep(d)
        if key in d.q("*IDN?").upper():
            d.s.timeout = 2
            return d
        d.s.close()
    raise SystemExit(f"{key} 장비를 찾지 못함")


def nwp_errors(pm):
    out = []
    for _ in range(20):
        e = pm.q("ERRSTR?")
        if not e or e.startswith("0"):
            break
        out.append(e)
    return out


def k_errors(k):
    out = []
    for _ in range(20):
        e = k.q(":SYST:ERR?")
        if not e or e.startswith("0") or e.startswith("+0"):
            break
        out.append(e)
    return out


# ---------------- 1936-R ----------------
PM_STATE = ["PM:MODE?", "PM:UNITs?", "PM:Lambda?", "PM:AUTO?", "PM:RANge?", "PM:FILTer?",
            "PM:ANALOGFILTER?", "PM:DIGITALFILTER?", "PM:DS:BUFfer?", "PM:DS:INTerval?", "PM:DS:SIZE?"]


def pm_save(pm):
    return {c: pm.q(c) for c in PM_STATE}


def pm_restore(pm, st):
    def i(c):
        return int(float(st[c]))
    pm.w("PM:DS:ENable 0")
    pm.w(f"PM:MODE {i('PM:MODE?')}")
    pm.w(f"PM:UNITs {i('PM:UNITs?')}")
    pm.w(f"PM:Lambda {i('PM:Lambda?')}")
    # 실측(fw 1.2.4): PM:FILTer가 digital filter 크기를 100으로 되돌림 → FILTer를 먼저
    pm.w(f"PM:FILTer {i('PM:FILTer?')}")
    pm.w(f"PM:ANALOGFILTER {i('PM:ANALOGFILTER?')}")
    pm.w(f"PM:DIGITALFILTER {i('PM:DIGITALFILTER?')}")
    if i("PM:AUTO?") == 0:
        pm.w("PM:AUTO 0")
        pm.w(f"PM:RANge {i('PM:RANge?')}")
    else:
        pm.w("PM:AUTO 1")
    pm.w(f"PM:DS:BUFfer {i('PM:DS:BUFfer?')}")
    pm.w(f"PM:DS:SIZE {i('PM:DS:SIZE?')}")
    try:  # 실측: fw 1.2.4의 INTerval?는 ms가 아니라 설정 정수를 그대로 반환
        pm.w(f"PM:DS:INTerval {max(1, int(float(st['PM:DS:INTerval?'])))}")
    except ValueError:
        pass


OVER = 1e30  # 1936-R는 over-range/range 전환 중에 3.4E+38(FLT_MAX)을 반환


def pm_read_A(pm):
    m = FLOAT.search(pm.q("PM:P?"))
    if not m:
        return float("nan")
    v = float(m.group())
    return float("nan") if abs(v) >= OVER else v


def pick_range(pm, k, level_cmd, n=8):
    """range 7(25 mA FS)에 고정한 채 짧게 켜서 크기만 잼 → 필요한 최소 range 계산.
    auto-range는 range 0(1.2 Hz)부터 올라오느라 느리고, 전환 중 FLT_MAX를 내므로 쓰지 않음."""
    pm.w("PM:AUTO 0")
    pm.w("PM:RANge 7", pause=0.3)
    k.ws(level_cmd)
    k.ws(":OUTP ON", pause=0.15)
    vals = [pm_read_A(pm) for _ in range(n)]
    k.ws(":OUTP OFF")
    good = [abs(v) for v in vals if v == v]
    if not good:
        raise SystemExit(f"range 탐색 실패: 유효 판독 없음 {vals}")
    peak = max(good)
    rng = next((r for r, fs in enumerate(FS) if peak * 1.15 <= 0.9 * fs), 7)
    return rng, peak, vals


def ds_capture(pm, n, on_start=None, pre_s=0.0):
    """data store n점(0.1 ms 간격) 수집. on_start는 pre_s 후 호출(LED ON 등). 반환: 호출 시각(DS 기준)"""
    pm.w("PM:DS:ENable 0")
    pm.w("PM:DS:CLear")
    pm.w("PM:DS:BUFfer 0")
    pm.w("PM:DS:INTerval 1")
    pm.w(f"PM:DS:SIZE {n}", pause=0.2)
    t_en = time.perf_counter()
    pm.w("PM:DS:ENable 1", pause=0)
    t_evt = None
    if on_start:
        time.sleep(pre_s)
        t_evt = time.perf_counter() - t_en
        on_start()
    deadline = time.perf_counter() + n * DT + 5
    while time.perf_counter() < deadline:
        time.sleep(0.05)
        c = re.search(r"[-+]?\d+(\.\d*)?([Ee][-+]?\d+)?", pm.q("PM:DS:Count?"))
        if c and int(float(c.group())) >= n:
            break
    else:
        print("  경고: data store가 시간 내에 다 차지 않음")
    return t_evt


def ds_dump(pm, n, chunk=500, raw_path=None):
    vals, raw_first = [], None
    for a in range(1, n + 1, chunk):
        b = min(n, a + chunk - 1)
        pm.s.reset_input_buffer()
        pm.w(f"PM:DS:GET? {a}-{b}", pause=0)
        txt = pm.read_block()
        if raw_first is None:
            raw_first = txt
        # 응답 = 헤더(...Responsivity: x.xE-00x...) / "End of Header" / 값 / "End of Data"
        body = txt.split("End of Header", 1)[-1].split("End of Data", 1)[0]
        got = [float(x) for x in FLOAT.findall(body)]
        if len(got) != b - a + 1:
            print(f"  경고: {a}-{b} 요청 {b - a + 1}점, 파싱 {len(got)}점")
        vals += got
        print(f"\r  덤프 {len(vals)}/{n}", end="", flush=True)
    print()
    if raw_path and raw_first is not None:
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_first)
    return vals


# ---------------- 2400 ----------------
def k_setup(k, compliance, rsense=False):
    for c in ["*CLS", ":OUTP OFF", f":SYST:RSEN {'ON' if rsense else 'OFF'}",
              ":SOUR:FUNC CURR", ":SOUR:CURR:MODE FIX",
              ":SOUR:CURR:RANG:AUTO OFF", ":SOUR:CURR:RANG 1", ":SOUR:CURR:LEV 0",
              ":SENS:FUNC:CONC OFF", ':SENS:FUNC "VOLT"', f":SENS:VOLT:PROT {compliance}",
              ":SENS:VOLT:RANG 20", ":SENS:VOLT:NPLC 1", ":FORM:ELEM VOLT,CURR"]:
        k.ws(c)


def k_read(k):
    v = [float(x) for x in FLOAT.findall(k.q(":READ?"))]
    return (v[0], v[1]) if len(v) >= 2 else (float("nan"), float("nan"))


def k_setup_v(k, ilim, rsense=False):
    """정전압 구동: 모듈 단자 전압과 전류를 둘 다 실측 (compliance 중엔 setpoint ≠ 실제 전압)"""
    for c in ["*CLS", ":OUTP OFF", f":SYST:RSEN {'ON' if rsense else 'OFF'}",
              ":SOUR:FUNC VOLT", ":SOUR:VOLT:MODE FIX", ":SOUR:VOLT:RANG 20", ":SOUR:VOLT:LEV 0",
              ":SENS:FUNC:CONC ON", ':SENS:FUNC "VOLT","CURR"', f":SENS:CURR:PROT {ilim}",
              ":SENS:CURR:RANG 1", ":SENS:CURR:NPLC 1", ":FORM:ELEM VOLT,CURR"]:
        k.ws(c)


VMODE = {"on": False}


def k_tripped(k):
    # 정전류 모드는 전압 compliance, 정전압 모드는 전류 compliance
    cmd = ":SENS:CURR:PROT:TRIP?" if VMODE["on"] else ":SENS:VOLT:PROT:TRIP?"
    return k.q(cmd).strip().endswith("1")


def k_safe_off(k, tries=10):
    """OUTPUT OFF를 반드시 전달. 포트가 죽었으면(USB 재열거 등) 같은 이름으로 다시 열어 재시도."""
    for n in range(tries):
        try:
            k.ws(":OUTP OFF")
            k.ws(":SOUR:VOLT:LEV 0" if VMODE["on"] else ":SOUR:CURR:LEV 0")
            if k.q(":OUTP?").strip().endswith("0"):
                print("2400 OUTPUT OFF 확인")
                return True
        except (serial.SerialException, OSError):
            pass
        try:
            k.s.close()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.0)
        try:
            k.s = Dev(k.port, 9600, "\r").s
        except serial.SerialException:
            continue
    print("\n" + "!" * 60 + "\n!! 2400 OUTPUT OFF 실패 — 전면 패널 ON/OFF OUTPUT 버튼으로 직접 끄세요 !!\n" + "!" * 60)
    return False


# ---------------- analysis ----------------
def analyze(fast, pre_n, slow, tol):
    base = statistics.fmean(fast[:pre_n]) if pre_n else 0.0
    sig = [x - base for x in fast]
    tail = statistics.fmean(sig[-max(10, len(sig) // 10):])
    sgn = 1 if tail >= 0 else -1
    s = [sgn * x for x in sig]
    amp = abs(tail)

    def first(th, start=0):
        for i in range(start, len(s)):
            if s[i] >= th:
                return i
        return None

    i10, i90 = first(0.1 * amp), first(0.9 * amp)
    out = {"dark_offset_A": base, "fast_end_A": tail, "onset_idx": i10}
    if i10 is not None and i90 is not None:
        out["rise_10_90_ms"] = (i90 - i10) * DT * 1e3
        out["fast_peak_A"] = sgn * max(s[i10:]) if i10 < len(s) else None
    if slow:
        final = statistics.fmean([r["I_pd"] for r in slow[-max(5, len(slow) // 10):]])
        out["slow_final_A"] = final
        if out.get("fast_peak_A"):
            out["droop_pct_peak_to_final"] = 100 * (out["fast_peak_A"] - final) / out["fast_peak_A"]
        band = abs(final) * tol
        last_out = None
        for r in slow:
            if abs(r["I_pd"] - final) > band:
                last_out = r["t_s"]
        out[f"settle_time_s_within_{tol * 100:g}pct"] = last_out
        out["vf_start_V"], out["vf_end_V"] = slow[0]["Vf"], slow[-1]["Vf"]
    return out


def spectrum(x, top=5):
    """스냅샷 요약: 평균, RMS, 상위 스펙트럼 피크 (주기적 발진인지 랜덤인지 판별용)"""
    try:
        import numpy as np
    except ImportError:
        return {"mean_A": statistics.fmean(x), "rms_A": statistics.pstdev(x)}
    v = np.asarray(x, float)
    v = v - v.mean()
    amp = np.abs(np.fft.rfft(v * np.hanning(len(v)))) * 2 / (len(v) * 0.5)
    fr = np.fft.rfftfreq(len(v), DT)
    idx = np.argsort(amp[1:])[::-1][:top] + 1
    return {"mean_A": float(np.mean(x)), "rms_A": float(v.std()),
            "rms_pct": float(100 * v.std() / abs(np.mean(x))) if np.mean(x) else None,
            "peaks_Hz_A": [[round(float(fr[i]), 1), float(amp[i])] for i in idx]}


def plot(path, fast, pre_n, onset, slow, snap=None, snap_t0=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib 없음 → 그림 생략 (pip install matplotlib)")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.5))
    if fast:
        i0 = onset if onset is not None else pre_n
        a1.plot([(i - i0) * DT * 1e3 for i in range(len(fast))], fast, lw=0.6)
        a1.set_xlabel("t − onset (ms)")
        a1.set_title("fast (10 kHz data store)")
    elif snap:
        n = min(len(snap), 200)  # 처음 20 ms
        a1.plot([i * DT * 1e3 for i in range(n)], snap[:n], ".-", lw=0.6, ms=2)
        a1.set_xlabel(f"t − {snap_t0:.2f} s (ms)")
        a1.set_title("snap (10 kHz, first 20 ms)")
    a1.set_ylabel("I_pd (A)")
    if slow:
        t = [r["t_s"] for r in slow]
        a2.plot(t, [r["I_pd"] for r in slow], lw=0.8, color="C0")
        a2.set_xlabel("t since OUTPUT ON (s)")
        a2.set_ylabel("I_pd (A)", color="C0")
        b = a2.twinx()
        b.plot(t, [r["Vf"] for r in slow], lw=0.8, color="C3")
        b.set_ylabel("Vf (V)", color="C3")
        a2.set_title("slow (polling)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)


# ---------------- main ----------------
def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--current", type=float, help="정전류 구동 (A), 최대 0.25")
    g.add_argument("--vsource", type=float, help="정전압 구동 (V), 최대 12.5 — 모듈 설계 방식")
    ap.add_argument("--ilim", type=float, default=0.25, help="정전압 모드 전류 제한 (A)")
    ap.add_argument("--compliance", type=float, default=12.0)
    ap.add_argument("--pre", type=float, default=0.1, help="LED ON 전 기록 (s)")
    ap.add_argument("--fast", type=float, default=1.0, help="fast 구간 전체 길이 (s)")
    ap.add_argument("--slow", type=float, default=120.0, help="slow polling 길이 (s)")
    ap.add_argument("--dark", type=float, default=0.5, help="dark 기준선 길이 (s)")
    ap.add_argument("--cool", type=float, default=30.0, help="range 탐색 펄스 후 최소 냉각 (s)")
    ap.add_argument("--range", type=int, default=None, help="1936-R range 수동 지정 0..7")
    ap.add_argument("--tol", type=float, default=0.01, help="settling 판정 밴드 (비율)")
    ap.add_argument("--lambda_nm", type=int, default=275)
    ap.add_argument("--rsense", action="store_true", help="2400 4-wire(remote sense)로 Vf 측정")
    ap.add_argument("--snap", type=float, default=None,
                    help="LED ON 후 이 시각(s)에 10 kHz 스냅샷 (fast 구간은 생략됨)")
    ap.add_argument("--snap_len", type=float, default=0.5, help="스냅샷 길이 (s)")
    a = ap.parse_args()
    VMODE["on"] = a.vsource is not None
    if VMODE["on"]:
        if not 0 < a.vsource <= 12.5 or not 0 < a.ilim <= I_MAX:
            raise SystemExit(f"--vsource ≤ 12.5 V, --ilim ≤ {I_MAX} A")
        level_cmd, tag = f":SOUR:VOLT:LEV {a.vsource}", f"{a.vsource:g}V"
    else:
        if not 0 < a.current <= I_MAX:
            raise SystemExit(f"--current는 0 < I ≤ {I_MAX} A")
        level_cmd, tag = f":SOUR:CURR:LEV {a.current}", f"{a.current * 1e3:g}mA"
    if a.fast <= a.pre:
        raise SystemExit("--fast는 --pre보다 커야 함")

    run = os.path.join("runs", datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{tag}")
    os.makedirs(run, exist_ok=True)

    pm = find(38400, "\r", "NEWPORT", prep=lambda d: (d.w("ECHO 0", 0.2), d.s.reset_input_buffer()))
    k = find(9600, "\r", "KEITHLEY", exclude=(pm.port,))
    print(f"led_settle {VERSION}   1936-R: {pm.port}   2400: {k.port}   → {run}")

    meta = {"version": VERSION, "args": vars(a), "pm_port": pm.port, "k_port": k.port,
            "pm_idn": pm.q("*IDN?"), "k_idn": k.q("*IDN?"),
            "pm_stale_errors": nwp_errors(pm)}
    k.ws("*CLS")
    pm_state = pm_save(pm)
    meta["pm_state_before"] = pm_state
    slow, fast, dark, snap = [], [], [], []
    nf = 0
    completed = False

    try:
        # ---- 설정 ----
        if VMODE["on"]:
            k_setup_v(k, a.ilim, a.rsense)
        else:
            k_setup(k, a.compliance, a.rsense)
        meta["k_setup_errors"] = k_errors(k)
        if meta["k_setup_errors"]:
            raise SystemExit(f"2400 설정 오류: {meta['k_setup_errors']}")
        for c in ["PM:DS:ENable 0", "PM:MODE 0", "PM:UNITs 0", f"PM:Lambda {a.lambda_nm}",
                  "PM:FILTer 0", "PM:ANALOGFILTER 0", "PM:DIGITALFILTER 1"]:
            pm.w(c)

        # ---- 1) 극성/배선 ----
        if VMODE["on"]:
            # 5 V(모듈 문턱 ~6.25 V 아래)에서 대기전류만 흐르는지: iv 스윕 실측 ≈ 55 µA
            # 1 A 고정 range에선 µA 측정·1 mA 제한이 부정확 → 점검 동안만 auto range.
            # 입력 커패시터 돌입전류가 끝날 때까지(최대 3 s) 기다렸다 판정.
            k.ws(":SENS:CURR:RANG:AUTO ON")
            k.ws(":SENS:CURR:PROT 0.01")   # 1 mA 제한 + 1 A range 조합에서 2400 루프가 불안정했음
            k.ws(":SOUR:VOLT:LEV 5")
            k.ws(":OUTP ON", pause=0.1)
            trace, t0 = [], time.perf_counter()
            while time.perf_counter() - t0 < 3.0:
                v1, i1 = k_read(k)
                trip = k_tripped(k)
                trace.append([round(time.perf_counter() - t0, 3), i1, trip, v1])
                if len(trace) >= 3 and not trip and not any(x[2] for x in trace[-3:]) \
                        and abs(trace[-1][1] - trace[-2][1]) <= 0.1 * abs(trace[-1][1]) + 2e-6:
                    break
            k.ws(":OUTP OFF")
            k.ws(f":SENS:CURR:PROT {a.ilim}")
            k.ws(":SENS:CURR:RANG 1")
            meta["polarity_check"] = {"V_meas": v1, "I_at_5V": i1, "compliance_trip": trip, "trace": trace}
            print(f"5 V 점검: V_meas = {v1:.3f} V, I = {i1 * 1e6:.1f} µA, trip = {trip}  (경과 {trace[-1][0]:.2f} s)")
            for t, ii, tr, vv in trace[:12]:
                print(f"    t={t:5.2f}s  V={vv:7.3f} V  I={ii * 1e6:9.1f} µA  {'TRIP' if tr else ''}")
            if trip or not 5e-6 < i1 < 5e-4:
                raise SystemExit("5 V 대기전류 비정상 — 위 trace 붙여주세요 (iv 스윕 기준 정상 ≈ 55 µA)")
        else:
            k.ws(":SOUR:CURR:LEV 0.001")
            k.ws(":OUTP ON", pause=0.3)
            v1, i1 = k_read(k)
            trip = k_tripped(k)
            k.ws(":OUTP OFF")
            meta["polarity_check"] = {"V_at_1mA": v1, "I": i1, "compliance_trip": trip}
            print(f"1 mA 점검: Vf = {v1:.3f} V, compliance trip = {trip}")
            if trip or not 2.0 < v1 < a.compliance - 0.5:
                raise SystemExit("Vf 비정상 — 극성 반대/개방/접촉 불량 확인 후 재시도")

        # ---- 2) range ----
        if a.range is None:
            rng, peak, raw = pick_range(pm, k, level_cmd)
            t_probe_off = time.perf_counter()
            print(f"range 탐색: 피크 ≈ {peak:.3e} A → range {rng} (FS {FS[rng]:.1e} A, BW {BW[rng]})")
            meta["range_probe"] = {"peak_A": peak, "raw": raw}
        else:
            rng, t_probe_off = a.range, time.perf_counter() - a.cool
        pm.w("PM:AUTO 0")
        pm.w(f"PM:RANge {rng}", pause=0.3)
        meta["range"] = {"index": rng, "full_scale_A": FS[rng], "bandwidth": BW[rng],
                         "readback": pm.q("PM:RANge?")}
        if rng <= 3:
            print(f"  주의: range {rng} 대역폭 {BW[rng]} — ms 이하 구간은 대역폭에 깎임")
        k.ws(level_cmd)
        func = k.q(":SOUR:FUNC?")
        lev = k.q(":SOUR:VOLT:LEV?" if VMODE["on"] else ":SOUR:CURR:LEV?")
        want_f, want_l = ("VOLT", a.vsource) if VMODE["on"] else ("CURR", a.current)
        meta["k_state_verified"] = {"func": func, "level": lev}
        try:
            good = func.strip('"').startswith(want_f) and abs(float(lev) - want_l) < 1e-6 + 1e-4 * want_l
        except ValueError:
            good = False
        if not good:
            raise SystemExit(f"2400 상태 불일치: FUNC={func}, LEV={lev} (기대 {want_f} {want_l})")
        nd = round(a.dark / DT)
        print(f"dark 기준선 {a.dark} s 수집/덤프 …")
        ds_capture(pm, nd)
        dark = ds_dump(pm, nd, raw_path=os.path.join(run, "ds_raw_first_chunk.txt"))
        rem = a.cool - (time.perf_counter() - t_probe_off)
        if rem > 0:
            print(f"냉각 대기 {rem:.0f} s …")
            time.sleep(rem)

        # ---- 4) fast (또는 --snap 모드면 생략: data store는 하나뿐) ----
        t_on = {}

        def led_on():
            k.w(":OUTP ON", pause=0)
            t_on["pc"] = time.perf_counter()

        ns = round(a.snap_len / DT) if a.snap is not None else 0
        if a.snap is None:
            nf = round(a.fast / DT)
            print(f"fast 수집 {a.fast} s (LED ON @ {a.pre} s) …")
            t_evt = ds_capture(pm, nf, on_start=led_on, pre_s=a.pre)
            meta["fast_led_on_pc_offset_s"] = t_evt
        else:
            print(f"snap 모드: fast 생략, LED ON 후 {a.snap} s 시점에 {a.snap_len} s 10 kHz 스냅샷")
            led_on()

        # ---- 5) slow ----
        pm.w("PM:FILTer 2", pause=0.1)
        pm.w("PM:DIGITALFILTER 500", pause=0.1)  # 50 ms 이동평균 (FILTer 다음에 써야 유지됨)
        meta["slow_digital_filter_readback"] = pm.q("PM:DIGITALFILTER?")
        print(f"slow polling {a.slow} s …")
        with open(os.path.join(run, "slow.csv"), "w", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(["t_s", "I_pd_A", "V_module_V" if VMODE["on"] else "Vf_V", "I_led_A"])
            tend = t_on["pc"] + a.slow
            while time.perf_counter() < tend:
                t_loop = time.perf_counter()
                try:
                    ip = pm_read_A(pm)
                    tp = time.perf_counter() - t_on["pc"]
                    vf, il = k_read(k)
                except serial.SerialException as e:
                    meta["slow_aborted"] = {"t_s": time.perf_counter() - t_on["pc"], "error": str(e)}
                    print(f"\n  통신 끊김 @ {meta['slow_aborted']['t_s']:.1f} s: {e}")
                    break
                slow.append({"t_s": tp, "I_pd": ip, "Vf": vf, "I_led": il})
                cw.writerow([f"{tp:.4f}", ip, vf, il])
                f.flush()
                if ns and "snap_t0_s" not in meta and tp >= a.snap:
                    pm.w("PM:FILTer 0", pause=0.05)
                    meta["snap_t0_s"] = time.perf_counter() - t_on["pc"]
                    print(f"\n  스냅샷 @ {meta['snap_t0_s']:.2f} s …")
                    ds_capture(pm, ns)
                    pm.w("PM:FILTer 2", pause=0.1)
                    pm.w("PM:DIGITALFILTER 500", pause=0.1)
                if len(slow) % 50 == 0:
                    print(f"\r  t={tp:6.1f}s  I_pd={ip:.4e}  Vf={vf:.4f}", end="", flush=True)
                time.sleep(max(0.0, 0.1 - (time.perf_counter() - t_loop)))  # 최대 10 Hz
            print()
        if "slow_aborted" not in meta:
            meta["compliance_trip_end"] = k_tripped(k)
        meta["k_off_ok"] = k_safe_off(k)

        # ---- 6) data store 덤프 ----
        pm.w("PM:FILTer 0")
        if nf:
            print("fast data store 덤프 …")
            fast = ds_dump(pm, nf)
        if ns and "snap_t0_s" in meta:
            print("snap data store 덤프 …")
            snap = ds_dump(pm, ns)
        completed = True
    finally:
        if not meta.get("k_off_ok"):
            meta["k_off_ok"] = k_safe_off(k)
        try:
            pm_restore(pm, pm_state)
            meta["pm_state_after"] = pm_save(pm)
            meta["pm_errors_end"] = nwp_errors(pm)
            print("1936-R 설정 원복 완료")
        except Exception as e:  # noqa: BLE001
            meta["pm_restore_error"] = str(e)
            print(f"1936-R 원복 실패: {e}")
        try:
            meta["k_errors_end"] = k_errors(k)
        except Exception as e:  # noqa: BLE001
            meta["k_errors_end"] = f"조회 실패: {e}"
        meta["completed"] = completed
        with open(os.path.join(run, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    pre_n = round(a.pre / DT)
    for name, data in (("dark.csv", dark), ("fast.csv", fast)):
        with open(os.path.join(run, name), "w", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(["t_s", "I_pd_A"])
            cw.writerows([(f"{i * DT:.4f}", v) for i, v in enumerate(data)])

    summ = analyze(fast, pre_n, slow, a.tol) if fast else {}
    if snap:
        t0 = meta["snap_t0_s"]
        with open(os.path.join(run, "snap.csv"), "w", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(["t_since_on_s", "I_pd_A"])
            cw.writerows([(f"{t0 + i * DT:.4f}", v) for i, v in enumerate(snap)])
        summ["snap"] = spectrum(snap)
    if dark:
        summ["dark_mean_A"] = statistics.fmean(dark)
        summ["dark_rms_noise_A"] = statistics.pstdev(dark)
    fit = None
    if slow or fast:
        try:
            from lablive import thermfit
            fit = thermfit.fit_run(fast, pre_n, slow, "Vf")
            summ["fit"] = thermfit.public(fit)
            thermfit.plot_fit(os.path.join(run, "fit.png"), fit,
                              "V_module (V)" if VMODE["on"] else "Vf (V)")
        except ImportError:
            print("thermfit.py 또는 numpy 없음 → 지수 피팅 생략")
        except Exception as e:  # noqa: BLE001 — 피팅 실패가 측정 데이터 저장을 막지 않게
            summ["fit_error"] = str(e)
    with open(os.path.join(run, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=2,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
    plot(os.path.join(run, "plot.png"), fast, pre_n, summ.get("onset_idx"), slow,
         snap, meta.get("snap_t0_s"))
    brief = {k: v for k, v in summ.items() if k != "fit"}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    if fit:
        from lablive import thermfit
        print("\n--- 지수 피팅 ---")
        print(json.dumps(thermfit.summary_lines(fit), ensure_ascii=False, indent=2))
    print(f"\n완료 → {run}  (meta.json, summary.json, plot.png, fit.png 올려주세요)")


if __name__ == "__main__":
    main()
