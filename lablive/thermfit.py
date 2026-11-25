"""
thermfit.py — LED 열 drift 다중 지수 피팅 + settling 시간 외삽

모델 (열 RC 네트워크, Foster 형):
    y(t) = y_inf + Σ_k a_k · exp(-t / τ_k),   k = 1..n
- 광전류 I_pd: fast(10 kHz data store) + slow(polling)를 이어 붙여 0.3 ms ~ 끝까지
- Vf (또는 모듈 전압): slow 구간만
- 시간축을 로그 간격으로 binning → 각 decade가 같은 비중 (fast 1만 점이 slow를 압도하지 않게)
- 진폭 a_k, y_inf는 선형 최소제곱, τ_k만 Nelder–Mead로 최적화 (variable projection)
- n = 1..nmax 중 BIC 최소 모델 선택 (τ가 겹치거나 진폭이 무의미하면 제외)
- settling: 모델이 |y - y_inf| ≤ tol·|y_inf| 안에 머물기 시작하는 시각 (측정 끝 이후면 외삽 표시)

단독 실행 (이미 측정한 run 폴더 재분석, 장비 불필요):
    python thermfit.py runs\\20261105_172254_100mA
    python thermfit.py runs\\... --nmax 4
필요: numpy (matplotlib 있으면 fit.png)
"""
import argparse
import csv
import json
import math
import os

import numpy as np

DT = 1e-4
TOLS = (0.01, 0.005, 0.001)


