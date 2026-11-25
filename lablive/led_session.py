"""LED 정전류 구동 세션 — 2400(구동) + 1936-R(광전류) + 안정도 추적을 하나로 묶는다.

통합 모듈에서 쓰는 방식:
    pm = Newport1936()
    k = Keithley2400(exclude=(pm.port,))
    with LedSession(pm, k, current=0.1, compliance=12.0) as s:   # start(): 점검 → LED ON → 샘플링
        if s.wait_stable(timeout=900):                           # 'settled'까지 대기
            ...                                                  # 다른 측정 (LED는 켜진 채 유지)
    # stop(): LED OFF 확인 → 1936-R 설정 원복 → meta.json

안전:
- 전류 상한 i_max는 필수 (LED마다 다름 — 프로파일에서 가져옴)
- 시작 전 저전류 극성/배선 점검 (polarity_check), 프로파일에 기록된 점검 전압과 비교, 구동 직전 2400 상태 재확인
- compliance trip(요청 전류를 못 흘림) 또는 통신 끊김 → watchdog이 즉시 LED OFF, state='fault'
- 어떤 경로로 끝나든 stop()에서 OUTPUT OFF를 :OUTP?로 확인
"""
import json
import math
import os
import sys
import threading
import time
from datetime import datetime

from . import config
from .newport1936 import BW, FS
from .sampler import Sampler
from .stability import StabilityTracker


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


CHECK_TOL_V = 0.3   # 프로파일 점검 전압과 이만큼 다르면 다른 LED/배선으로 보고 중단


def polarity_check(k, compliance, i_check=1e-3, v_min=0.5, log=print):
    """저전류로 잠깐 켜서 단자 전압 확인 → dict. 실패 시 RuntimeError (출력은 끈 상태로).
    - trip (전압 상한 도달): 극성 반대 / 개방 / 접촉 불량 / compliance가 Vf보다 낮음
    - V < v_min: 단락 (LED가 아닌 것이 연결됨)"""
    k.set_current(i_check)
    try:
        k.output(True)
        time.sleep(0.3)
        v, _ = k.read()
        trip = k.tripped()
    finally:
        k.output(False)
    res = {"I_A": i_check, "V": v, "trip": trip, "V_at_1mA": v if abs(i_check - 1e-3) < 1e-9 else None}
    log(f"극성 점검 {i_check * 1e3:g} mA: V = {v:.3f} V, trip = {trip}")
    if trip or not math.isfinite(v) or v >= compliance - 0.05:
        raise RuntimeError(f"극성 점검 실패 ({i_check * 1e3:g} mA에서 전압 상한 {compliance:g} V 도달) — "
                           f"극성 반대 / 개방 / 접촉 불량, 또는 compliance가 LED Vf보다 낮음")
    if v < v_min:
        raise RuntimeError(f"극성 점검 실패 ({i_check * 1e3:g} mA에서 V = {v:.3f} V < {v_min} V) — 단락 의심")
    return res


def compare_check(profile, res):
    """프로파일에 기록된 점검 결과와 비교. 반환: (ok, 메시지). 기록이 없거나 전류가 다르면 (True, ...)"""
    ref = profile.get("check") if profile else None
    if not ref or ref.get("V") is None:
        return True, "점검 기준 없음 (이번 값을 기록)"
    if abs(ref["I_A"] - res["I_A"]) > 0.02 * res["I_A"]:
        return True, f"점검 전류가 다름 ({ref['I_A'] * 1e3:g} vs {res['I_A'] * 1e3:g} mA) — 비교 생략"
    dv = res["V"] - ref["V"]
    msg = f"점검 V {res['V']:.3f} V vs 프로파일 {ref['V']:.3f} V ({1e3 * dv:+.0f} mV)"
    return abs(dv) <= CHECK_TOL_V, msg


