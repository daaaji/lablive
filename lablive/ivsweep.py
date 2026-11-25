"""I-V(-L) 스윕 — 처음 보는 LED의 특성 파악용.

current 모드 (기본, 안전): 2400 정전류 계단 + 전압 상한. 전류가 i_max를 넘을 수 없음.
voltage 모드          : 2400 정전압 계단 + 전류 제한(ilim ≤ i_max). 모듈 내장 드라이버/UVLO 판별용
                        (bare LED는 문턱 이후 전류가 지수적으로 늘어 곧 전류 제한, 드라이버 내장이면 평탄)

각 점: 소스 설정 → dwell 대기 → 2400 (V, I) → compliance 확인 → 1936-R n회 판독 (range 소프트웨어 자동)
1936-R은 필터 없이(순간값) 읽는다 → 점마다 표준편차가 kHz 발진을 잡아냄 (noise_pct)
  (50 ms 필터를 쓰면 발진이 평균되어 안 보임 — uvc275 모듈의 6.26 V 문턱 발진이 그 예)
"""
import csv
import json
import math
import os
import time
from datetime import datetime

from . import config
from .led_session import compare_check, polarity_check

NOISY_PCT = 2.0     # 점 내 광전류 표준편차가 이 % 넘으면 '불안정(발진 의심)'


def log_levels(start, stop, n):
    if n < 2:
        return [stop]
    r = (stop / start) ** (1 / (n - 1))
    return [start * r ** i for i in range(n)]


def lin_levels(start, stop, step):
    n = int(math.floor((stop - start) / step + 1e-9))
    return [round(start + i * step, 6) for i in range(n + 1)]


def _row(direction, setv, V, I, trip, pd, dark, dark_sd=0.0):
    m, sd, r, ng = pd
    sig = m - dark if math.isfinite(m) else math.nan
    floor = max(1e-11, 20 * abs(dark_sd or 0.0))      # dark 잡음 수준의 신호는 '불안정' 판정에서 제외
    noise = 100 * sd / abs(sig) if math.isfinite(sig) and abs(sig) > floor and ng > 1 else math.nan
    return {"dir": direction, "set": setv, "V": V, "I": I, "trip": int(bool(trip)), "I_pd_A": sig,
            "I_pd_raw_A": m, "pd_sd_A": sd, "noise_pct": noise, "pd_range": r, "n_pd": ng}


def sweep(pm, k, mode, levels, *, compliance=12.0, ilim=None, dwell=0.3, n_avg=8, back=False,
          dark=0.0, dark_sd=0.0, log=print, stop_trips=1, guard=None):
    """반환: 행 dict 리스트. 출력은 켠 채로 끝난다 (끄는 것은 호출자).
    guard: before(x, rows) / after(row, rows) → 중단 사유 문자열 또는 None (미상 LED용 UnknownGuard).
           guard가 있으면 설정 직후 V도 읽어 dwell 동안의 전압 변화(dV_dwell, 급가열 지표)를 기록."""
    rows = []
    seqs = [("up", list(levels))] + ([("down", list(levels)[::-1])] if back else [])
    k.output(True)
    for direction, seq in seqs:
        trips = 0
        for x in seq:
            if guard and direction == "up":
                why = guard.before(x, rows)
                if why:
                    guard.reason = why
                    log(f"  중단 (점 진입 전): {why}")
                    break
            (k.set_current if mode == "current" else k.set_voltage)(x)
            t0 = time.perf_counter()
            v_early = k.read()[0] if guard else math.nan
            time.sleep(max(0.0, dwell - (time.perf_counter() - t0)))
            V, I = k.read()
            trip = k.tripped()
            pd = pm.read_stats(n_avg, period=0.05)
            row = _row(direction, x, V, I, trip, pd, dark, dark_sd)
            row["dV_dwell"] = V - v_early
            rows.append(row)
            u = "mA" if mode == "current" else "V"
            sx = f"{x * 1e3:9.4f} mA" if mode == "current" else f"{x:7.3f} V"
            log(f"  {direction:4s} {sx}  V={V:7.3f}  I={I * 1e3:9.4f} mA  I_pd={row['I_pd_A']:.4e} A  "
                f"noise {row['noise_pct']:5.2f}%  r{row['pd_range']}{'  TRIP' if trip else ''}"
                f"{'  NOISY' if row['noise_pct'] > NOISY_PCT else ''}"
                + (f"  P {V * I * 1e3:.1f} mW  dV {1e3 * row['dV_dwell']:+.0f} mV" if guard else ""))
            why = guard.after(row, rows) if guard and direction == "up" and not trip else None
            if why:
                guard.reason = why
                row["stop"] = why
                log(f"  중단: {why}")
                if back:
                    seqs[1] = ("down", [y for y in seq if y < x][::-1])
                break
            trips = trips + 1 if trip else 0
            if trips >= stop_trips:
                if guard:
                    guard.reason = "compliance (voltage cap) reached"
                log(f"  compliance 도달 → {direction} 스윕 중단 @ {x:g} {'A' if u == 'mA' else 'V'}")
                if direction == "up" and back:
                    seqs[1] = ("down", [y for y in seq if y <= x][::-1])
                break
    return rows