# ---------- 전처리 ----------
def onset_index(fast, pre_n):
    if not fast or pre_n <= 0:
        return None
    f = np.asarray(fast, float)
    base = f[:pre_n].mean()
    tail = f[-max(10, len(f) // 10):].mean()
    amp = tail - base
    if amp == 0:
        return None
    s = (f - base) / amp
    idx = np.nonzero(s[pre_n // 2:] >= 0.1)[0]
    return int(idx[0] + pre_n // 2) if len(idx) else None


def logbin(t, y, per_decade=20, tmin=3e-4):
    t, y = np.asarray(t, float), np.asarray(y, float)
    m = np.isfinite(t) & np.isfinite(y) & (t >= tmin)
    t, y = t[m], y[m]
    if len(t) == 0:
        return t, y
    edges = 10 ** np.arange(math.log10(tmin), math.log10(t.max()) + 1.0 / per_decade, 1.0 / per_decade)
    bi = np.digitize(t, edges)
    tb, yb = [], []
    for b in np.unique(bi):
        sel = bi == b
        tb.append(t[sel].mean())
        yb.append(y[sel].mean())
    return np.array(tb), np.array(yb)


# ---------- 피팅 ----------
def _solve(t, y, taus):
    A = np.column_stack([np.ones_like(t)] + [np.exp(-t / tau) for tau in taus])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    r = A @ coef - y
    return coef, float(r @ r)


def _nelder_mead(f, x0, step=0.5, iters=3000, tol=1e-12):
    n = len(x0)
    pts = [np.array(x0, float)] + [np.array(x0, float) + step * np.eye(n)[i] for i in range(n)]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = np.argsort(vals)
        pts = [pts[i] for i in order]
        vals = [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) <= tol * (abs(vals[0]) + 1e-300):
            break
        c = np.mean(pts[:-1], axis=0)
        xr = c + (c - pts[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = c + 2 * (c - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = c + 0.5 * (pts[-1] - c)
            fc = f(xc)
            if fc < vals[-1]:
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0] + 0.5 * (p - pts[0]) for p in pts]
                vals = [f(p) for p in pts]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def fit_multiexp(t, y, n, tau_min, tau_max):
    lo, hi = math.log(tau_min), math.log(tau_max)

    def obj(x):
        if np.any(x < lo) or np.any(x > hi):
            return 1e300
        return _solve(t, y, np.exp(np.sort(x)))[1]

    best = None
    span = (math.log10(max(t.min() * 5, tau_min)), math.log10(t.max() / 2))
    for shift in (0.0, -0.5, 0.5):
        x0 = np.log(10 ** (np.linspace(span[0], span[1], n) + shift))
        x0 = np.clip(x0, lo + 1e-6, hi - 1e-6)
        x, v = _nelder_mead(obj, x0)
        if best is None or v < best[1]:
            best = (x, v)
    taus = np.exp(np.sort(best[0]))
    coef, ssr = _solve(t, y, taus)
    return {"taus": taus, "y_inf": float(coef[0]), "amps": coef[1:], "ssr": ssr}


def settle_times(fit, t_first, t_last, tols=TOLS):
    taus, amps, yinf = fit["taus"], fit["amps"], fit["y_inf"]
    tg = np.logspace(math.log10(max(t_first, 1e-4)),
                     math.log10(max(100 * taus.max(), 10 * t_last)), 6000)
    dev = np.abs(sum(a * np.exp(-tg / tau) for a, tau in zip(amps, taus))) / abs(yinf)
    out = {}
    for tol in tols:
        idx = np.nonzero(dev > tol)[0]
        ts = float(tg[idx[-1]]) if len(idx) else 0.0
        out[f"{tol * 100:g}%"] = {"t_s": ts, "extrapolated": bool(ts > float(t_last))}
    return out


def fit_signal(t, y, nmax=3, per_decade=20):
    tb, yb = logbin(t, y, per_decade)
    m = len(tb)
    if m < 6:
        return {"error": f"too few bins ({m})"}
    tau_min, tau_max = max(tb.min() / 3, 1e-5), tb.max() * 30
    cands = []
    for n in range(1, nmax + 1):
        if 2 * n + 1 >= m:
            break
        f = fit_multiexp(tb, yb, n, tau_min, tau_max)
        k = 2 * n + 1
        bic = m * math.log(max(f["ssr"], 1e-300) / m) + k * math.log(m)
        rel = np.abs(f["amps"]) / abs(f["y_inf"]) if f["y_inf"] else np.inf
        distinct = n == 1 or np.all(np.diff(np.log(f["taus"])) > math.log(2))
        degenerate = (not distinct) or np.any(rel < 1e-5)
        f.update(n=n, bic=bic, degenerate=bool(degenerate),
                 rms_pct=float(100 * math.sqrt(f["ssr"] / m) / abs(f["y_inf"])))
        cands.append(f)
    ok = [c for c in cands if not c["degenerate"]] or cands
    best = min(ok, key=lambda c: c["bic"])
    res = {
        "n_terms": best["n"],
        "y_inf": best["y_inf"],
        "terms": [{"tau_s": float(tau), "amp": float(a), "amp_pct_of_final": float(100 * a / best["y_inf"])}
                  for tau, a in zip(best["taus"], best["amps"])],
        "rms_resid_pct": best["rms_pct"],
        "t_range_s": [float(tb.min()), float(tb.max())],
        "settle": settle_times(best, tb.min(), tb.max()),
        "tau_max_poorly_constrained": bool(best["taus"].max() > tb.max() / 3),
        "candidates": [{"n": c["n"], "bic": round(c["bic"], 2), "rms_pct": c["rms_pct"],
                        "taus_s": [float(x) for x in c["taus"]], "degenerate": c["degenerate"]}
                       for c in cands],
    }
    res["_model"] = best
    res["_bins"] = (tb, yb)
    return res


# ---------- run 단위 ----------
def fit_run(fast, pre_n, slow, v_key="Vf", nmax=3):
    """fast: 리스트(A), slow: [{'t_s','I_pd',v_key,...}]"""
    out = {}
    t_all, y_all = [], []
    i0 = onset_index(fast, pre_n)
    t_fast_end = 0.0
    if fast and i0 is not None:
        tf = (np.arange(len(fast)) - i0) * DT
        t_all.append(tf[tf > 0])
        y_all.append(np.asarray(fast, float)[tf > 0])
        t_fast_end = float(tf[-1])
    if slow:
        ts = np.array([r["t_s"] for r in slow], float)
        yi = np.array([r["I_pd"] for r in slow], float)
        sel = ts > t_fast_end
        t_all.append(ts[sel])
        y_all.append(yi[sel])
    if t_all:
        out["I_pd"] = fit_signal(np.concatenate(t_all), np.concatenate(y_all), nmax)
    if slow and v_key in slow[0]:
        out["V"] = fit_signal(np.array([r["t_s"] for r in slow]), np.array([r[v_key] for r in slow]), nmax)
    out["onset_idx"] = i0
    return out


def public(fit):
    """JSON 저장용: 내부 객체 제거"""
    return {k: ({kk: vv for kk, vv in v.items() if not kk.startswith("_")} if isinstance(v, dict) else v)
            for k, v in fit.items()}


def plot_fit(path, fit, v_label="Vf (V)"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    keys = [k for k in ("I_pd", "V") if k in fit and "_model" in fit[k]]
    if not keys:
        return
    fig, axes = plt.subplots(len(keys), 2, figsize=(12, 4.2 * len(keys)), squeeze=False)
    for row, key in enumerate(keys):
        f = fit[key]
        tb, yb = f["_bins"]
        mdl = f["_model"]
        tg = np.logspace(math.log10(tb.min()), math.log10(tb.max()), 400)
        yg = mdl["y_inf"] + sum(a * np.exp(-tg / tau) for a, tau in zip(mdl["amps"], mdl["taus"]))
        ax, rx = axes[row]
        ax.semilogx(tb, yb, "o", ms=3, label="data (log-binned)")
        ax.semilogx(tg, yg, "-", lw=1.2, label=f"{f['n_terms']}-exp fit")
        ax.axhline(mdl["y_inf"], ls=":", lw=0.8, color="gray")
        for tau in mdl["taus"]:
            ax.axvline(tau, ls="--", lw=0.6, color="C3")
        ax.set_xlabel("t since LED ON (s)")
        ax.set_ylabel("I_pd (A)" if key == "I_pd" else v_label)
        taus = ", ".join(f"{x:.3g}" for x in mdl["taus"])
        ax.set_title(f"{key}: τ = [{taus}] s")
        ax.legend(fontsize=8)
        yfit_b = mdl["y_inf"] + sum(a * np.exp(-tb / tau) for a, tau in zip(mdl["amps"], mdl["taus"]))
        rx.semilogx(tb, 100 * (yb - yfit_b) / abs(mdl["y_inf"]), "o-", ms=3, lw=0.6)
        rx.axhline(0, color="gray", lw=0.6)
        rx.set_xlabel("t since LED ON (s)")
        rx.set_ylabel("residual (% of final)")
        rx.set_title(f"residual rms {f['rms_resid_pct']:.3g}%")
    fig.tight_layout()
    fig.savefig(path, dpi=150)


# ---------- 단독 실행 ----------
def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--nmax", type=int, default=3)
    a = ap.parse_args()
    meta = json.load(open(os.path.join(a.run_dir, "meta.json"), encoding="utf-8"))
    pre = meta.get("args", {}).get("pre", 0.1)
    fast = [float(r["I_pd_A"]) for r in _read_csv(os.path.join(a.run_dir, "fast.csv"))]
    rows = _read_csv(os.path.join(a.run_dir, "slow.csv"))
    vcol = "V_module_V" if rows and "V_module_V" in rows[0] else "Vf_V"
    slow = [{"t_s": float(r["t_s"]), "I_pd": float(r["I_pd_A"]), "Vf": float(r[vcol])} for r in rows]
    fit = fit_run(fast, round(pre / DT), slow, "Vf", a.nmax)
    with open(os.path.join(a.run_dir, "fit.json"), "w", encoding="utf-8") as f:
        json.dump(public(fit), f, ensure_ascii=False, indent=2)
    plot_fit(os.path.join(a.run_dir, "fit.png"), fit)
    print(json.dumps(summary_lines(fit), ensure_ascii=False, indent=2))
    print(f"→ {a.run_dir}\\fit.json, fit.png")


def summary_lines(fit):
    out = {}
    for key in ("I_pd", "V"):
        f = fit.get(key)
        if not f or "error" in f:
            out[key] = f
            continue
        out[key] = {
            "terms": [f"τ={x['tau_s']:.3g} s  a={x['amp_pct_of_final']:+.3g}%"
                      + ("  (데이터 시작 전 — 약하게 구속됨)" if x["tau_s"] < f["t_range_s"][0] else "")
                      for x in f["terms"]],
            "final": f["y_inf"],
            "rms_resid_pct": round(f["rms_resid_pct"], 4),
            "settle": {k: f"{v['t_s']:.3g} s" + (" (외삽)" if v["extrapolated"] else "")
                       for k, v in f["settle"].items()},
            "warning": "최장 τ가 측정 길이의 1/3보다 김 — 외삽 신뢰도 낮음" if f["tau_max_poorly_constrained"] else None,
        }
    return out


if __name__ == "__main__":
    main()
