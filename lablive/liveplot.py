"""실시간 화면 — LedSession을 읽기만 한다 (장비를 직접 만지지 않음).

패널: [광전류 전체 이력 + 피팅 곡선 + ±tol 밴드] [기준값 대비 편차 %, 최근 구간] [단자 전압] [상태]
키:   q 종료(LED OFF)   s PNG 저장   l 시간축 log/linear 전환
그림 안 글자는 영어(한글 폰트 없는 PC에서도 깨지지 않게).
"""
import math
import os

import numpy as np

from .led_session import fmt_t
from .plotstyle import MONO
from .plotstyle import setup as _font_setup

STYLE = {
    "waiting": ("#8d8d8d", "WAITING"),
    "settling": ("#e67e22", "SETTLING"),
    "quasi": ("#b7950b", "SHORT-TERM STABLE"),
    "settled": ("#1e8449", "SETTLED"),
    "anomaly": ("#c0392b", "ANOMALY (noise burst)"),
    "fault": ("#c0392b", "FAULT - LED OFF"),
    "stopped": ("#555555", "STOPPED - LED OFF"),
}


# 그림 요소 색 (GUI '색' 창에서 바꾸고 ~/.lablive.json에 저장)
COLORS = {
    "I_pd": "#1f77b4",          # 광전류
    "fit_free": "#ff7f0e",      # 자유 피팅 곡선 / 예측 최종값 선
    "fit_profile": "#9467bd",   # 프로파일 모델 곡선
    "band": "#2ca02c",          # ±tol 밴드, 편차 패널의 ±tol 선
    "deviation": "#1f77b4",     # 편차 %
    "voltage": "#d62728",       # 단자 전압
    "guard": "#000000",         # 경고 전압 선
    "segment": "#7f7f7f",       # 전류 변경 시각 세로선
    "background": "#ffffff",    # 그래프 배경
}
COLOR_NAMES = {"I_pd": "광전류", "fit_free": "피팅 곡선 / 예측 최종값", "fit_profile": "프로파일 모델 곡선",
               "band": "±tol 밴드/선", "deviation": "편차 %", "voltage": "단자 전압", "guard": "경고 전압 선",
               "segment": "전류 변경 표시", "background": "그래프 배경"}


def _dec(a, n):
    if len(a) <= n:
        return a
    return a[:: int(math.ceil(len(a) / n))]