class UnknownGuard:
    """사양을 모르는 LED를 아래에서부터 올리며, 과구동 징후가 보이면 멈춘다.
    - 전력: 다음 점 예상 V·I > p_max면 진입 전에 중단 (패키지/방열 미상 → 보수적으로)
    - 급가열: dwell 동안 V가 dv_max 넘게 떨어짐 (정전류에서 V↓ = 접합 온도↑, 약 -2 mV/K)
    - 효율 저하: 광전류/전류가 최고치의 droop 미만 (droop/발열 영역)
    - 열 roll-over: 전류를 올렸는데 광출력이 줄어듦"""

    def __init__(self, p_max=0.15, droop=0.75, dv_max=0.05, dark_sd=0.0):
        self.p_max, self.droop, self.dv_max = p_max, droop, dv_max
        self.lit_min = max(1e-10, 100 * abs(dark_sd or 0.0))
        self.reason = None

    def lit(self, r):
        """검출기 바닥 위의 신호 (안정 여부 무관)"""
        return math.isfinite(r["I_pd_A"]) and r["I_pd_A"] > self.lit_min and not r["trip"]

    def lit_quiet(self, r):
        """효율 비교에 쓸 만한 점: 신호 있고 점 내 요동 ≤ NOISY_PCT"""
        return self.lit(r) and math.isfinite(r["noise_pct"]) and r["noise_pct"] <= NOISY_PCT

    def before(self, x, rows):
        up = [r for r in rows if r["dir"] == "up" and math.isfinite(r["V"])]
        if up:
            p = up[-1]["V"] * x * 1.05
            if p > self.p_max:
                return f"next {x * 1e3:.3g} mA would be ~{p * 1e3:.0f} mW > p_max {self.p_max * 1e3:g} mW"
        return None

    def after(self, row, rows):
        if math.isfinite(row["V"]) and row["V"] * row["I"] > self.p_max:
            return f"power {row['V'] * row['I'] * 1e3:.0f} mW > p_max {self.p_max * 1e3:g} mW"
        if math.isfinite(row["dV_dwell"]) and row["dV_dwell"] < -self.dv_max:
            return f"V fell {-1e3 * row['dV_dwell']:.0f} mV within dwell (rapid heating)"
        lit = [r for r in rows if r["dir"] == "up" and self.lit_quiet(r)]
        if len(lit) >= 3 and lit[-1] is row:
            peak = max(r["I_pd_A"] / r["I"] for r in lit[:-1])
            e = row["I_pd_A"] / row["I"]
            if e < self.droop * peak:
                return f"efficiency {100 * e / peak:.0f}% of peak (droop / heating)"
            if row["I_pd_A"] < 0.98 * lit[-2]["I_pd_A"]:
                return "light decreased with more current (thermal roll-over)"
        return None


KT_Q = 0.025693   # 25 °C
SUGGEST_EFF = 0.85   # 제안 i_max: 효율 최고점 위에서 효율이 최고치의 85% 이상인 최대 시험 전류


def _interp_v(up, i_target):
    pts = sorted((r["I"], r["V"]) for r in up if r["I"] > 0)
    for (i0, v0), (i1, v1) in zip(pts, pts[1:]):
        if i0 <= i_target <= i1:
            w = (math.log(i_target) - math.log(i0)) / (math.log(i1) - math.log(i0)) if i1 > i0 else 0
            return v0 + w * (v1 - v0)
    return None


