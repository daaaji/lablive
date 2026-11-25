"""한 프로파일(LED)의 회차들을 나란히 비교.

질문별로 보는 곳:
- 매번 같은 모양으로 안정되나?        → (a) 정규화 곡선 (I/I∞ - 1, 로그 시간) 겹쳐 그리기
- 광원이 약해지거나 정렬이 바뀌었나?  → (b) 회차별 최종 광전류 I∞  (고정대/거리 같을 때만 절대 비교 의미)
- 안정까지 걸리는 시간이 변하나?      → (c) ±tol 도달 시각 (속 빈 점 = 측정 길이 밖 외삽)
- 시작 온도/접촉이 달랐나?            → (d) 시작·끝 단자 전압, 그리고 표의 '앞 회차와 간격'
"""
import csv
import math
import os
from datetime import datetime

import numpy as np

from .runfit import summarize


def collect(profile, current=None, tols=(0.01, 0.005)):
    rows = []
    for d in profile.run_dirs():
        if not os.path.isdir(d):
            rows.append({"dir": d, "error": "폴더 없음"})
            continue
        try:
            s = summarize(d, tols)
        except Exception as e:  # noqa: BLE001
            rows.append({"dir": d, "error": f"{type(e).__name__}: {e}"})
            continue
        if current is not None and (s.get("current_A") is None or abs(s["current_A"] - current) > 0.02 * current):
            continue
        rows.append(s)
    ok = [r for r in rows if "error" not in r]
    ok.sort(key=lambda r: r.get("started") or "")
    prev_end = None
    for r in ok:
        st = datetime.fromisoformat(r["started"]) if r.get("started") else None
        r["gap_prev_min"] = ((st - prev_end).total_seconds() / 60) if (st and prev_end) else None
        if st and r.get("duration_s"):
            prev_end = st.timestamp() + r["duration_s"]
            prev_end = datetime.fromtimestamp(prev_end)
    return ok, [r for r in rows if "error" in r]


def _label(r):
    st = r.get("started")
    s = (st[5:16].replace("T", " ") if st else os.path.basename(r["dir"]))
    return f"{s}  {r['current_A'] * 1e3:g} mA" if r.get("current_A") else s


def write_table(rows, path, tol_key="1%"):
    cols = ["started", "kind", "current_mA", "duration_s", "gap_prev_min", "I_final_uA", "I_start_uA",
            f"t_settle_{tol_key}_s", "extrapolated", "taus_s", "amps_pct", "rms_resid_pct",
            "V_start", "V_final", "note", "dir"]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get("started"), r.get("kind"),
                        r["current_A"] * 1e3 if r.get("current_A") else "",
                        _r(r.get("duration_s"), 1), _r(r.get("gap_prev_min"), 1),
                        _r(_u(r.get("I_final_A")), 4), _r(_u(r.get("I_start_A")), 4),
                        _r((r.get("t_settle_s") or {}).get(tol_key), 1),
                        (r.get("t_settle_extrapolated") or {}).get(tol_key, ""),
                        " / ".join(f"{x:.3g}" for x in r.get("taus_s", [])),
                        " / ".join(f"{x:+.3g}" for x in r.get("amps_pct", [])),
                        _r(r.get("rms_resid_pct"), 4), _r(r.get("V_start"), 4), _r(r.get("V_final"), 4),
                        r.get("note", ""), r["dir"]])


def _u(x):
    return None if x is None else x * 1e6


def _r(x, n):
    return "" if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(x, n)


def plot(rows, path, tol=0.01, title=""):
    import matplotlib
    matplotlib.use("Agg")
    from .plotstyle import setup as _font_setup
    _font_setup()
    import matplotlib.pyplot as plt

    tol_key = f"{tol * 100:g}%"
    fig, ax = plt.subplots(2, 2, figsize=(13, 8.5))
    (a1, a2), (a3, a4) = ax
    cmap = plt.get_cmap("viridis")
    n = max(len(rows), 1)
    for i, r in enumerate(rows):
        if r.get("curve_t"):
            a1.semilogx(r["curve_t"], r["curve_dev_pct"], lw=1.1, color=cmap(i / max(n - 1, 1)), label=_label(r))
    a1.axhspan(-100 * tol, 100 * tol, color="C2", alpha=0.12, lw=0)
    a1.axhline(0, color="gray", lw=0.6)
    a1.set_xlabel("t since LED ON (s)")
    a1.set_ylabel("I / I_final - 1 (%)")
    a1.set_title(f"(a) normalized warm-up curves (band ±{tol * 100:g}%)", fontsize=10)
    if rows:
        a1.legend(fontsize=7, loc="upper right")

    x = np.arange(len(rows))
    labels = [_label(r) for r in rows]
    cur = sorted({r.get("current_A") for r in rows if r.get("current_A")})
    for j, I in enumerate(cur):
        idx = [i for i, r in enumerate(rows) if r.get("current_A") == I and r.get("I_final_A")]
        a2.plot(idx, [rows[i]["I_final_A"] * 1e6 for i in idx], "o-", color=f"C{j}", label=f"{I * 1e3:g} mA")
        for i in idx:
            if rows[i].get("I_start_A"):
                a2.plot(i, rows[i]["I_start_A"] * 1e6, "^", color=f"C{j}", ms=5, alpha=0.6)
    a2.set_title("(b) final photocurrent I_final (o) and start (^)", fontsize=10)
    a2.set_ylabel("I_pd (µA)")
    if cur:
        a2.legend(fontsize=8)

    for i, r in enumerate(rows):
        ts = (r.get("t_settle_s") or {}).get(tol_key)
        if ts is None:
            continue
        ext = (r.get("t_settle_extrapolated") or {}).get(tol_key)
        a3.plot(i, ts, "o", ms=7, mfc="none" if ext else f"C{cur.index(r['current_A']) if r.get('current_A') in cur else 0}",
                mec=f"C{cur.index(r['current_A']) if r.get('current_A') in cur else 0}")
    a3.set_title(f"(c) time to ±{tol * 100:g}% of final (hollow = extrapolated)", fontsize=10)
    a3.set_ylabel("s")

    a4.plot(x, [r.get("V_start", np.nan) for r in rows], "s-", label="V start")
    a4.plot(x, [r.get("V_final", np.nan) for r in rows], "o-", label="V end")
    a4.set_title("(d) terminal voltage at start / end", fontsize=10)
    a4.set_ylabel("V")
    a4.legend(fontsize=8)

    for a in (a2, a3, a4):
        a.set_xticks(x)
        a.set_xticklabels(labels, rotation=35, ha="right", fontsize=7)
        a.grid(alpha=0.3)
    a1.grid(alpha=0.3, which="both")
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
