"""펄스(셔터 열림/닫힘) 검출과 폭·도달률 계산 — 장비와 무관한 순수 계산 모듈 ((t, y)만 받음).

쓰임: 펄스 모드 측정(led-live --pulse, GUI 측정 모드 '펄스'), data store 고속 캡처, 사후 분석(led-pulses).

방법
- 수준: 닫힘(dark)·열림(open)은 신호 분포의 아래/위 무리 중앙값. 문턱 = 둘의 중간 (50%).
- 에지 시각 = 50% 통과 시각. 1936-R 디지털 필터(이동평균 F)를 거친 계단은 길이 R(≈ F + 셔터 전이)의
  직선 경사가 되고, 50% 폭은 필터와 무관하게 보존된다 (폭 > R일 때).
    두 이웃 샘플이 모두 경사 위(5–95%)  → 직선 보간 (정확)
    한 샘플만 경사 위                    → 그 샘플의 위치(%)와 기울기 1/R로 50% 시각 계산
    경사 위 샘플 없음 (간격이 R보다 김)   → 두 샘플 중간, 불확도 ±(간격 − R)/2
  R은 경사 위 샘플이 둘 이상인 에지들에서 직접 잰다 (없으면 F, F도 모르면 판독 간격).
  그래서 펄스 모드 자동 필터는 실제 판독 간격에 맞춘다 (F ≈ Δ → 에지마다 경사 위 샘플이 거의 항상 하나).
- 품질: 펄스 안팎(에지 앞뒤 한 칸 포함)의 샘플 간격이 기준 간격의 gap_factor배를 넘거나, 과범위(NaN)가
  끼어 있으면 'gap'으로 표시하고 그룹 통계에서 뺀다 (11/25 측정의 주기적 공백이 만든 가짜 폭을 거르기 위함).
- 도달률: 펄스 최대값 / 그 시각의 '다 열림' 수준. 다 열림 수준은 평탄 구간이 충분한 펄스들의 평탄값을
  시간에 대해 1차 맞춤 (LED가 데워지며 출력이 서서히 줄어드는 것을 따라감; 11/25: −0.19 %/min).
- 그룹: 연속한 펄스 중 폭이 비슷한 것끼리 (공칭 폭 목록이 있으면 가장 가까운 공칭 폭으로).
"""
import math

import numpy as np


def levels(y):
    """(dark, open) — 값 분포를 가운데에서 둘로 나눠 각 무리의 중앙값."""
    y = y[np.isfinite(y)]
    if len(y) < 10:
        return math.nan, math.nan
    lo, hi = np.percentile(y, 2), np.percentile(y, 99.5)
    mid = 0.5 * (lo + hi)
    a, b = y[y < mid], y[y >= mid]
    if len(a) == 0 or len(b) == 0:
        return math.nan, math.nan
    return float(np.median(a)), float(np.median(b))


RAMP_LO, RAMP_HI = 0.05, 0.95


def _edge(ta, fa, tb, fb, R, rising):
    """a = 문턱 이전 쪽 샘플, b = 이후 쪽 샘플 (시간순). f = 열림 비율 (0 닫힘 … 1 열림).
    반환 (50% 시각, 불확도 s, 방법)."""
    on_a = RAMP_LO < fa < RAMP_HI if math.isfinite(fa) else False
    on_b = RAMP_LO < fb < RAMP_HI if math.isfinite(fb) else False
    if on_a and on_b and fb != fa:
        return ta + (0.5 - fa) / (fb - fa) * (tb - ta), 0.0, "interp"
    sgn = 1.0 if rising else -1.0            # 경사 방향: 비율이 시간에 따라 +1/R 또는 −1/R
    if on_b:
        return tb - sgn * (fb - 0.5) * R, 0.0, "ramp"
    if on_a:
        return ta + sgn * (0.5 - fa) * R, 0.0, "ramp"
    return 0.5 * (ta + tb), max(0.0, (tb - ta) - R) / 2, "mid"