FAMILY = [(1.6, "IR (GaAs/AlGaAs) 쪽"), (2.4, "적색~황색 (AlGaInP) 쪽"), (3.7, "녹·청·백·UVA (InGaN) 쪽"),
          (5.0, "UVA~UVB 또는 InGaN 고전류"), (99, "UVC (AlGaN), 또는 직렬 칩/보호소자/드라이버 내장 모듈")]


def characterize(rows, guard, ceiling, p_max):
    """미상 LED 스윕 결과 → 특성/제안 dict"""
    import numpy as np
    up = [r for r in rows if r["dir"] == "up" and not r["trip"] and math.isfinite(r["V"]) and r["I"] > 0]
    c = {"stop_reason": guard.reason, "ceiling_A": ceiling, "p_max_W": p_max}
    if not up:
        return c
    lit_any = [r for r in up if guard.lit(r)]
    lit = [r for r in up if guard.lit_quiet(r)]
    c["light_detected"] = bool(lit_any)
    if lit_any:
        c["light_onset_mA"], c["light_onset_V"] = 1e3 * lit_any[0]["I"], lit_any[0]["V"]
    c["n_stable_lit"] = len(lit)
    if lit:
        effs = [r["I_pd_A"] / r["I"] for r in lit]
        k = int(np.argmax(effs))
        c["peak_eff_I_mA"] = 1e3 * lit[k]["I"]
        c["eff_top_rel"] = effs[-1] / effs[k]
        ok = [r for r, e in zip(lit, effs) if e >= SUGGEST_EFF * effs[k] and r["V"] * r["I"] <= p_max
              and r["I"] >= lit[k]["I"]] or [lit[k]]
        c["suggest_i_max_A"] = max(r["I"] for r in ok) if ok else None
    for i in (1e-3, 1e-2):
        c[f"V_at_{i * 1e3:g}mA"] = _interp_v(up, i)
    fit = [r for r in up if r["I"] >= 1e-5]
    if len(fit) >= 5:
        I = np.array([r["I"] for r in fit])
        V = np.array([r["V"] for r in fit])
        A = np.column_stack([np.ones_like(I), np.log(I), I])
        (a, b, rs), *_ = np.linalg.lstsq(A, V, rcond=None)
        c["n_ideality"], c["R_s_ohm"] = b / KT_Q, rs
        c["fit_rms_mV"] = 1e3 * float(np.sqrt(np.mean((A @ [a, b, rs] - V) ** 2)))
    top = up[-1]
    c["V_top"], c["I_top_mA"] = top["V"], 1e3 * top["I"]
    vref = c.get("V_at_10mA") or top["V"]
    c["family_hint"] = next(t for lim, t in FAMILY if vref < lim)
    c["suggest_compliance_V"] = float(math.ceil(max(r["V"] for r in up) + 1.5))
    c["ceiling_reached"] = top["I"] >= 0.999 * ceiling
    nz = [r for r in up if guard.lit(r) and math.isfinite(r["noise_pct"]) and r["noise_pct"] > NOISY_PCT]
    c["noisy_I_mA"] = [1e3 * r["I"] for r in nz]
    return c