class LedSession:
    def __init__(self, pm, k, current, compliance=12.0, *, i_max, lambda_nm=None, pm_range=None,
                 tol=0.01, window_s=60.0, vf_guard=None, pd_rate=20.0, k_rate=2.0,
                 trip_every=4, stop_on_trip=True, rsense=False, digital_filter=None,
                 fit_every_s=15.0, fit_min_s=20.0, run_dir=None, log=print,
                 profile=None, note="", settle_margin=0.5, check_current=1e-3, reset_check=False,
                 mode="settle", pulse_widths_ms=None):
        if i_max is None or not i_max > 0:
            raise ValueError("i_max(전류 상한) 필요 — LED 정격에서 정할 것")
        if not 0 < current <= i_max:
            raise ValueError(f"current는 0 < I ≤ i_max = {i_max} A")
        if mode not in ("settle", "pulse"):
            raise ValueError("mode는 settle 또는 pulse")
        # pulse: 셔터 등으로 빛이 켜졌다 꺼졌다 하는 측정 (캘리브레이션). 안정도 피팅을 하지 않고(펄스 데이터에선
        #   의미 없고 무거움) 펄스 폭·도달률을 실시간 계산. range는 열림 기준으로 고정해야 함 (아래 start 참고).
        self.mode = mode
        self.pulse_widths_ms = [float(x) for x in pulse_widths_ms] if pulse_widths_ms else None
        if mode == "pulse":
            fit_every_s = 0
        self.pm, self.k = pm, k
        self.current, self.compliance = current, compliance
        self.lambda_nm, self.pm_range, self.tol, self.window_s = lambda_nm, pm_range, tol, window_s
        self.vf_guard, self.pd_rate, self.k_rate = vf_guard, pd_rate, k_rate
        self.trip_every, self.stop_on_trip, self.rsense = trip_every, stop_on_trip, rsense
        # 1936-R 디지털 필터 점 수 (10 kHz 기준). None = 판독 간격에 맞춤 (20 Hz → 500점 = 50 ms)
        self.filter_auto = digital_filter is None       # True면 start()에서 실제 판독 간격을 재서 다시 맞춤
        if digital_filter is None:
            digital_filter = int(min(10000, max(1, round(10000.0 / pd_rate))))
        if not 1 <= int(digital_filter) <= 10000:
            raise ValueError("1936-R 디지털 필터는 1–10000점")
        self.digital_filter, self.log = int(digital_filter), log
        tag = f"_{profile.name}" if profile else ""
        self.run_dir = run_dir or os.path.join(
            config.runs_dir(), datetime.now().strftime("%Y%m%d_%H%M%S") + f"_live{tag}_{current * 1e3:g}mA")
        self.profile, self.note = profile, note
        self.i_max = i_max
        self.check_current, self.reset_check = min(check_current, current), reset_check
        self.seg_t0 = 0.0          # 실행 중 전류를 바꾸면 그 시각부터 안정도 판정을 새로 (segment)
        self.segments = []
        self._range_grace = -1.0
        self.args = {k_: v for k_, v in locals().items() if k_ not in ("self", "pm", "k", "log", "profile")}
        self.args["profile"] = profile.name if profile else None
        self.prior, self.prior_how = (profile.model_for(current) if profile else (None, "no profile"))
        if mode == "pulse":
            self.prior, self.prior_how = None, "pulse mode (no stability model)"
        self._tr_kw = dict(window_s=window_s, fit_every_s=fit_every_s, fit_min_s=fit_min_s)
        self.settle_margin = settle_margin
        self.tracker = StabilityTracker(tol=tol, prior=self.prior, settle_margin=settle_margin, **self._tr_kw)
        self._prior_logged = False
        self.events, self.meta = [], {}
        self.fault = None
        self.led_on = False
        self.t_on = self.t_off = None
        self.started = self.stopped = False
        self.pd = self.kk = None
        self.pm_state = None
        self._over = 0
        self._k_n = 0
        self._vf_low_logged = False
        self._halt = threading.Event()
        self._off_lock = threading.Lock()
        self._watch = None
        self.pd_timing = {"rtt_ms": None, "gaps": 0, "gap_max_ms": 0.0}
        self._pd_last_t = None
        self._pd_pause = threading.Event()        # data store 캡처 중 PM:P? 폴링 멈춤
        self.capture = None                       # 진행 중 캡처 상태 (GUI 표시용)
        self.captures = []
        self._pulse_cache = (None, -1e9)
        self._seek_floor = None
        self._paused = []                         # (시작, 끝) — 캡처로 폴링을 멈춘 구간 (판독 간격 통계에서 뺌)

    # ---- 공통 ----
    def clock(self):
        return time.perf_counter() - self.t_on if self.t_on else 0.0

    def event(self, msg):
        t = self.clock()
        self.events.append((round(t, 2), msg))
        self.log(f"[{t:8.2f} s] {msg}")

    # ---- 시작 ----
    def start(self):
        if self.started:
            return
        self.started = True
        os.makedirs(self.run_dir, exist_ok=True)
        pm, k = self.pm, self.k
        # 피팅·그리기 스레드가 바쁠 때 샘플러가 GIL을 기다리는 시간 (기본 5 ms 단위) 줄이기
        self._old_switch = sys.getswitchinterval()
        sys.setswitchinterval(min(self._old_switch, 0.001))
        self._range_key = f"{self.current * 1e3:g}"
        if self.mode == "pulse" and self.pm_range is None and self.profile:
            r = (self.profile.get("pulse_ranges") or {}).get(self._range_key)
            if r is not None:
                self.pm_range = int(r)
                self._range_from_profile = True
        self.meta.update(profile=self.args["profile"], note=self.note,
                         prior=({k_: v for k_, v in self.prior.items()} if self.prior else None),
                         prior_status=self.prior_how)
        self.meta.update(args=self.args, started=datetime.now().isoformat(timespec="seconds"),
                         pm_port=pm.port, k_port=k.port, pm_idn=pm.idn(), k_idn=k.idn(),
                         pm_stale_errors=pm.errors())
        self.pm_state = pm.save_state()
        self.meta["pm_state_before"] = self.pm_state
        try:
            failed, errs = k.setup_current_source(self.compliance, self.rsense)
            self.meta["k_setup"] = {"unconfirmed": failed, "errors": errs}
            if failed or errs:
                raise RuntimeError(f"2400 설정 실패: 미확인 {failed}, 오류 {errs}")

            chk = polarity_check(k, self.compliance, self.check_current, log=self.log)
            self.meta["polarity_check"] = chk
            if self.profile:
                ok, msg = compare_check(self.profile, chk)
                self.meta["polarity_vs_profile"] = msg
                self.log(f"  {msg}")
                if not ok and not self.reset_check:
                    raise RuntimeError(f"{msg} — 허용 ±{CHECK_TOL_V} V 초과. 다른 LED가 연결됐거나 배선/접촉 문제. "
                                       f"같은 LED가 맞으면 --reset-check 로 기준 갱신")
                if self.reset_check or not self.profile.get("check"):
                    self.profile.update(check={"I_A": chk["I_A"], "V": chk["V"],
                                               "recorded": datetime.now().isoformat(timespec="seconds")})

            rb = pm.configure_current(self.lambda_nm, self.digital_filter)
            self.meta["pm_digital_filter_readback"] = rb
            self.meta["pm_filter"] = {"points": self.digital_filter, "ms": self.digital_filter / 10.0,
                                      "read_interval_ms": 1000.0 / self.pd_rate}
            try:
                if int(float(rb)) != self.digital_filter:
                    self.log(f"주의: 1936-R 디지털 필터 확인값 {rb} ≠ 설정 {self.digital_filter}")
            except (TypeError, ValueError):
                pass
            pm.set_range(self.pm_range if self.pm_range is not None else 7)
            k.set_current(self.current)
            ok, func, lev = k.verify_current(self.current)
            self.meta["k_state_verified"] = {"func": func, "level": lev}
            if not ok:
                raise RuntimeError(f"2400 상태 불일치: FUNC={func}, LEV={lev}")

            k.output(True)
            self.t_on = time.perf_counter()
            self.led_on = True
            self.event(f"LED ON {self.current * 1e3:g} mA (cap {self.compliance:g} V)"
                       + ("  [PULSE mode]" if self.mode == "pulse" else ""))

            if self.pm_range is None:   # range 7에서 크기만 보고 고정 (auto-range는 전환 중 FLT_MAX)
                probe = [pm.read_A() for _ in range(6)]
                good = [abs(v) for v in probe if math.isfinite(v)]
                if not good:
                    raise RuntimeError(f"range 탐색 실패 — 유효 판독 없음 {probe}")
                r = pm.range_for(max(good))
                pm.set_range(r)
                self.meta["range_probe"] = probe
                self.event(f"1936-R range {r} (FS {FS[r]:.1e} A, BW {BW[r]})")
            elif getattr(self, "_range_from_profile", False):
                self.event(f"1936-R range {pm.range} from profile (last pulse run at {self._range_key} mA)")
            if self.mode == "pulse" and self.pm_range is None:
                self.event("PULSE mode: range chosen from current light - if shutter was closed, first pulse "
                           "may be lost to over-range (set range; kept in profile for next time)")
            self.meta["pm_range"] = pm.range
            self._tune_filter()
            if self.prior:
                self.event(f"profile {self.args['profile']}: model {self.prior['how']}, "
                           f"±{100 * self.tol:g}% expected at {fmt_t(self.tracker.prior_t_settle)}")
            elif self.profile:
                self.event(f"profile {self.args['profile']}: no model for this current -> free fit")
        except BaseException:
            self.stop()
            raise

        self.pd = Sampler("1936-R", self._read_pd, 1 / self.pd_rate, self.clock, ("I_pd_A", "range"),
                          os.path.join(self.run_dir, "pd.csv"), self._on_pd, stamp="fn")
        self.kk = Sampler("2400", self._read_k, 1 / self.k_rate, self.clock, ("V", "I_A", "trip"),
                          os.path.join(self.run_dir, "k.csv"))
        self.tracker.start()
        self.pd.start()
        self.kk.start()
        self._watch = threading.Thread(target=self._watchdog, name="watchdog", daemon=True)
        self._watch.start()

    def _tune_filter(self):
        """PM:P? 왕복 시간을 재서 실제로 낼 수 있는 판독 간격을 알아내고, 자동 필터면 그 간격에 맞춘다.
        (b6까지는 설정 Hz로 필터를 정해, 50 Hz 설정에 실제 ~40 Hz일 때 필터 20 ms < 간격 24 ms였음)"""
        pm = self.pm
        rtt, _ = pm.round_trip(12)
        period = 1.0 / self.pd_rate
        achieved = max(period, rtt)
        self.pd_timing["rtt_ms"] = 1e3 * rtt
        self.pd_timing["expected_interval_ms"] = 1e3 * achieved
        if self.filter_auto:
            pts = pm.filter_for(1.0 / achieved)
            if pts != self.digital_filter:
                rb = pm.set_digital_filter(pts)
                self.meta["pm_digital_filter_readback"] = rb
                self.digital_filter = pts
        self.meta["pm_filter"] = {"points": self.digital_filter, "ms": self.digital_filter / 10.0,
                                  "read_interval_ms": 1e3 * period, "round_trip_ms": 1e3 * rtt,
                                  "auto": self.filter_auto}
        if rtt > 1.05 * period:
            self.event(f"1936-R round trip {1e3 * rtt:.1f} ms > set {1e3 * period:.0f} ms -> "
                       f"actual ~{1 / rtt:.0f} Hz (set {self.pd_rate:g})")
        self.event(f"1936-R read {min(self.pd_rate, 1 / rtt):.3g} Hz, digital filter {self.digital_filter} pts "
                   f"({self.digital_filter / 10:g} ms{', = read interval' if self.filter_auto else ''})")

    # ---- 샘플러 콜백 ----
    def _read_pd(self):
        if self._pd_pause.is_set():                   # data store 캡처 중 — 포트를 캡처가 씀
            self._halt.wait(0.05)
            return None
        t, v = self.pm.read_A_at(self.clock)
        r = self.pm.range
        if t < self._range_grace:                     # range/필터를 막 바꾼 직후 (필터 과도 구간)
            return (t, math.nan, r)
        if not math.isfinite(v) or abs(v) > 0.95 * FS[r]:
            self._over += 1
            pulse = self.mode == "pulse"
            if self._over >= (1 if pulse else 2) and r < 7:
                # 펄스 모드: 한 칸씩 올리면 펄스마다 한 칸 → 펄스를 여러 개 잃음. 맨 위로 갔다가
                #   열림 수준(이번 range 상한 이상)을 한 번 읽으면 거기에 맞는 range로 내려옴
                nr = 7 if pulse else r + 1
                self.pm.set_range(nr)
                self._over = 0
                self._seek_floor = 0.9 * FS[r] if pulse else None
                self.event(f"over-range -> 1936-R range {nr}" + (" (seek open level)" if pulse else ""))
            return (t, math.nan, r)
        self._over = 0
        if self._seek_floor is not None and abs(v) >= self._seek_floor:
            self._seek_floor = None
            nr = self.pm.range_for(abs(v))
            if nr < r:
                self.pm.set_range(nr)
                self._range_grace = self.clock() + 0.05 + self.digital_filter * 1e-4
                self.event(f"open level {abs(v) * 1e6:.4g} uA -> 1936-R range {nr}")
        return (t, v, r)

    GAP_WARN_S = 0.2

    def _on_pd(self, row):
        t = row[0]
        if self._pd_last_t is not None and self.capture is None:
            gap = t - self._pd_last_t
            lim = max(self.GAP_WARN_S, 4.0 / self.pd_rate)
            if gap > lim:
                pt = self.pd_timing
                pt["gaps"] += 1
                pt["gap_max_ms"] = max(pt["gap_max_ms"], 1e3 * gap)
                if pt["gaps"] <= 3 or pt["gaps"] % 20 == 0:
                    self.event(f"1936-R sampling gap {1e3 * gap:.0f} ms at {self._pd_last_t:.1f} s "
                               f"(#{pt['gaps']}{', further gaps counted only' if pt['gaps'] == 3 else ''})")
        self._pd_last_t = t
        if self.led_on:
            self.tracker.add(t - self.seg_t0, row[1])

    # ---- 실행 중 변경 ----
    def set_current_live(self, amps):
        """LED를 켠 채 전류 변경. 안정도 판정(최종값 피팅)은 이 시각부터 새로 시작 (프로파일 곡선은 쓰지 않음 —
        냉간 시작이 아니므로). 회차 비교(history)에는 첫 전류 구간만 쓰인다."""
        if not self.led_on or self.stopped or self.fault:
            raise RuntimeError("LED가 켜져 있지 않음")
        if not 0 < amps <= self.i_max * (1 + 1e-9):
            raise ValueError(f"전류는 0 < I ≤ i_max {self.i_max * 1e3:g} mA")
        old = self.current
        if abs(amps - old) <= 1e-9:
            return
        last = self.pd.last() if self.pd else None
        y_last = last[1] if last else math.nan
        if not self.k.set_current(amps):
            raise RuntimeError("2400 전류 설정 응답 없음")
        ok, func, lev = self.k.verify_current(amps)
        if not ok:
            self.fault = f"current change not confirmed (FUNC={func}, LEV={lev})"
            raise RuntimeError(self.fault)
        t = self.clock()
        old_tr = self.tracker
        self.segments.append({"t_s": t, "from_A": old, "to_A": amps,
                              "t_first_settled_prev": (old_tr.t_first_settled + self.seg_t0)
                              if old_tr.t_first_settled is not None else None})
        self.current = amps
        self.seg_t0 = t                               # 먼저 (새 tracker에 이전 기준 시각이 섞이지 않게)
        self.tracker = StabilityTracker(tol=self.tol, prior=None, settle_margin=self.settle_margin, **self._tr_kw)
        self.tracker.start()
        old_tr.stop()
        self._vf_low_logged = False
        if self.pm_range is None and math.isfinite(y_last) and old > 0:
            r = self.pm.range_for(abs(y_last) * amps / old)
            if r != self.pm.range:
                self._range_grace = self.clock() + 0.6
                self.pm.set_range(r)
        self.event(f"CURRENT {old * 1e3:g} -> {amps * 1e3:g} mA (stability tracking restarted)")

    def set_compliance_live(self, volts):
        kl = self.kk.last() if self.kk else None
        v_now = kl[1] if kl else math.nan
        if not 0 < volts <= 21:
            raise ValueError("전압 상한은 0 < V ≤ 21 (2400 1 A range 한계)")
        if self.led_on and math.isfinite(v_now) and volts < v_now + 0.3:
            raise ValueError(f"현재 단자 V {v_now:.3f} V + 0.3 V보다 낮게는 불가 (바로 trip)")
        if self.led_on or self.started:
            if not self.k.ws(f":SENS:VOLT:PROT {volts:g}"):
                raise RuntimeError("2400 응답 없음")
            self.event(f"compliance {self.compliance:g} -> {volts:g} V")
        self.compliance = volts

    def set_vf_guard(self, volts):
        self.vf_guard = volts or None
        self._vf_low_logged = False
        if self.started:
            self.event(f"V guard -> {volts or 'off'}")

    def set_tol(self, tol=None, margin=None):
        if tol is not None:
            if not 0 < tol < 0.5:
                raise ValueError("tol은 0 ~ 0.5")
            self.tol = self.tracker.tol = tol
        if margin is not None:
            if not 0 < margin <= 1:
                raise ValueError("margin은 0 ~ 1")
            self.settle_margin = self.tracker.settle_margin = margin

    def set_note(self, note):
        self.note = note
        self.meta["note"] = note

    def _read_k(self):
        if not self.led_on:
            return None
        v, i = self.k.read()
        self._k_n += 1
        if self._k_n == 1 and self.prior and self.prior.get("V_start") and self.prior.get("V_final"):
            span = self.prior["V_start"] - self.prior["V_final"]
            dv = v - self.prior["V_start"]
            self.meta["V_first"] = v
            # 정전류에서 시작 V가 기준과 다르다 = 시작 온도가 다르다 (또는 2-wire 접촉저항 변화)
            #   낮음: 이미 데워진 상태 → 남은 변화가 기준보다 작음
            #   높음: 기준 회차가 덜 식은 상태였음 → 기준 곡선이 남은 변화를 과소평가 → SETTLED가 일찍 뜸 (더 위험)
            if span > 0 and abs(dv) > 0.1 * span:
                why = "warmer" if dv < 0 else "colder"
                self.tracker.reject_prior(f"start V {1e3 * dv:+.0f} mV vs profile ({why} start)")
                self.event(f"START STATE differs: V {v:.3f} V vs profile {self.prior['V_start']:.3f} V "
                           f"({1e3 * dv:+.0f} mV, {why}) -> profile curve not used, free fit")
        trip = math.nan
        if self._k_n % self.trip_every == 0:
            trip = 1.0 if self.k.tripped() else 0.0
            if trip and self.stop_on_trip and self.fault is None:
                self.fault = "compliance trip (V would exceed cap)"
        if (self.vf_guard and math.isfinite(v) and v < self.vf_guard and not self._vf_low_logged):
            self._vf_low_logged = True
            self.event(f"WARN V {v:.3f} V < guard {self.vf_guard:g} V (profile vf_guard)")
        return (v, i, trip)

    def _watchdog(self):
        while not self._halt.wait(0.3):
            pf = self.tracker._pfit
            if pf and pf["mismatch"] and not self._prior_logged and not self.tracker.prior_reject_reason:
                self._prior_logged = True
                self.event(f"PROFILE MISMATCH (shape rms {pf['rms_pct']:.2f}% > {pf['rms_limit_pct']:.2f}%) "
                           f"-> free fit")
            if self.fault is None:
                for s in (self.pd, self.kk):
                    if s is not None and s.error is not None:
                        self.fault = f"{s.name} comm error: {s.error}"
                        break
            if self.fault and self.led_on:
                self.event(f"FAULT: {self.fault} -> LED OFF")
                self._led_off()

    def _led_off(self):
        with self._off_lock:
            was_on = self.led_on
            self.led_on = False          # 먼저 내려야 꺼지는 동안의 0 A가 판정/피팅에 안 섞임
            if was_on and self.t_on:
                self.t_off = self.clock()
            if self.kk is not None:
                self.kk.stop()
            ok = self.k.safe_off()
            self.meta["k_off_ok"] = ok
            if not ok:
                self.event("!! 2400 OUTPUT OFF FAILED - press OUTPUT on the front panel !!")
            return ok

    # ---- 조회 ----
    def status(self):
        st = self.tracker.status()
        o = self.seg_t0                     # tracker 시각(구간 기준) → LED ON 기준
        if o:
            if "t" in st:
                st["t"] += o
            if st.get("t_first_settled") is not None:
                st["t_first_settled"] += o
            if st.get("fit"):
                st["fit"] = dict(st["fit"], t_settle=st["fit"]["t_settle"] + o)
        st.update(seg_t0=o, segments=list(self.segments), i_max=self.i_max, note=self.note)
        kl = self.kk.last() if self.kk else None
        v = kl[1] if kl else math.nan
        st.update(led_on=self.led_on, fault=self.fault, current_A=self.current,
                  compliance_V=self.compliance, t_since_on=self.clock() if self.t_on else None,
                  V=v, I_led=kl[2] if kl else math.nan, pd_range=self.pm.range,
                  vf_guard=self.vf_guard,
                  vf_low=bool(self.vf_guard and math.isfinite(v) and v < self.vf_guard),
                  events=list(self.events[-6:]), run_dir=self.run_dir, mode=self.mode,
                  pd_timing=dict(self.pd_timing), capture=dict(self.capture) if self.capture else None)
        st["t_off"] = self.t_off
        if self.mode == "pulse":
            st["pulse"] = self.pulse_result()
            if st["state"] not in ("fault",):
                st["state"] = "pulse"
        if self.fault:
            st["state"] = "fault"
        elif self.stopped:
            st["last_state"] = st["state"]
            st["state"] = "stopped"
        return st

    def pulse_result(self, max_age_s=1.0):
        """펄스 분석 (1초마다 다시 계산, 그 사이엔 캐시)."""
        from . import pulses
        res, at = self._pulse_cache
        now = time.perf_counter()
        if res is not None and now - at < max_age_s:
            return res
        pd = self.snapshot_pd()
        if pd is None or len(pd) < 20:
            return None
        t, y = pd[:, 0], pd[:, 1]
        m = t >= 0
        if self.t_off is not None:
            m &= t <= self.t_off
        res = pulses.analyze(t[m], y[m], nominal_ms=self.pulse_widths_ms, filter_s=self.digital_filter * 1e-4)
        self._pulse_cache = (res, now)
        return res

    def snapshot_pd(self):
        """(n, 3): t_s, I_pd_A, range"""
        return self.pd.snapshot() if self.pd else None

    def snapshot_k(self):
        """(n, 4): t_s, V, I_A, trip"""
        return self.kk.snapshot() if self.kk else None

    def wait_stable(self, timeout=None, accept=("settled",), hold_s=0.0, poll_s=0.5):
        """state가 accept 중 하나로 hold_s 동안 유지되면 True, fault/timeout이면 False."""
        t0 = time.perf_counter()
        since = None
        while True:
            st = self.status()
            if st["state"] == "fault":
                return False
            if st["state"] in accept:
                since = since or time.perf_counter()
                if time.perf_counter() - since >= hold_s:
                    return True
            else:
                since = None
            if timeout and time.perf_counter() - t0 > timeout:
                return False
            time.sleep(poll_s)

    # ---- 종료 ----
    def stop(self):
        if self.stopped:
            return
        final = None
        try:
            final = self.status() if self.t_on else None   # 'stopped'로 바뀌기 전 마지막 판정
        except Exception:  # noqa: BLE001
            pass
        if final and final.get("pulse"):
            final["pulse"] = "-> meta.pulse_summary / pulses.csv"
        self.stopped = True
        self._halt.set()
        self._pd_pause.clear()
        self.tracker.stop()              # 꺼진 뒤 데이터로 재피팅하지 않게 먼저
        self._led_off()
        if self.pd is not None:
            self.pd.stop()
        if self.pm_state:
            try:
                self.pm.restore_state(self.pm_state)
                self.meta["pm_state_after"] = self.pm.save_state()
                self.meta["pm_errors_end"] = self.pm.errors()
            except Exception as e:  # noqa: BLE001
                self.meta["pm_restore_error"] = str(e)
                self.log(f"1936-R 원복 실패: {e}")
        try:
            self.meta["k_errors_end"] = self.k.errors()
        except Exception as e:  # noqa: BLE001
            self.meta["k_errors_end"] = f"조회 실패: {e}"
        if self.t_on:
            self.meta["duration_s"] = self.clock()
        if hasattr(self, "_old_switch"):
            sys.setswitchinterval(self._old_switch)
        self.meta["pd_timing"] = self._timing_stats()
        if self.mode == "pulse" and self.t_on:
            self._save_pulses()
        if self.captures:
            self.meta["ds_captures"] = self.captures
        first_settled = (self.segments[0]["t_first_settled_prev"] if self.segments
                         else self.tracker.t_first_settled)
        self.meta.update(lablive_version=_version(), final_status=final, events=self.events, fault=self.fault,
                         t_first_settled=first_settled, t_off=self.t_off, segments=self.segments,
                         current_final_A=self.current, note=self.note,
                         prior_fit=self.tracker._pfit if not self.segments else None)
        if self.profile and self.t_on and self.mode == "settle":
            try:
                self.profile.register_run(self.run_dir)
            except Exception as e:  # noqa: BLE001
                self.log(f"프로파일 회차 등록 실패: {e}")
        if self.profile and self.t_on and self.mode == "pulse" and self.pm.range is not None:
            try:                         # 다음 펄스 측정은 처음부터 이 range로 (닫힌 셔터에서 고르면 첫 펄스가 과범위)
                pr = dict(self.profile.get("pulse_ranges") or {})
                if pr.get(self._range_key) != self.pm.range:
                    pr[self._range_key] = self.pm.range
                    self.profile.update(pulse_ranges=pr)
                    self.log(f"프로파일 {self.profile.name}: 펄스 range {self.pm.range} 저장 ({self._range_key} mA)")
            except Exception as e:  # noqa: BLE001
                self.log(f"프로파일 펄스 range 저장 실패: {e}")
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.run_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2, default=_json_default)
        self.log(f"LED OFF{' 확인' if self.meta.get('k_off_ok') else ' 확인 실패!'} / 1936-R 원복 → {self.run_dir}")

    # ---- data store 고속 캡처 ----
    def ds_capture(self, seconds, interval=10, nominal_ms=None, progress=None, cancel=None):
        """1936-R 내부 data store로 seconds 동안 interval×0.1 ms 간격 캡처 → 덤프 → ds_HHMMSS.csv + 펄스 분석.
        LED는 켠 채 유지, 2400 기록도 계속. 캡처·덤프 동안 PM:P? 폴링(pd.csv)은 멈춤.
        덤프는 RS-232 38400 baud라 초당 약 270점 → 10만 점이면 약 6분. progress(phase, done, total) 콜백.
        cancel: threading.Event — 설정되면 캡처를 일찍 끝내고 그때까지 저장된 점만 덤프.
        반환: dict (csv 경로, 점 수, 펄스 분석 요약)."""
        if not self.led_on or self.stopped:
            raise RuntimeError("LED가 켜져 있지 않음")
        if self.capture is not None:
            raise RuntimeError("이미 캡처 중")
        interval = int(interval)
        if not 1 <= interval <= 10000:
            raise ValueError("간격은 1–10000 (×0.1 ms)")
        n = int(round(seconds * 10000 / interval))
        if not 10 <= n <= self.pm.DS_MAX:
            raise ValueError(f"점 수 {n} — 10 ~ {self.pm.DS_MAX} (길이 × 10000 / 간격)")
        from . import pulses
        pm = self.pm
        prog = progress or (lambda *a: None)
        self.capture = {"phase": "start", "done": 0, "total": n, "interval": interval, "seconds": seconds}
        t_pause = self.clock()
        self._pd_pause.set()
        filt_changed = False
        stamp = datetime.now().strftime("%H%M%S")
        try:
            with pm.dev.lock:             # 진행 중 PM:P? 왕복이 끝나길 기다림
                pass
            filt = max(1, interval)          # 저장 간격만큼 이동평균 (anti-alias). 1 = 필터 사실상 없음
            if filt != self.digital_filter:
                pm.set_digital_filter(filt)
                filt_changed = True
            time.sleep(0.05 + filt * 1e-4)
            size, iv = pm.ds_start(n, interval)
            t_en = self.clock()
            if size is not None and size < n:
                self.event(f"data store SIZE {size} < requested {n} (device limit)")
                n = size
            if iv is not None and iv != interval:
                raise RuntimeError(f"PM:DS:INTerval 확인값 {iv} ≠ {interval}")
            self.event(f"DS CAPTURE start: {seconds:g} s, {interval / 10:g} ms step, {n} pts")
            self.capture.update(phase="capture", t0=t_en, total=n)
            t_end = time.perf_counter() + n * interval * 1e-4 + 3.0
            cnt = 0
            while time.perf_counter() < t_end:
                if self._halt.wait(0.5) or (cancel is not None and cancel.is_set()):
                    break
                cnt = pm.ds_count() or 0
                self.capture["done"] = cnt
                prog("capture", cnt, n)
                if cnt >= n:
                    break
            pm.ds_stop()
            cnt = pm.ds_count() or cnt
            if cnt < n and not self._halt.is_set():
                self.event(f"DS capture stopped at {cnt}/{n} pts")
            self.capture.update(phase="dump", done=0, total=cnt)
            vals = []
            chunk = 500
            t_d = time.perf_counter()
            for a in range(1, cnt + 1, chunk):
                b = min(cnt, a + chunk - 1)
                got = pm.ds_get(a, b)
                if len(got) != b - a + 1:
                    self.event(f"DS dump {a}-{b}: got {len(got)} values (expected {b - a + 1})")
                    got = (got + [math.nan] * (b - a + 1))[:b - a + 1]
                vals += got
                self.capture["done"] = len(vals)
                prog("dump", len(vals), cnt)
                if self._halt.is_set():
                    break
            dump_s = time.perf_counter() - t_d
        finally:
            if filt_changed:
                try:
                    pm.set_digital_filter(self.digital_filter)
                except Exception:  # noqa: BLE001
                    pass
            self._range_grace = self.clock() + 0.05 + self.digital_filter * 1e-4
            self._pd_last_t = None
            self._pd_pause.clear()
            t_resume = self.clock()
            self._paused.append((t_pause, t_resume))
            cap, self.capture = self.capture, None
        import numpy as np
        dt = interval * 1e-4
        t = t_en + dt * np.arange(len(vals))
        y = np.array(vals, float)
        path = os.path.join(self.run_dir, f"ds_{stamp}.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("t_s,I_pd_A\n")
            for a, b in zip(t, y):
                f.write(f"{a:.6f},{'' if not math.isfinite(b) else f'{b:.7E}'}\n")
        res = pulses.analyze(t, y, nominal_ms=nominal_ms or self.pulse_widths_ms, dt_ref=dt, filter_s=filt * 1e-4)
        pulses.save_all(self.run_dir, t, y, res, prefix=f"ds_{stamp}_pulses",
                        title=f"{os.path.basename(self.run_dir)}  DS {interval / 10:g} ms step")
        lines = pulses.summary_lines(res, f"ds_{stamp}.csv")
        rec = {"csv": os.path.basename(path), "t0_s": t_en, "interval_ms": interval / 10, "n": int(len(y)),
               "n_requested": cap["total"] if cap else n, "dump_s": round(dump_s, 1),
               "pts_per_s_dump": round(len(y) / dump_s, 1) if dump_s > 0 else None,
               "pulses": {"info": res["info"], "groups": res["groups"]}}
        self.captures.append(rec)
        self.event(f"DS CAPTURE done: {len(y)} pts, dump {dump_s:.0f} s -> {os.path.basename(path)}")
        for ln in lines:
            self.log(ln)
        return dict(rec, lines=lines, path=path)

    def _timing_stats(self):
        pd = self.snapshot_pd()
        out = dict(self.pd_timing, set_hz=self.pd_rate)
        if pd is not None and len(pd) > 2:
            import numpy as np
            t = pd[:, 0]
            if self.t_off is not None:
                t = t[t <= self.t_off]
            d = np.diff(t)
            keep = np.ones(len(d), bool)
            for a, b in self._paused:
                keep &= ~((t[1:] >= a) & (t[:-1] <= b))
            d = d[keep]
            if len(d):
                out.update(n=int(len(t)), median_ms=float(1e3 * np.median(d)), mean_ms=float(1e3 * d.mean()),
                           p95_ms=float(1e3 * np.percentile(d, 95)), max_ms=float(1e3 * d.max()),
                           over_100ms=int((d > 0.1).sum()), achieved_hz=float(1 / d.mean()),
                           paused_for_capture=[list(x) for x in self._paused])
        return out

    def _save_pulses(self):
        from . import pulses
        try:
            res = self.pulse_result(max_age_s=0)
            if not res:
                return
            pd = self.snapshot_pd()
            m = pd[:, 0] >= 0
            if self.t_off is not None:
                m &= pd[:, 0] <= self.t_off
            err = pulses.save_all(self.run_dir, pd[m, 0], pd[m, 1], res, prefix="pulses",
                                  title=f"{os.path.basename(self.run_dir)}  (1936-R {self.pd_rate:g} Hz poll)")
            self.meta["pulse_summary"] = {"info": res["info"], "groups": res["groups"]}
            for ln in pulses.summary_lines(res, "pd.csv"):
                self.log(ln)
            if err:
                self.log(err)
        except Exception as e:  # noqa: BLE001
            self.log(f"펄스 분석 저장 실패: {type(e).__name__}: {e}")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def _version():
    try:
        from . import __version__
        return __version__
    except ImportError:
        return None


def fmt_t(s):
    if s is None or not math.isfinite(s):
        return "-"
    s = max(0.0, s)
    m, sec = divmod(int(round(s)), 60)
    return f"{m}m{sec:02d}s" if m else f"{s:.1f}s"


def status_line(st):
    """콘솔 한 줄 요약."""
    if st.get("n", 0) < 10:
        return f"[{fmt_t(st.get('t_since_on'))}] {st['state'].upper()}  (데이터 수집 중)"
    w, f = st["window"], st.get("fit")
    s = (f"[{fmt_t(st['t'])}] {st['state'].upper():9s} I={st['y_now'] * 1e6:9.3f} µA  "
         f"drift {w['slope_pct_min']:+.3f}%/min  noise {w['noise_pct']:.3f}%  V={st['V']:.3f}")
    if f:
        eta = "reached" if f["eta_s"] <= 0 else f"in {fmt_t(f['eta_s'])}"
        s += (f" | [{f.get('source', 'free')}] final {f['y_inf'] * 1e6:.3f} µA, now {f['dev_pct']:+.2f}%, "
              f"±{st['tol_pct']:g}% @ {fmt_t(f['t_settle'])} ({eta}{'' if f['reliable'] else ', 외삽'})")
    if st.get("vf_low"):
        s += "  [V LOW]"
    return s