def ramp_length(t, f):
    """경사 위(5–95%)에 연속 두 샘플 이상 있는 곳에서 R = Δt / Δf 의 중앙값 (없으면 nan)."""
    on = np.isfinite(f) & (f > RAMP_LO) & (f < RAMP_HI)
    idx = np.flatnonzero(on[:-1] & on[1:])
    if len(idx) == 0:
        return math.nan, 0
    df = np.abs(f[idx + 1] - f[idx])
    m = df > 0.05
    if not m.any():
        return math.nan, 0
    r = (t[idx + 1] - t[idx])[m] / df[m]
    return float(np.median(r)), int(m.sum())


def detect(t, y, dt_ref=None, filter_s=0.0, gap_factor=2.5, min_contrast=5.0, min_depth=0.5):
    """펄스 목록. t (s), y (A) — NaN(과범위·range 전환)은 그대로 두면 품질 표시에 쓰임.
    dt_ref: 기준 판독 간격 (None이면 중앙값). filter_s: 1936-R 디지털 필터 길이 (평탄 구간을 고를 때 에지에서 뗄 거리).
    반환: (pulses, info). pulse = dict(i, t_rise, t_fall, width_s, n, peak, plateau, n_plateau, dark_before, ok, why)"""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    info = {"n_samples": int(len(t))}
    if len(t) < 10:
        return [], dict(info, error="too few samples")
    dt = np.diff(t)
    dref = float(dt_ref) if dt_ref else float(np.median(dt[dt > 0])) if (dt > 0).any() else math.nan
    dark, opn = levels(y)
    info.update(dt_ref_s=dref, dark=dark, open=opn)
    if not (math.isfinite(dark) and math.isfinite(opn)):
        return [], dict(info, error="no levels")
    noise = float(np.std(y[np.isfinite(y) & (y < 0.5 * (dark + opn))])) if np.isfinite(y).any() else 0.0
    if opn - dark < min_contrast * max(noise, 1e-15) or opn <= dark:
        return [], dict(info, error="no pulses (open/dark contrast too small)")
    if opn - dark < min_depth * abs(opn):        # 안정화 곡선의 작은 변화·노이즈를 펄스로 오인하지 않게
        return [], dict(info, error=f"no pulses (modulation {100 * (opn - dark) / abs(opn):.1f}% < {100 * min_depth:g}%)")
    thr = 0.5 * (dark + opn)
    info["threshold"] = thr
    fin = np.isfinite(y)
    hi = np.where(fin, y > thr, False)
    # NaN은 앞 값의 상태를 이어받게 (과범위는 보통 열림 중) — 에지는 유한값 사이에서만 보간
    state = hi.copy()
    for i in np.flatnonzero(~fin):
        state[i] = state[i - 1] if i > 0 else False
    d = np.diff(state.astype(np.int8))
    rises = np.flatnonzero(d == 1) + 1          # 첫 열림 샘플
    falls = np.flatnonzero(d == -1)             # 마지막 열림 샘플
    if len(falls) and len(rises) and falls[0] < rises[0]:
        falls = falls[1:]
    frac = (y - dark) / (opn - dark)
    R, nR = ramp_length(t, frac)
    info["ramp_s_measured"], info["ramp_n_edges"] = R, nR
    if not math.isfinite(R):
        R = filter_s or dref
    info["ramp_s"] = R
    pulses = []
    prev_fall = 0
    for s, e in zip(rises, falls):
        if s == 0 or e + 1 >= len(t):
            continue
        # 보간에 쓸 이웃 유한값
        a = s - 1
        while a > 0 and not fin[a]:
            a -= 1
        b = e + 1
        while b < len(t) - 1 and not fin[b]:
            b += 1
        s2 = s
        while s2 < e and not fin[s2]:
            s2 += 1
        e2 = e
        while e2 > s and not fin[e2]:
            e2 -= 1
        tr, ur, mr = _edge(t[a], frac[a], t[s2], frac[s2], R, True)
        tf, uf, mf = _edge(t[e2], frac[e2], t[b], frac[b], R, False)
        gaps = dt[a:b]
        why = []
        if len(gaps) and gaps.max() > gap_factor * dref:
            why.append(f"gap {1e3 * gaps.max():.0f} ms")
        if not fin[s:e + 1].all():
            why.append("over-range")
        if t[s2] - t[a] > gap_factor * dref or t[b] - t[e2] > gap_factor * dref:
            why.append("edge")
        yy = y[s:e + 1]
        tt = t[s:e + 1]
        pk = float(np.nanmax(yy)) if np.isfinite(yy).any() else math.nan
        guard = filter_s + dref
        core = (tt >= tr + guard) & (tt <= tf - guard) & np.isfinite(yy)
        plateau = float(np.median(yy[core])) if core.sum() >= 1 else math.nan
        dk = y[prev_fall:a + 1]
        dk = dk[np.isfinite(dk) & (dk < thr)]
        dk = dk[-max(3, min(len(dk), 15)):] if len(dk) else dk
        pulses.append({"i": len(pulses), "t_rise": float(tr), "t_fall": float(tf), "width_s": float(tf - tr),
                       "width_unc_s": float(ur + uf), "edges": f"{mr}/{mf}",
                       "n": int(e - s + 1), "peak": pk, "plateau": plateau, "n_plateau": int(core.sum()),
                       "dark_before": float(np.median(dk)) if len(dk) else math.nan,
                       "ok": not why, "why": ", ".join(why)})
        prev_fall = b
    info["n_pulses"] = len(pulses)
    _reach(pulses, opn, info)
    return pulses, info