def summarize(rows, mode):
    up = [r for r in rows if r["dir"] == "up" and not r["trip"] and math.isfinite(r["V"])]
    s = {"n_points": len(rows), "any_trip": any(r["trip"] for r in rows)}
    if not up:
        return s
    noisy = [r for r in up if math.isfinite(r["noise_pct"]) and r["noise_pct"] > NOISY_PCT]
    quiet = [r for r in up if math.isfinite(r["noise_pct"]) and r["noise_pct"] <= NOISY_PCT]
    s["noisy_points"] = [{"I_mA": 1e3 * r["I"], "V": r["V"], "noise_pct": r["noise_pct"]} for r in noisy]
    if noisy:
        s["noisy_I_max_mA"] = 1e3 * max(r["I"] for r in noisy)
        s["noisy_V_max"] = max(r["V"] for r in noisy)
        above = [r for r in quiet if r["I"] > max(x["I"] for x in noisy)]
        if above:
            s["stable_from_mA"] = 1e3 * min(r["I"] for r in above)
            s["stable_V_min"] = min(r["V"] for r in above)
            # 경고 전압 제안: 발진 구간 최고 V와 안정 구간 최저 V 사이 (정전류 구동 중 V가 이 아래로 가면 발진 영역 접근)
            s["suggest_vf_guard_V"] = round((s["noisy_V_max"] + s["stable_V_min"]) / 2, 3)
    top = up[-3:]
    if len(top) >= 2 and top[-1]["I"] > top[0]["I"]:
        s["R_dyn_top_ohm"] = (top[-1]["V"] - top[0]["V"]) / (top[-1]["I"] - top[0]["I"])
    s["V_max"] = max(r["V"] for r in up)
    s["I_max_mA"] = 1e3 * max(r["I"] for r in up)
    eff = [(r["I_pd_A"] / r["I"], r) for r in (quiet or up)
           if r["I"] > 0 and math.isfinite(r["I_pd_A"]) and r["I_pd_A"] > 0]
    if eff:
        e, r = max(eff, key=lambda x: x[0])
        s["peak_eff_A_per_A"], s["peak_eff_I_mA"] = e, 1e3 * r["I"]
        s["eff_at_top_rel"] = eff[-1][0] / e
    if mode == "voltage":
        hi = up[-max(3, len(up) // 10):]
        imax = max(r["I"] for r in hi)
        s["I_spread_top_pct"] = 100 * (imax - min(r["I"] for r in hi)) / imax if imax > 0 else None
    return s


def plot(rows, path, title=""):
    import matplotlib
    if matplotlib.get_backend().lower() not in ("agg",):
        matplotlib.use("Agg", force=True)
    from .plotstyle import setup as _font_setup
    _font_setup()
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 2, figsize=(12, 8))
    (a, b), (c, d) = ax
    for direction, col in (("up", "C0"), ("down", "C3")):
        rr = [r for r in rows if r["dir"] == direction and r["I"] > 0]
        if not rr:
            continue
        I = [1e3 * r["I"] for r in rr]
        a.plot(I, [r["V"] for r in rr], ".-", color=col, label=direction)
        b.plot(I, [r["I_pd_A"] for r in rr], ".-", color=col, label=direction)
        c.plot(I, [r["I_pd_A"] / r["I"] for r in rr], ".-", color=col, label=direction)
        d.plot(I, [max(r["noise_pct"], 1e-3) for r in rr], ".-", color=col, label=direction)
        nz = [r for r in rr if r["noise_pct"] > NOISY_PCT]
        if nz:
            a.plot([1e3 * r["I"] for r in nz], [r["V"] for r in nz], "o", mfc="none", mec="r", ms=9,
                   label=f"noise > {NOISY_PCT:g}%" if direction == "up" else None)
        tr = [r for r in rr if r["trip"]]
        if tr:
            a.plot([1e3 * r["I"] for r in tr], [r["V"] for r in tr], "x", color="k", ms=9, label="compliance")
    a.set(xscale="log", xlabel="I_LED (mA)", ylabel="V terminal (V)", title="(a) V-I")
    b.set(xscale="log", yscale="log", xlabel="I_LED (mA)", ylabel="I_pd - dark (A)", title="(b) L-I")
    c.set(xscale="log", xlabel="I_LED (mA)", ylabel="I_pd / I_LED (A/A)", title="(c) relative efficiency")
    d.set(xscale="log", yscale="log", xlabel="I_LED (mA)", ylabel="I_pd std / mean (%)",
          title="(d) optical noise per point (unfiltered)")
    d.axhline(NOISY_PCT, color="r", ls=":", lw=1)
    for x in (a, b, c, d):
        x.grid(alpha=0.3, which="both")
        x.legend(fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def run_iv(pm, k, *, mode="current", levels, compliance=12.0, i_max, ilim=None, lambda_nm=None,
           dwell=0.3, n_avg=8, back=False, profile=None, note="", run_dir=None, log=print, rsense=False,
           unknown=None):
    """스윕 전체 (점검 → 스윕 → 끄기 → 원복 → 저장). 반환: (run_dir, summary)
    unknown: {"p_max": W} 이면 미상 LED 탐색 — 첫 점 전류로 극성 점검, UnknownGuard로 과구동 징후에서 정지,
             summary["unknown"]에 특성/제안값"""
    if i_max is None or i_max <= 0:
        raise ValueError("i_max 필요")
    if mode == "current":
        if max(levels) > i_max * (1 + 1e-9):
            raise ValueError(f"스윕 최대 {max(levels) * 1e3:g} mA > i_max {i_max * 1e3:g} mA")
    else:
        ilim = ilim or i_max
        if ilim > i_max:
            raise ValueError(f"ilim {ilim * 1e3:g} mA > i_max {i_max * 1e3:g} mA")
        if max(levels) > compliance:
            raise ValueError(f"스윕 최대 전압 {max(levels):g} V > 상한 {compliance:g} V")
    tag = f"_{profile.name}" if profile else ""
    run_dir = run_dir or os.path.join(config.runs_dir(),
                                      datetime.now().strftime("%Y%m%d_%H%M%S") + f"_iv{tag}_{mode}")
    os.makedirs(run_dir, exist_ok=True)
    meta = {"kind": "iv", "mode": mode, "started": datetime.now().isoformat(timespec="seconds"),
            "profile": profile.name if profile else None, "note": note, "pm_port": pm.port, "k_port": k.port,
            "args": {"levels": levels, "compliance": compliance, "i_max": i_max, "ilim": ilim, "dwell": dwell,
                     "n_avg": n_avg, "back": back, "lambda_nm": lambda_nm, "rsense": rsense}}
    st0 = pm.save_state()
    meta["pm_state_before"] = st0
    rows = []
    guard = None
    try:
        failed, errs = k.setup_current_source(compliance, rsense)
        if failed or errs:
            raise RuntimeError(f"2400 설정 실패: {failed} {errs}")
        chk = polarity_check(k, compliance, levels[0] if unknown else min(1e-3, i_max), log=log)
        meta["polarity_check"] = chk
        if profile:
            ok, msg = compare_check(profile, chk)
            meta["polarity_vs_profile"] = msg
            log(f"  {msg}")
            if not ok:
                raise RuntimeError(f"{msg} — 다른 LED/배선? (같은 LED면 led-profile set {profile.name} --clear-check)")
            if not profile.get("check"):
                profile.update(check={"I_A": chk["I_A"], "V": chk["V"],
                                      "recorded": datetime.now().isoformat(timespec="seconds")})
        pm.configure_current(lambda_nm, digital_filter=0)
        pm.set_range(7)
        dark = pm.read_stats(10, period=0.05)
        meta["dark_A"] = dark[0]
        log(f"dark {dark[0]:.3e} A (range {dark[2]})")
        if mode == "voltage":
            failed, errs = k.setup_voltage_source(ilim, rsense)
            if failed or errs:
                raise RuntimeError(f"2400 V 소스 설정 실패: {failed} {errs}")
        pm.set_range(7)
        guard = UnknownGuard(unknown["p_max"], dark_sd=dark[1] if math.isfinite(dark[1]) else 0.0) \
            if unknown else None
        rows = sweep(pm, k, mode, levels, compliance=compliance, ilim=ilim, dwell=dwell, n_avg=n_avg,
                     back=back, dark=dark[0] if math.isfinite(dark[0]) else 0.0,
                     dark_sd=dark[1] if math.isfinite(dark[1]) else 0.0, log=log,
                     stop_trips=1 if mode == "current" else 3, guard=guard)
    except KeyboardInterrupt:
        log("중단 (Ctrl+C)")
        meta["interrupted"] = True
    finally:
        meta["k_off_ok"] = k.safe_off()
        if not meta["k_off_ok"]:
            log("!! 2400 OUTPUT OFF 확인 실패 — 전면 OUTPUT 버튼으로 끌 것 !!")
        try:
            pm.restore_state(st0)
            meta["pm_state_after"] = pm.save_state()
        except Exception as e:  # noqa: BLE001
            meta["pm_restore_error"] = str(e)
        if rows:
            with open(os.path.join(run_dir, "iv.csv"), "w", newline="", encoding="utf-8") as f:
                cols = list(dict.fromkeys(key for r in rows for key in r))
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
        meta["summary"] = summarize(rows, mode) if rows else {}
        if unknown and guard is not None:
            meta["summary"]["unknown"] = characterize(rows, guard, max(levels), unknown["p_max"])
        with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    if rows:
        try:
            plot(rows, os.path.join(run_dir, "iv.png"),
                 title=f"{profile.name if profile else ''} {mode} sweep  {meta['started']}")
        except Exception as e:  # noqa: BLE001
            log(f"iv.png 실패: {e}")
    if profile:
        profile.register_iv(run_dir)
    return run_dir, meta["summary"]