class LivePlot:
    def __init__(self, session, refresh_ms=300, max_points=3000, zoom_s=None, duration_s=None,
                 fig=None, colors=None, on_done=None):
        """fig: 이미 있는 matplotlib Figure (GUI에 끼워 넣을 때). None이면 pyplot 창을 새로 만든다.
        on_done: duration_s가 지나면 호출 (None이면 pyplot 창 닫기)."""
        _font_setup()
        self.s = session
        self.refresh_ms, self.N = refresh_ms, max_points
        self.zoom_s = zoom_s or max(3 * session.window_s, 60.0)
        self.duration_s, self.on_done = duration_s, on_done
        self.logx = False
        self.anim = None
        self.c = dict(COLORS, **(colors or {}))
        self._done = False
        self._nseg = 0
        if fig is None:
            import matplotlib.pyplot as plt
            self.plt = plt
            fig = plt.figure(figsize=(13.5, 8.2))
        else:
            self.plt = None
            fig.clear()
        self.fig = fig
        gs = fig.add_gridspec(3, 2, width_ratios=[3.2, 1.35], height_ratios=[1.35, 1.0, 0.9],
                              hspace=0.42, wspace=0.06, left=0.07, right=0.985, top=0.92, bottom=0.07)
        self.ax_i = fig.add_subplot(gs[0, 0])
        self.ax_d = fig.add_subplot(gs[1, 0])
        self.ax_v = fig.add_subplot(gs[2, 0], sharex=self.ax_i)
        self.ax_t = fig.add_subplot(gs[:, 1])
        self.ax_t.axis("off")
        tol = 100 * session.tol
        self.title = fig.suptitle("", fontsize=10)

        (self.l_i,) = self.ax_i.plot([], [], lw=0.8, color="C0", label="I_pd")
        (self.l_fit,) = self.ax_i.plot([], [], lw=1.1, ls="--", color="C1", label="multi-exp fit")
        self.l_inf = self.ax_i.axhline(0, ls=":", lw=0.9, color="C1", label="predicted final")
        self.l_inf.set_visible(False)
        self.band = None
        self.ax_i.set_ylabel("I_pd (µA)")
        self.ax_i.set_title("photocurrent since LED ON (band: ±tol around reference)", fontsize=9.5)
        self.ax_i.legend(fontsize=8, loc="upper right")

        (self.l_d,) = self.ax_d.plot([], [], lw=0.8, color="C0")
        self.ax_d.axhline(0, color="gray", lw=0.6)
        self.l_tp = self.ax_d.axhline(+tol, ls="--", color="C2", lw=0.9)
        self.l_tm = self.ax_d.axhline(-tol, ls="--", color="C2", lw=0.9)
        self.ax_d.set_ylabel("deviation (%)")
        self.ax_d.set_xlabel("t since LED ON (s)", fontsize=8)

        (self.l_v,) = self.ax_v.plot([], [], lw=0.9, color="C3")
        self.l_guard = self.ax_v.axhline(session.vf_guard or 0, ls="--", color="k", lw=0.7)
        self.l_guard.set_visible(bool(session.vf_guard))
        self.ax_v.set_ylabel("V terminal (V)")
        self.ax_v.set_xlabel("t since LED ON (s)")

        self.badge = self.ax_t.text(0.5, 1.0, "", transform=self.ax_t.transAxes, ha="center", va="top",
                                    fontsize=13, weight="bold", color="white",
                                    bbox=dict(boxstyle="round,pad=0.45", fc="#8d8d8d", ec="none"))
        self.txt = self.ax_t.text(0.02, 0.93, "", transform=self.ax_t.transAxes, ha="left", va="top",
                                  family=MONO, fontsize=9)
        fig.canvas.mpl_connect("key_press_event", self._on_key)
        self.apply_colors()

    def apply_colors(self, colors=None):
        if colors:
            self.c.update(colors)
        c = self.c
        self.l_i.set_color(c["I_pd"])
        self.l_inf.set_color(c["fit_free"])
        self.l_d.set_color(c["deviation"])
        self.l_tp.set_color(c["band"])
        self.l_tm.set_color(c["band"])
        self.l_v.set_color(c["voltage"])
        self.l_guard.set_color(c["guard"])
        for ax in (self.ax_i, self.ax_d, self.ax_v):
            ax.set_facecolor(c["background"])
        if self.band is not None:
            self.band.set_color(c["band"])
        for ln in getattr(self, "_seg_lines", []):
            ln.set_color(c["segment"])
        if self.ax_i.get_legend():
            self.ax_i.legend(fontsize=8, loc="upper right")

    # ---- 갱신 ----
    def update(self, _frame=None):
        s = self.s
        st = s.status()
        tol = 100 * s.tol
        pd = s.snapshot_pd()
        kd = s.snapshot_k()
        fit = st.get("fit")

        t = y = np.empty(0)
        if pd is not None and len(pd):
            m = np.isfinite(pd[:, 1]) & (pd[:, 0] >= 0)
            if st.get("t_off") is not None:      # LED가 꺼진 뒤 데이터는 그리지 않음 (CSV에는 남음)
                m &= pd[:, 0] <= st["t_off"]
            t, y = pd[m, 0], pd[m, 1]
        o = st.get("seg_t0") or 0.0
        segs = st.get("segments") or []
        self.title.set_text(f"LED {s.current * 1e3:g} mA" + (f" (start {segs[0]['from_A'] * 1e3:g})" if segs else "")
                            + f"  |  cap {s.compliance:g} V  |  tol ±{tol:g}%  |  window {s.window_s:g} s  |  "
                            f"{os.path.basename(s.run_dir)}")
        if len(segs) > self._nseg:                   # 전류 변경 시각 표시
            self._seg_lines = getattr(self, "_seg_lines", [])
            for sg in segs[self._nseg:]:
                for ax in (self.ax_i, self.ax_d, self.ax_v):
                    self._seg_lines.append(ax.axvline(sg["t_s"], color=self.c["segment"], ls="-.", lw=0.8))
            self._nseg = len(segs)
        self.l_tp.set_ydata([tol] * 2)
        self.l_tm.set_ydata([-tol] * 2)
        self.l_guard.set_visible(bool(s.vf_guard))
        if s.vf_guard:
            self.l_guard.set_ydata([s.vf_guard] * 2)
        if len(t) >= 2:
            ref = fit["y_inf"] if fit else (st["window"]["mean"] if "window" in st else y[-1])
            self.l_i.set_data(_dec(t, self.N), _dec(y, self.N) * 1e6)
            model = s.tracker.model()
            if model:
                taus, amps, yinf, src = model
                lab = "profile model (I_final scaled)" if src == "profile" else "multi-exp fit (free)"
                self.l_fit.set_color(self.c["fit_profile"] if src == "profile" else self.c["fit_free"])
                if self.ax_i.get_legend() and self.ax_i.get_legend().get_texts()[1].get_text() != lab:
                    self.l_fit.set_label(lab)
                    self.ax_i.legend(fontsize=8, loc="upper right")
                tr = t - o                                   # 구간(전류 변경 이후) 기준 시각
                t0 = max(tr[tr > 0].min() if (tr > 0).any() else 1e-2, 1e-2)
                tg = np.logspace(math.log10(t0), math.log10(max(tr[-1], t0 * 1.01)), 300)
                yg = yinf + sum(a * np.exp(-tg / tau) for a, tau in zip(amps, taus))
                self.l_fit.set_data(tg + o, yg * 1e6)
                self.l_inf.set_ydata([yinf * 1e6] * 2)
                self.l_inf.set_visible(True)
            if self.band is not None:
                self.band.remove()
            lo_b, hi_b = ref * (1 - tol / 100) * 1e6, ref * (1 + tol / 100) * 1e6
            self.band = self.ax_i.axhspan(min(lo_b, hi_b), max(lo_b, hi_b), color=self.c["band"], alpha=0.13, lw=0)
            ys = y[t >= o] if (t >= o).sum() >= 2 else y          # 전류를 바꿨으면 현재 구간 기준으로 y축
            ylo = min(ys.min() * 1e6, lo_b, hi_b)
            yhi = max(ys.max() * 1e6, lo_b, hi_b)
            pad = 0.05 * (yhi - ylo or abs(yhi) or 1)
            self.ax_i.set_ylim(ylo - pad, yhi + pad)
            if self.logx:
                self.ax_i.set_xlim(max(t[t > 0].min(), 1e-2), max(t[-1] * 1.05, 0.1))
            else:
                self.ax_i.set_xlim(0, max(10.0, t[-1] * 1.02))

            sel = t >= t[-1] - self.zoom_s
            dev = (y[sel] / ref - 1) * 100
            self.l_d.set_data(_dec(t[sel], self.N), _dec(dev, self.N))
            lo = max(0.0, t[-1] - self.zoom_s)
            self.ax_d.set_xlim(lo, t[-1] + 0.02 * max(t[-1] - lo, 1.0))
            lim = max(2 * tol, 1.2 * float(np.nanpercentile(np.abs(dev), 99.5))) if len(dev) else 2 * tol
            self.ax_d.set_ylim(-min(lim, 50), min(lim, 50))
            self.ax_d.set_title(f"deviation from {'predicted final (fit)' if fit else 'window mean'}, "
                                f"last {self.zoom_s:g} s  (dashed: ±{tol:g}%)", fontsize=9.5)

        if kd is not None and len(kd):
            m = np.isfinite(kd[:, 1]) & (kd[:, 0] >= 0)
            if st.get("t_off") is not None:
                m &= kd[:, 0] <= st["t_off"]
            tk, vk = kd[m, 0], kd[m, 1]
            if len(tk) >= 2:
                self.l_v.set_data(_dec(tk, self.N), _dec(vk, self.N))
                lo, hi = vk.min(), vk.max()
                g = s.vf_guard
                if g and lo - 0.5 < g < hi + 0.5:     # 가까우면 guard 선이 보이게
                    lo, hi = min(lo, g), max(hi, g)
                pad = max(0.05 * (hi - lo), 0.005)
                self.ax_v.set_ylim(lo - pad, hi + pad)

        color, label = STYLE.get(st["state"], ("#8d8d8d", st["state"].upper()))
        self.badge.set_text(label)
        self.badge.get_bbox_patch().set_facecolor(color)
        self.txt.set_text("\n".join(self._lines(st)))

        if (self.duration_s and not self._done and st.get("t_since_on")
                and st["t_since_on"] >= self.duration_s):
            self._done = True
            if self.on_done:
                self.on_done()
            elif self.plt:
                self.plt.close(self.fig)
        return ()

    def _lines(self, st):
        tol = st["tol_pct"]
        L = []
        if st.get("n", 0) >= 10:
            L.append(f"I_pd   {st['y_now'] * 1e6:11.4f} µA  (r{st['pd_range']})")
        L.append(f"LED    {st['I_led'] * 1e3:8.2f} mA   V {st['V']:.3f}")
        if st.get("vf_guard"):
            L.append(f"V guard {st['vf_guard']:.2f} V   {'LOW !' if st['vf_low'] else 'ok'}")
        L.append(f"t on   {fmt_t(st.get('t_off') if st.get('t_off') is not None else st.get('t_since_on'))}")
        if st.get("segments"):
            sg = st["segments"]
            L.append(f"I changed x{len(sg)}, last at {fmt_t(sg[-1]['t_s'])}")
            L.append("  (tracking restarted, no profile curve)")
        if st.get("last_state"):
            L.append(f"last state before stop: {st['last_state'].upper()}")
        if "window" in st:
            w = st["window"]
            L += ["", f"-- window {st['window_s']:g} s {'' if w['full'] else '(filling)'}",
                  f"drift {w['slope_pct_min']:+8.3f} %/min",
                  f"d/win {w['drift_pct']:8.3f} %  (<= {tol / 2:g})",
                  f"noise {w['noise_pct']:8.4f} %  (<= {tol / 4:g})"]
        f = st.get("fit")
        src = (f or {}).get("source")
        L += ["", f"-- final value: {'PROFILE model' if src == 'profile' else 'free fit'}"]
        pf = st.get("prior")
        if self.s.prior is not None:
            L.append(f"profile {self.s.args.get('profile')}: {self.s.prior.get('how')}")
            if pf:
                L.append(f"shape rms {pf['rms_pct']:.3f}% (limit {pf['rms_limit_pct']:.2f}) "
                         + ("MISMATCH" if pf["mismatch"] else "ok"))
                if pf.get("reason") and pf["reason"] != "shape":
                    L.append(f"not used: {pf['reason'][:36]}")
        elif self.s.profile is not None:
            L.append(f"profile {self.s.args.get('profile')}: {self.s.prior_how[:38]}")
        if f:
            eta = "reached" if f["eta_s"] <= 0 else f"in {fmt_t(f['eta_s'])}"
            L += [f"final  {f['y_inf'] * 1e6:11.4f} µA",
                  f"now    {f['dev_pct']:+8.3f} % vs final (SETTLED <= {tol * st['settle_margin']:g})",
                  f"±{tol:g}% at {fmt_t(f['t_settle'])} ({eta})",
                  "tau s  " + " / ".join(f"{x:.3g}" for x in f["taus"]),
                  "amp %  " + " / ".join(f"{x:+.2g}" for x in f["amps_pct"]),
                  ("reliable" if f["reliable"] else "EXTRAPOLATED - keep running")
                  + f"  (fit {f['age_s']:.0f} s ago)"]
        else:
            L.append(st.get("fit_error") or f"needs >= {self.s.tracker.fit_min_s:g} s of data")
        if math.isfinite(st.get("burst_noise_pct", math.nan)):
            L += ["", f"burst noise {st['burst_noise_pct']:.3f} % (base {st['base_noise_pct']:.3f})"]
        if st.get("t_first_settled") is not None:
            L.append(f"first SETTLED at {fmt_t(st['t_first_settled'])}")
        if st.get("fault"):
            L += ["", f"FAULT: {st['fault']}"]
        if st.get("events"):
            L += ["", "-- events"] + [f"{fmt_t(t):>7s} {m[:40]}" for t, m in st["events"][-5:]]
        L += ["", "[q] stop+LED OFF  [s] save  [l] log t" if self.plt else "[s] save  [l] log t  (click plot first)"]
        return L

    # ---- 키 / 실행 ----
    def _on_key(self, ev):
        if ev.key == "q" and self.plt:
            self.plt.close(self.fig)
        elif ev.key == "s":
            p = os.path.join(self.s.run_dir, f"snap_{int(self.s.clock())}s.png")
            self.save(p)
            self.s.log(f"저장: {p}")
        elif ev.key == "l":
            self.logx = not self.logx
            self.ax_i.set_xscale("log" if self.logx else "linear")

    def save(self, path):
        try:
            self.update()
        except Exception:  # noqa: BLE001
            pass
        self.fig.savefig(path, dpi=130)

    def run(self):
        """창이 닫힐 때까지 블록."""
        from matplotlib.animation import FuncAnimation
        self.anim = FuncAnimation(self.fig, self.update, interval=self.refresh_ms, cache_frame_data=False)
        self.plt.show()