def _reach(pulses, opn, info):
    """다 열림 수준 ref(t)를 평탄값으로 1차 맞춤하고, 펄스마다 도달률(최대/ref) 계산."""
    good = [p for p in pulses if p["ok"] and p["n_plateau"] >= 3 and math.isfinite(p["plateau"])]
    if len(good) >= 3:
        tt = np.array([0.5 * (p["t_rise"] + p["t_fall"]) for p in good])
        pp = np.array([p["plateau"] for p in good])
        if tt.max() - tt.min() > 1.0:
            a, b = np.polyfit(tt, pp, 1)
            # 1차 맞춤에서 크게 벗어난 것(덜 열린 펄스 등) 빼고 다시
            r = pp - (a * tt + b)
            keep = np.abs(r) <= 3 * max(np.median(np.abs(r)) * 1.4826, 1e-15)
            if keep.sum() >= 3:
                a, b = np.polyfit(tt[keep], pp[keep], 1)
        else:
            a, b = 0.0, float(np.median(pp))
    elif good:
        a, b = 0.0, float(np.median([p["plateau"] for p in good]))
    else:
        a, b = 0.0, float(opn)
    info["open_ref"] = {"slope_per_s": float(a), "at_t0": float(b),
                        "drift_pct_per_min": float(100 * 60 * a / b) if b else math.nan,
                        "from_pulses": len(good)}
    for p in pulses:
        ref = a * 0.5 * (p["t_rise"] + p["t_fall"]) + b
        p["open_ref"] = float(ref)
        p["reach_pct"] = float(100 * p["peak"] / ref) if ref else math.nan
        p["plateau_pct"] = float(100 * p["plateau"] / ref) if (ref and math.isfinite(p["plateau"])) else math.nan


