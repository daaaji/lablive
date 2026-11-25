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
                 profile=None, note="", settle_margin=0.5, check_current=1e-3, reset_check=False):
        if i_max is None or not i_max > 0:
            raise ValueError("i_max(전류 상한) 필요 — LED 정격에서 정할 것")
        if not 0 < current <= i_max:
            raise ValueError(f"current는 0 < I ≤ i_max = {i_max} A")
        self.pm, self.k = pm, k
        self.current, self.compliance = current, compliance
        self.lambda_nm, self.pm_range, self.tol, self.window_s = lambda_nm, pm_range, tol, window_s
        self.vf_guard, self.pd_rate, self.k_rate = vf_guard, pd_rate, k_rate
        self.trip_every, self.stop_on_trip, self.rsense = trip_every, stop_on_trip, rsense
        # 1936-R 디지털 필터 점 수 (10 kHz 기준). None = 판독 간격에 맞춤 (20 Hz → 500점 = 50 ms)
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
            self.event(f"LED ON {self.current * 1e3:g} mA (cap {self.compliance:g} V)")
            self.event(f"1936-R read {self.pd_rate:g} Hz, digital filter {self.digital_filter} pts "
                       f"({self.digital_filter / 10:g} ms)")

            if self.pm_range is None:   # range 7에서 크기만 보고 고정 (auto-range는 전환 중 FLT_MAX)
                probe = [pm.read_A() for _ in range(6)]
                good = [abs(v) for v in probe if math.isfinite(v)]
                if not good:
                    raise RuntimeError(f"range 탐색 실패 — 유효 판독 없음 {probe}")
                r = pm.range_for(max(good))
                pm.set_range(r)
                self.meta["range_probe"] = probe
                self.event(f"1936-R range {r} (FS {FS[r]:.1e} A, BW {BW[r]})")
            self.meta["pm_range"] = pm.range
            if self.prior:
                self.event(f"profile {self.args['profile']}: model {self.prior['how']}, "
                           f"±{100 * self.tol:g}% expected at {fmt_t(self.tracker.prior_t_settle)}")
            elif self.profile:
                self.event(f"profile {self.args['profile']}: no model for this current -> free fit")
        except BaseException:
            self.stop()
            raise

        self.pd = Sampler("1936-R", self._read_pd, 1 / self.pd_rate, self.clock, ("I_pd_A", "range"),
                          os.path.join(self.run_dir, "pd.csv"), self._on_pd)
        self.kk = Sampler("2400", self._read_k, 1 / self.k_rate, self.clock, ("V", "I_A", "trip"),
                          os.path.join(self.run_dir, "k.csv"))
        self.tracker.start()
        self.pd.start()
        self.kk.start()
        self._watch = threading.Thread(target=self._watchdog, name="watchdog", daemon=True)
        self._watch.start()

    # ---- 샘플러 콜백 ----
    def _read_pd(self):
        v = self.pm.read_A()
        r = self.pm.range
        if self.clock() < self._range_grace:          # range를 막 바꾼 직후 (필터 과도 구간)
            return (math.nan, r)
        if not math.isfinite(v) or abs(v) > 0.95 * FS[r]:
            self._over += 1
            if self._over >= 2 and r < 7:
                self.pm.set_range(r + 1)
                self._over = 0
                self.event(f"over-range -> 1936-R range {r + 1}")
            return (math.nan, r)
        self._over = 0
        return (v, r)

    def _on_pd(self, row):
        if self.led_on:
            self.tracker.add(row[0] - self.seg_t0, row[1])

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
                  events=list(self.events[-6:]), run_dir=self.run_dir)
        st["t_off"] = self.t_off
        if self.fault:
            st["state"] = "fault"
        elif self.stopped:
            st["last_state"] = st["state"]
            st["state"] = "stopped"
        return st

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
        self.stopped = True
        self._halt.set()
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
        first_settled = (self.segments[0]["t_first_settled_prev"] if self.segments
                         else self.tracker.t_first_settled)
        self.meta.update(lablive_version=_version(), final_status=final, events=self.events, fault=self.fault,
                         t_first_settled=first_settled, t_off=self.t_off, segments=self.segments,
                         current_final_A=self.current, note=self.note,
                         prior_fit=self.tracker._pfit if not self.segments else None)
        if self.profile and self.t_on:
            try:
                self.profile.register_run(self.run_dir)
            except Exception as e:  # noqa: BLE001
                self.log(f"프로파일 회차 등록 실패: {e}")
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.run_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2, default=_json_default)
        self.log(f"LED OFF{' 확인' if self.meta.get('k_off_ok') else ' 확인 실패!'} / 1936-R 원복 → {self.run_dir}")

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
