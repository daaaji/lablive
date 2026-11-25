"""측정 회차 폴더 하나 → 시계열 로드 + 열 모델 요약. 두 형식을 같은 모양으로 맞춘다.

- live   (led_live.py)  : pd.csv (t_s, I_pd_A, range), k.csv (t_s, V, I_A, trip), meta.json
- settle (led_settle.py): fast.csv (10 kHz data store), slow.csv (t_s, I_pd_A, Vf_V|V_module_V, ...), meta.json
결과는 runfit.json에 캐시 (데이터 파일이 바뀌면 다시 계산).
"""
import csv
import json
import math
import os
from datetime import datetime

import numpy as np

from . import thermfit

CACHE = "runfit.json"
VERSION = 3


def _csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def _started(d, meta):
    if meta.get("started"):
        try:
            return datetime.fromisoformat(meta["started"])
        except ValueError:
            pass
    try:
        return datetime.strptime(os.path.basename(os.path.normpath(d))[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def _trim_tail(t, y):
    """LED를 끈 뒤 섞여 들어간 꼬리(신호 급감) 제거 — t_off 기록이 없는 옛 회차용."""
    if len(y) < 50:
        return t, y
    ref = np.median(y[-min(len(y), 400):-20]) if len(y) > 420 else np.median(y)
    n = len(y)
    while n > 10 and abs(y[n - 1] / ref - 1) > 0.02:   # 꺼지는 순간(50 ms 필터를 지나는 중간값 포함)
        n -= 1
    return t[:n], y[:n]


def load_run(d):
    meta_p = os.path.join(d, "meta.json")
    meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
    args = meta.get("args", {})
    out = {"dir": d, "meta": meta, "current": args.get("current"), "started": _started(d, meta),
           "note": args.get("note") or meta.get("note") or "", "profile": meta.get("profile")}
    if os.path.exists(os.path.join(d, "pd.csv")):
        out["kind"] = "live"
        rows = _csv(os.path.join(d, "pd.csv"))
        t = np.array([_f(r["t_s"]) for r in rows])
        y = np.array([_f(r["I_pd_A"]) for r in rows])
        m = np.isfinite(t) & np.isfinite(y) & (t >= 0)
        t_off = meta.get("t_off")
        if meta.get("segments"):          # 실행 중 전류를 바꾼 회차: 첫 전류 구간만
            seg = meta["segments"][0]["t_s"]
            t_off = seg if t_off is None else min(t_off, seg)
        if t_off is not None:
            m &= t <= t_off
        t, y = _trim_tail(t[m], y[m]) if t_off is None else (t[m], y[m])
        kr = _csv(os.path.join(d, "k.csv"))
        tv = np.array([_f(r["t_s"]) for r in kr])
        v = np.array([_f(r["V"]) for r in kr])
    else:
        out["kind"] = "settle"
        pre = args.get("pre", 0.1)
        fast = [_f(r["I_pd_A"]) for r in _csv(os.path.join(d, "fast.csv"))]
        srows = _csv(os.path.join(d, "slow.csv"))
        if not srows and not fast:
            raise FileNotFoundError(f"{d}: pd.csv / slow.csv 없음")
        vcol = "V_module_V" if srows and "V_module_V" in srows[0] else "Vf_V"
        ts = np.array([_f(r["t_s"]) for r in srows])
        ys = np.array([_f(r["I_pd_A"]) for r in srows])
        tv, v = ts, np.array([_f(r[vcol]) for r in srows])
        parts_t, parts_y = [], []
        i0 = thermfit.onset_index(fast, round(pre / thermfit.DT)) if fast else None
        t_fast_end = 0.0
        if i0 is not None:
            tf = (np.arange(len(fast)) - i0) * thermfit.DT
            parts_t.append(tf[tf > 0])
            parts_y.append(np.asarray(fast)[tf > 0])
            t_fast_end = float(tf[-1])
        sel = ts > t_fast_end
        parts_t.append(ts[sel])
        parts_y.append(ys[sel])
        t, y = np.concatenate(parts_t), np.concatenate(parts_y)
        m = np.isfinite(t) & np.isfinite(y)
        t, y = t[m], y[m]
        if args.get("vsource") is not None:
            out["current"] = None
    if meta.get("segments"):
        tcut = meta["segments"][0]["t_s"]
        tv, v = tv[tv <= tcut], v[tv <= tcut]
    mv = np.isfinite(tv) & np.isfinite(v) & (tv >= 0)
    out.update(t=t, I=y, tV=tv[mv], V=v[mv])
    return out


def _stamp(d):
    fs = [os.path.join(d, x) for x in ("pd.csv", "k.csv", "fast.csv", "slow.csv", "meta.json")]
    return [round(os.path.getmtime(f), 1) for f in fs if os.path.exists(f)]


def summarize(d, tols=(0.01, 0.005), use_cache=True):
    """회차 요약 dict (JSON 저장 가능)."""
    cp = os.path.join(d, CACHE)
    key = {"v": VERSION, "stamp": _stamp(d), "tols": list(tols)}
    if use_cache and os.path.exists(cp):
        try:
            c = json.load(open(cp, encoding="utf-8"))
            if c.get("key") == key:
                return c["summary"]
        except (ValueError, KeyError):
            pass
    r = load_run(d)
    t, y = r["t"], r["I"]
    s = {"dir": d, "kind": r["kind"], "current_A": r["current"], "note": r["note"],
         "started": r["started"].isoformat(timespec="seconds") if r["started"] else None,
         "duration_s": float(t.max()) if len(t) else None, "n": int(len(t))}
    if len(t) >= 30 and t.max() - t.min() > 5:
        res = thermfit.fit_signal(t, y, 3)
        if "error" not in res:
            m = res["_model"]
            st = thermfit.settle_times(m, float(t.min()), float(t.max()), tols)
            tb, yb = res["_bins"]
            s.update(I_final_A=float(m["y_inf"]), taus_s=[float(x) for x in m["taus"]],
                     amps_pct=[float(100 * a / m["y_inf"]) for a in m["amps"]],
                     n_terms=int(res["n_terms"]), rms_resid_pct=float(res["rms_resid_pct"]),
                     reliable=not bool(res["tau_max_poorly_constrained"]),
                     t_settle_s={k: v["t_s"] for k, v in st.items()},
                     t_settle_extrapolated={k: bool(v["extrapolated"]) for k, v in st.items()},
                     curve_t=[float(x) for x in tb],
                     curve_dev_pct=[float(100 * (b / m["y_inf"] - 1)) for b in yb])
        else:
            s["fit_error"] = res["error"]
    tv, v = r["tV"], r["V"]
    if len(v):
        s["V_start"] = float(np.median(v[tv <= tv.min() + 2.0]))
        s["V_final"] = float(np.mean(v[tv >= tv.max() - max(10.0, 0.05 * tv.max())]))
    if len(t):
        s["I_start_A"] = float(np.median(y[t <= t.min() + 1.0]))
    try:
        json.dump({"key": key, "summary": s}, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError:
        pass
    return s


def model_entry(summary, tol_keys=("1%", "0.5%")):
    """요약 → 프로파일 기준 모델 항목."""
    need = ("taus_s", "amps_pct", "I_final_A")
    if not all(k in summary for k in need):
        raise ValueError(f"{summary['dir']}: 피팅 결과 없음 ({summary.get('fit_error', '데이터 부족')})")
    if summary.get("current_A") is None:
        raise ValueError(f"{summary['dir']}: 정전류 회차가 아님")
    return {"current_A": summary["current_A"], "taus_s": summary["taus_s"], "amps_pct": summary["amps_pct"],
            "n_terms": summary["n_terms"], "I_final_A": summary["I_final_A"],
            "V_start": summary.get("V_start"), "V_final": summary.get("V_final"),
            "t_settle_s": {k: summary["t_settle_s"].get(k) for k in tol_keys if k in summary["t_settle_s"]},
            "reliable": summary["reliable"], "rms_resid_pct": summary["rms_resid_pct"],
            "duration_s": summary["duration_s"], "run_started": summary["started"]}