def group(pulses, nominal_ms=None, rel_tol=0.12, abs_tol_s=0.015):
    """그룹 목록. 정상 펄스를 시간순으로 보며 폭이 그룹 중앙값에서 max(abs_tol, rel_tol×폭) 넘게 벗어나면 새 그룹.
    제외(gap 등) 펄스는 시간상 앞 정상 펄스의 그룹에 붙인다 (개수 n_all에만 셈, 통계에서는 뺌).
    nominal_ms가 있으면 각 그룹에 가장 가까운 공칭 폭을 붙이고, 같은 공칭 폭의 이웃 그룹은 합친다."""
    blocks = []
    for p in pulses:
        if not p["ok"]:
            continue
        w = p["width_s"]
        if blocks:
            ref = float(np.median([q["width_s"] for q in blocks[-1]]))
            if abs(w - ref) <= max(abs_tol_s, rel_tol * ref):
                blocks[-1].append(p)
                continue
        blocks.append([p])
    noms = sorted(float(x) for x in nominal_ms) if nominal_ms else None
    labels = []
    for b in blocks:
        wm = 1e3 * float(np.median([q["width_s"] for q in b]))
        labels.append(min(noms, key=lambda n: abs(math.log(wm / n))) if noms else None)
    groups = []
    for b, lab in zip(blocks, labels):
        if noms and groups and groups[-1]["nominal_ms"] == lab:
            groups[-1]["members"] += b
        else:
            groups.append({"nominal_ms": lab, "members": list(b)})
    # 제외 펄스: 앞 정상 펄스의 그룹 (맨 앞이면 첫 그룹)
    owner = {}
    for k, g in enumerate(groups):
        for q in g["members"]:
            owner[q["i"]] = k
    last = 0
    for p in pulses:
        if p["i"] in owner:
            last = owner[p["i"]]
        elif groups:
            groups[last]["members"].append(p)
    for k, g in enumerate(groups):
        g["members"].sort(key=lambda q: q["t_rise"])
        for q in g["members"]:
            q["group"] = k
    out = []
    for k, g in enumerate(groups):
        m = g["members"]
        ok = [p for p in m if p["ok"]]
        w = np.array([1e3 * p["width_s"] for p in ok])
        r = np.array([p["reach_pct"] for p in ok])
        starts = np.array([p["t_rise"] for p in m])
        per = float(np.median(np.diff(starts))) if len(starts) >= 2 else math.nan
        wm = float(w.mean()) if len(w) else math.nan
        out.append({
            "group": k, "nominal_ms": g["nominal_ms"] if g["nominal_ms"] is not None
            else (float(np.median(w)) if len(w) else math.nan),
            "n_all": len(m), "n_ok": len(ok),
            "width_ms_mean": wm, "width_ms_sd": float(w.std(ddof=1)) if len(w) > 1 else math.nan,
            "width_ms_min": float(w.min()) if len(w) else math.nan, "width_ms_max": float(w.max()) if len(w) else math.nan,
            "reach_pct_mean": float(r.mean()) if len(r) else math.nan,
            "reach_pct_min": float(r.min()) if len(r) else math.nan,
            "period_s": per, "dark_ms": 1e3 * per - wm if math.isfinite(per) and math.isfinite(wm) else math.nan,
            "t_first": float(starts.min()) if len(starts) else math.nan,
        })
    return out


def analyze(t, y, nominal_ms=None, dt_ref=None, filter_s=0.0, gap_factor=2.5):
    pulses, info = detect(t, y, dt_ref=dt_ref, filter_s=filter_s, gap_factor=gap_factor)
    groups = group(pulses, nominal_ms) if pulses else []
    ok = [p for p in pulses if p["ok"]]
    if nominal_ms and ok:
        err = np.array([1e3 * p["width_s"] - groups[p["group"]]["nominal_ms"] for p in ok])
        info["width_error_ms"] = {"mean": float(err.mean()), "sd": float(err.std(ddof=1)) if len(err) > 1 else math.nan,
                                  "sem": float(err.std(ddof=1) / math.sqrt(len(err))) if len(err) > 1 else math.nan,
                                  "n": int(len(err))}
    return {"info": info, "pulses": pulses, "groups": groups}


# ---------------- 출력 ----------------
PULSE_COLS = ("i", "group", "t_rise", "t_fall", "width_s", "width_unc_s", "edges", "n", "peak", "plateau", "n_plateau", "open_ref",
              "reach_pct", "plateau_pct", "dark_before", "ok", "why")
GROUP_COLS = ("group", "nominal_ms", "n_all", "n_ok", "width_ms_mean", "width_ms_sd", "width_ms_min", "width_ms_max",
              "reach_pct_mean", "reach_pct_min", "period_s", "dark_ms", "t_first")


def write_csv(path, rows, cols):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([_c(r.get(c)) for c in cols])


def _c(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        return "" if not math.isfinite(v) else f"{v:.9g}"
    return "" if v is None else v


def summary_lines(res, src=""):
    info, groups = res["info"], res["groups"]
    L = []
    if info.get("error"):
        return [f"펄스 분석{(' (' + src + ')') if src else ''}: {info['error']}"]
    n_ok = sum(p["ok"] for p in res["pulses"]) if res["pulses"] else sum(g["n_ok"] for g in groups)
    L.append(f"펄스 분석{(' (' + src + ')') if src else ''}: {info['n_pulses']}개 검출, 정상 {n_ok}개 "
             f"(간격 {1e3 * info['dt_ref_s']:.3g} ms 기준)")
    L.append(f"  닫힘 {info['dark'] * 1e6:.4g} µA, 열림 {info['open'] * 1e6:.5g} µA "
             f"(대비 {info['open'] / info['dark']:.0f}:1)" if info["dark"] > 0 else
             f"  닫힘 {info['dark'] * 1e6:.4g} µA, 열림 {info['open'] * 1e6:.5g} µA")
    o = info.get("open_ref") or {}
    if o.get("from_pulses", 0) >= 3:
        L.append(f"  다 열림 수준 변화 {o['drift_pct_per_min']:+.3f} %/min (평탄 펄스 {o['from_pulses']}개로 맞춤)")
    L.append("  그룹   공칭ms   정상/검출   폭 평균±sd (ms)   차    도달률 평균(최소) %   주기 s   어둠 ms")
    for g in groups:
        nom = g["nominal_ms"]
        dif = g["width_ms_mean"] - nom if (math.isfinite(g["width_ms_mean"]) and math.isfinite(nom)) else math.nan
        L.append(f"  {g['group']:3d} {nom:9.4g}   {g['n_ok']:3d}/{g['n_all']:<3d}   "
                 f"{g['width_ms_mean']:8.1f} ± {g['width_ms_sd']:5.1f}   {dif:+6.1f}   "
                 f"{g['reach_pct_mean']:8.2f} ({g['reach_pct_min']:.2f})   {g['period_s']:6.3f}  {g['dark_ms']:7.1f}")
    we = info.get("width_error_ms")
    if we:
        L.append(f"  공칭 대비 폭 오차 전체: {we['mean']:+.1f} ± {we['sem']:.1f} ms (평균 ± 표준오차, n={we['n']}), "
                 f"펄스별 sd {we['sd']:.1f} ms")
    bad = [p for p in res["pulses"] if not p["ok"]]
    if bad:
        L.append(f"  제외 {len(bad)}개: " + "; ".join(f"#{p['i']} @{p['t_rise']:.1f}s {p['why']}" for p in bad[:8])
                 + (" ..." if len(bad) > 8 else ""))
    return L


def plot_overlay(path, t, y, res, title=""):
    """그룹별로 50% 상승 시각에 맞춰 겹친 그림."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    from .plotstyle import setup
    setup()
    groups = res["groups"]
    if not groups:
        return None
    n = len(groups)
    cols = min(4, n)
    rows = int(math.ceil(n / cols))
    fig = Figure(figsize=(3.8 * cols, 3.0 * rows + 0.6))
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    for k, g in enumerate(groups):
        ax = fig.add_subplot(rows, cols, k + 1)
        nom = g["nominal_ms"] if math.isfinite(g["nominal_ms"]) else 100.0
        for p in res["pulses"]:
            if p.get("group") != k:
                continue
            m = (t > p["t_rise"] - 0.15) & (t < p["t_fall"] + 0.2)
            ax.plot(1e3 * (t[m] - p["t_rise"]), 1e6 * y[m], ".-" if p["ok"] else "x:", lw=0.8, ms=2.5,
                    color=None if p["ok"] else "0.6")
        ax.axvspan(0, nom, color="C0", alpha=0.07, lw=0)
        ax.set_title(f"{nom:.4g} ms  ({g['n_ok']}/{g['n_all']} ok, "
                     f"{g['width_ms_mean']:.1f}±{g['width_ms_sd']:.1f})", fontsize=9)
        ax.set_xlabel("ms from 50% rise", fontsize=8)
        if k % cols == 0:
            ax.set_ylabel("I_pd (uA)", fontsize=8)
        ax.tick_params(labelsize=7)
    fig.suptitle(title + "   (grey x = excluded: gap/over-range)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    return path


def save_all(run_dir, t, y, res, prefix="pulses", title=""):
    import json
    import os
    write_csv(os.path.join(run_dir, f"{prefix}.csv"), res["pulses"], PULSE_COLS)
    write_csv(os.path.join(run_dir, f"{prefix}_groups.csv"), res["groups"], GROUP_COLS)
    with open(os.path.join(run_dir, f"{prefix}_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines(res, prefix)) + "\n\n")
        f.write(json.dumps(res["info"], ensure_ascii=False, indent=2, default=float))
    try:
        plot_overlay(os.path.join(run_dir, f"{prefix}.png"), t, y, res, title=title or prefix)
    except Exception as e:  # noqa: BLE001
        return f"그림 실패: {e}"
    return None
