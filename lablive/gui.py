"""LED 측정 창 (tkinter + matplotlib). 실시간 그래프 위에 도구 막대:

  [▶ 시작/■ 정지] [LED 설정] [경로] | 전류 [ ] mA [적용] 단계 [ ] [▲][▼] | [색] [데이터] [PNG] [요약]

- LED 설정: 프로파일 선택/값 편집/프로파일 저장. 측정 중에는 실시간 적용 가능한 항목만 (전압 상한, 경고 전압,
           tol, margin, 메모, 전류 상한 낮추기)
- 경로: 데이터 폴더(home), 회차 저장 폴더 — 기본값 버튼, ~/.lablive.json에 저장
- 장비: 1936-R / 2400 포트 (기본 '자동' = 매번 *IDN? 탐색), [포트 찾기]로 각 COM 포트에 무엇이 붙어 있는지 확인
- 전류: 측정 중 바꾸면 그 시각부터 안정도 판정을 새로 시작 (회차 비교에는 첫 전류 구간만)
- 색: 그래프 요소 색 (~/.lablive.json에 저장)
- 데이터: 광전류/2400/이벤트 표 (스크롤, 실시간 추가), 다른 CSV 열기
장비 통신은 전부 작업 스레드에서 (창이 멈추지 않게), 그림 갱신은 Tk 주 스레드에서만.
"""
import csv
import math
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from types import SimpleNamespace

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from . import __version__, config
from .led_session import LedSession, fmt_t
from .liveplot import COLOR_NAMES, COLORS, STYLE, LivePlot
from .plotstyle import setup as font_setup
from .profiles import Profile

NO_PROFILE = "(프로파일 없음)"
DEFAULTS = {"profile": "", "current_mA": 100.0, "compliance": 12.0, "i_max_mA": None, "vf_guard": None,
            "lambda_nm": None, "tol_pct": 1.0, "window": 60.0, "margin": 0.5, "note": "", "range": "auto",
            "rsense": False, "pd_rate": 20.0, "k_rate": 2.0, "duration": 0.0}


def open_path(p):
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("열기 실패", str(e))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fmt(v):
    if isinstance(v, float):
        if not math.isfinite(v):
            return ""
        return f"{v:.6g}" if abs(v) >= 1e-3 or v == 0 else f"{v:.6e}"
    return str(v)


# =====================================================================
class LiveApp:
    def __init__(self, root, cfg=None, sim=False, pm_port=None, k_port=None, session=None, log=print):
        """session: 이미 시작된 LedSession (led-live에서 호출) — 이 경우 시작 버튼 없이 붙어서 표시만/조작만."""
        font_setup()
        self.root, self.sim, self.log = root, sim, log
        saved = {} if sim else (config.load_user().get("ports") or {})
        self.pm_port = pm_port or saved.get("pm")          # None = 자동
        self.k_port = k_port or saved.get("k")
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.colors = dict(COLORS, **(config.load_user().get("colors") or {}))
        self.s = session
        self.attached = session is not None
        self.pm = self.k = None
        self.plot = None
        self.busy = False
        self.q = queue.Queue()
        self._fault_shown = False
        self._summary = []
        self.data_win = None
        self._reset_check = False
        self._close_after_stop = False

        root.title(f"lablive {__version__}" + ("  [SIMULATOR]" if sim else ""))
        root.geometry("1420x930")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self._tk_error
        self._build()
        if session is not None:
            self.cfg.update(profile=session.profile.name if session.profile else "",
                            current_mA=session.current * 1e3, compliance=session.compliance,
                            i_max_mA=session.i_max * 1e3, vf_guard=session.vf_guard, tol_pct=100 * session.tol,
                            window=session.window_s, margin=session.settle_margin, note=session.note)
            self.cur_var.set(f"{session.current * 1e3:g}")
            self._attach_plot()
        else:
            self._placeholder()
        self._refresh_buttons()
        root.after(100, self._poll)

    # ---------------- 화면 구성 ----------------
    def _build(self):
        r = self.root
        bar = ttk.Frame(r, padding=(6, 4))
        bar.pack(side="top", fill="x")
        self.b_run = ttk.Button(bar, text="▶ 시작", width=9, command=self.toggle_run)
        self.b_run.pack(side="left")
        ttk.Button(bar, text="LED 설정", command=self.open_settings).pack(side="left", padx=(6, 0))
        self.b_paths = ttk.Button(bar, text="경로", command=self.open_paths)
        self.b_paths.pack(side="left", padx=(4, 0))
        self.b_dev = ttk.Button(bar, text="장비", command=self.open_devices)
        self.b_dev.pack(side="left", padx=(4, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(bar, text="전류").pack(side="left")
        self.cur_var = tk.StringVar(value=f"{self.cfg['current_mA']:g}")
        e = ttk.Entry(bar, textvariable=self.cur_var, width=9, justify="right")
        e.pack(side="left", padx=2)
        e.bind("<Return>", lambda _e: self.apply_current())
        ttk.Label(bar, text="mA").pack(side="left")
        self.b_cur = ttk.Button(bar, text="적용", width=5, command=self.apply_current)
        self.b_cur.pack(side="left", padx=(4, 8))
        ttk.Label(bar, text="단계").pack(side="left")
        self.step_var = tk.StringVar(value="5")
        ttk.Combobox(bar, textvariable=self.step_var, width=5, values=("0.1", "0.5", "1", "5", "10", "25"),
                     justify="right").pack(side="left", padx=2)
        ttk.Label(bar, text="mA").pack(side="left")
        ttk.Button(bar, text="▲", width=3, command=lambda: self.step_current(+1)).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="▼", width=3, command=lambda: self.step_current(-1)).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="색", width=4, command=self.open_colors).pack(side="left")
        ttk.Button(bar, text="데이터", command=self.open_data).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="PNG 저장", command=self.save_png).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="요약", command=self.show_summary).pack(side="left", padx=(4, 0))

        self.status = tk.Label(r, text="", anchor="w", font=("TkDefaultFont", 11, "bold"), fg="white",
                               bg="#8d8d8d", padx=8, pady=3)
        self.status.pack(side="top", fill="x")
        self.info = ttk.Label(r, text="", anchor="w", padding=(8, 1))
        self.info.pack(side="top", fill="x")

        self.fig = Figure(figsize=(13.5, 8.2))
        self.canvas = FigureCanvasTkAgg(self.fig, master=r)
        tb = NavigationToolbar2Tk(self.canvas, r, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)

    def _placeholder(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.axis("off")
        c = self.cfg
        ax.text(0.5, 0.55, "▶ 시작  →  극성 점검 후 LED ON", ha="center", va="center", fontsize=18, color="#555")
        ax.text(0.5, 0.42, f"프로파일 {c['profile'] or '-'}   |   {c['current_mA']:g} mA   |   전압 상한 {c['compliance']:g} V"
                f"   |   tol ±{c['tol_pct']:g}%", ha="center", va="center", fontsize=12, color="#777")
        ax.text(0.5, 0.33, f"데이터 폴더 {config.home()}", ha="center", va="center", fontsize=10, color="#999")
        self.canvas.draw_idle()
        self._set_status("READY" if not self.sim else "READY (SIMULATOR)", "#8d8d8d")

    def _set_status(self, text, color):
        self.status.configure(text=text, bg=color)

    @property
    def running(self):
        return self.s is not None and self.s.started and not self.s.stopped

    def _refresh_buttons(self):
        if self.busy:
            self.b_run.configure(state="disabled")
        elif self.running:
            self.b_run.configure(state="normal", text="■ 정지")
        else:
            self.b_run.configure(state="disabled" if self.attached else "normal", text="▶ 시작")
        self.b_paths.configure(state="disabled" if (self.running or self.busy or self.attached) else "normal")
        self.b_dev.configure(text=f"장비: {self._port_label()}")

    # ---------------- 시작 / 정지 ----------------
    def toggle_run(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def _resolve(self):
        """cfg → LedSession 인자. 문제가 있으면 ValueError."""
        c = self.cfg
        prof = Profile.load(c["profile"]) if c["profile"] else None
        i_max = c["i_max_mA"] / 1e3 if c["i_max_mA"] else (prof.get("i_max_A") if prof else None)
        if prof and prof.get("i_max_A") and i_max > prof["i_max_A"] * (1 + 1e-9):
            raise ValueError(f"전류 상한 {i_max * 1e3:g} mA > 프로파일 값 {prof['i_max_A'] * 1e3:g} mA "
                             f"(올리려면 LED 설정 → 프로파일에 저장)")
        if not i_max:
            raise ValueError("전류 상한(i_max)이 없음 — LED 설정에서 프로파일을 고르거나 상한을 입력")
        cur = _f(self.cur_var.get())
        if cur is None or not 0 < cur / 1e3 <= i_max:
            raise ValueError(f"전류는 0 < I ≤ {i_max * 1e3:g} mA")
        c["current_mA"] = cur
        rng = None if str(c["range"]) == "auto" else int(c["range"])
        sim = self.sim
        return prof, dict(current=cur / 1e3, compliance=c["compliance"], i_max=i_max,
                          lambda_nm=c["lambda_nm"] or (prof.get("lambda_nm") if prof else None), pm_range=rng,
                          tol=c["tol_pct"] / 100, window_s=c["window"] if not sim else min(c["window"], 5.0),
                          vf_guard=c["vf_guard"] or None, pd_rate=c["pd_rate"], k_rate=c["k_rate"],
                          fit_every_s=2.0 if sim else 15.0, fit_min_s=3.0 if sim else 20.0,
                          rsense=c["rsense"], note=c["note"], settle_margin=c["margin"])

    def start(self):
        if self.busy or self.running:
            return
        try:
            prof, kw = self._resolve()
        except (ValueError, FileNotFoundError) as e:
            messagebox.showerror("시작 불가", str(e))
            return
        self.plot = None
        self._placeholder()
        kw["reset_check"] = self._reset_check
        self._reset_check = False
        self.busy = True
        self._fault_shown = False
        self._summary = []
        self._refresh_buttons()
        self._set_status("장비 연결 / 극성 점검 중 ...", "#e67e22")
        threading.Thread(target=self._start_worker, args=(prof, kw), daemon=True).start()

    def _start_worker(self, prof, kw):
        from .keithley2400 import Keithley2400
        from .newport1936 import Newport1936
        from .cli.common import Tee
        pm = k = s = None
        try:
            pm = Newport1936(self.pm_port)
            k = Keithley2400(self.k_port, exclude=(pm.port,))
            holder = {}
            log = Tee(lambda: holder.get("dir"))
            s = LedSession(pm, k, kw.pop("current"), kw.pop("compliance"), profile=prof, log=log, **kw)
            holder["dir"] = s.run_dir
            s.start()
            self.q.put(("started", s, pm, k))
        except Exception as e:  # noqa: BLE001
            for d in (pm, k):
                try:
                    d and d.close()
                except Exception:  # noqa: BLE001
                    pass
            self.q.put(("start_failed", f"{type(e).__name__}: {e}" if not isinstance(e, (RuntimeError, ValueError))
                        else str(e)))

    def stop(self):
        if not self.running or self.busy:
            return
        self.busy = True
        self._refresh_buttons()
        self._set_status("LED OFF / 1936-R 원복 중 ...", "#555555")
        threading.Thread(target=self._stop_worker, daemon=True).start()

    def _stop_worker(self):
        try:
            self.s.stop()
        finally:
            if not self.attached:
                for d in (self.pm, self.k):
                    try:
                        d and d.close()
                    except Exception:  # noqa: BLE001
                        pass
            self.q.put(("stopped",))

    def _attach_plot(self):
        self.plot = LivePlot(self.s, fig=self.fig, colors=self.colors, on_done=self._duration_done,
                             duration_s=self.cfg.get("duration") or None)
        self.canvas.draw_idle()

    def _duration_done(self):
        self._close_after_stop = self.attached      # led-live --duration: 끝나면 창도 닫고 CLI로 돌아감
        self.stop()

    # ---------------- 주기 처리 (Tk 주 스레드) ----------------
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        if self.plot is not None and self.s is not None:
            try:
                self.plot.update()
                self.canvas.draw_idle()
                self._update_status()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        self.root.after(300 if self.running else 600, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "started":
            _, self.s, self.pm, self.k = msg
            self.busy = False
            self._attach_plot()
        elif kind == "start_failed":
            self.busy = False
            self._placeholder()
            if "--reset-check" in msg[1]:
                if messagebox.askyesno("극성 점검 기준과 다름", msg[1] + "\n\n같은 LED가 확실하면 점검 기준을 이번 값으로 "
                                       "갱신하고 다시 시작할까요?"):
                    self._reset_check = True
                    self.root.after(100, self.start)
            else:
                messagebox.showerror("시작 실패", msg[1] + ("\n\n장비 창의 [포트 찾기]로 어느 포트에 무엇이 붙어 있는지 확인할 수 있습니다."
                                                    if "찾지 못함" in msg[1] or "포트" in msg[1] else ""))
        elif kind == "stopped":
            self.busy = False
            self._after_stop()
            if self._close_after_stop:
                self.root.after(500, self.root.destroy)
        elif kind == "error":
            messagebox.showerror(msg[1], msg[2])
        elif kind == "info":
            self.log(msg[1])
        self._refresh_buttons()

    def _update_status(self):
        st = self.s.status()
        color, label = STYLE.get(st["state"], ("#8d8d8d", st["state"].upper()))
        f = st.get("fit")
        txt = f"{label}   t {fmt_t(st.get('t_since_on'))}"
        if st.get("n", 0) >= 10:
            txt += f"   I_pd {st['y_now'] * 1e6:.4f} µA"
        if f:
            txt += f"   최종값 대비 {f['dev_pct']:+.3f}%"
        if st.get("fault"):
            txt += f"   FAULT: {st['fault']}"
        self._set_status(txt, color)
        self.info.configure(text=f"LED {self.s.current * 1e3:g} mA (상한 {self.s.i_max * 1e3:g})   "
                                 f"V {st['V']:.3f} / cap {self.s.compliance:g} V   "
                                 f"guard {self.s.vf_guard or '-'}   1936-R r{st['pd_range']}   "
                                 f"{self.s.pm.port} / {self.s.k.port}   →  {self.s.run_dir}")
        if st["state"] == "fault" and not self._fault_shown:
            self._fault_shown = True
            self.stop()
            messagebox.showwarning("FAULT — LED OFF", f"{st['fault']}\n\nLED는 꺼졌습니다. 기록: {self.s.run_dir}")

    def _after_stop(self):
        s = self.s
        try:
            if self.plot is not None:
                self.plot.save(os.path.join(s.run_dir, "final.png"))
        except Exception as e:  # noqa: BLE001
            self.log(f"final.png 저장 실패: {e}")
        if not self.attached:
            from .cli.live import report
            lines = []
            report(s, SimpleNamespace(tol=s.tol), s.profile, log=lambda m: (lines.append(m), self.log(m)))
            self._summary = lines
            self.show_summary()

    # ---------------- 전류 ----------------
    def apply_current(self, value_mA=None):
        v = value_mA if value_mA is not None else _f(self.cur_var.get())
        if v is None or v <= 0:
            messagebox.showerror("전류", "숫자(mA) 입력")
            return
        if not self.running:
            self.cfg["current_mA"] = v
            self.cur_var.set(f"{v:g}")
            if self.plot is None:
                self._placeholder()
            return
        if v / 1e3 > self.s.i_max * (1 + 1e-9):
            messagebox.showerror("전류", f"{v:g} mA > 전류 상한 {self.s.i_max * 1e3:g} mA")
            self.cur_var.set(f"{self.s.current * 1e3:g}")
            return
        if abs(v / 1e3 - self.s.current) < 1e-9:
            return
        if not messagebox.askyesno("전류 변경", f"{self.s.current * 1e3:g} → {v:g} mA\n"
                                   "안정도 판정은 이 시각부터 새로 시작합니다 (회차 비교에는 첫 전류 구간만)."):
            self.cur_var.set(f"{self.s.current * 1e3:g}")
            return
        self.cur_var.set(f"{v:g}")

        def work():
            try:
                self.s.set_current_live(v / 1e3)
            except Exception as e:  # noqa: BLE001
                self.q.put(("error", "전류 변경 실패", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def step_current(self, sign):
        step = _f(self.step_var.get())
        cur = _f(self.cur_var.get())
        if not step or cur is None:
            return
        new = round(max(step, cur + sign * step), 6)
        if self.running:
            new = min(new, self.s.i_max * 1e3)
        self.cur_var.set(f"{new:g}")
        self.apply_current(new)

    # ---------------- 기타 버튼 ----------------
    def save_png(self):
        d = self.s.run_dir if self.s else config.home()
        p = filedialog.asksaveasfilename(initialdir=d if os.path.isdir(d) else config.home(),
                                         initialfile=f"snap_{datetime.now():%H%M%S}.png",
                                         defaultextension=".png", filetypes=[("PNG", "*.png")])
        if p:
            self.fig.savefig(p, dpi=130)

    def show_summary(self):
        if not self._summary:
            if not self.s:
                messagebox.showinfo("요약", "아직 측정한 회차가 없음")
                return
            self._summary = [f"측정 중 — {self.s.run_dir}"]
        TextWindow(self.root, "요약", "\n".join(self._summary), folder=self.s.run_dir if self.s else None)

    def open_settings(self):
        SettingsWindow(self)

    def open_paths(self):
        PathsWindow(self)

    def open_devices(self):
        DevicesWindow(self)

    def _port_label(self):
        if self.running or self.attached:
            if self.s is not None and getattr(self.s, "pm", None) is not None:
                return f"{self.s.pm.port}/{self.s.k.port}"
        if not self.pm_port and not self.k_port:
            return "자동"
        return f"{self.pm_port or '자동'}/{self.k_port or '자동'}"

    def open_colors(self):
        ColorsWindow(self)

    def open_data(self):
        if self.data_win is not None and self.data_win.winfo_exists():
            self.data_win.lift()
            return
        self.data_win = DataWindow(self)

    def apply_colors(self, colors):
        self.colors.update(colors)
        if self.plot is not None:
            self.plot.apply_colors(colors)
            self.canvas.draw_idle()

    # ---------------- 종료 / 오류 ----------------
    def on_close(self):
        if self.busy:
            return
        if self.running:
            if not messagebox.askyesno("종료", "측정 중입니다. LED를 끄고 종료할까요?"):
                return
            self.stop()
            self._wait_close()
            return
        self.root.destroy()

    def _wait_close(self):
        if self.busy or self.running:
            self.root.after(200, self._wait_close)
        else:
            self.root.after(300, self.root.destroy)

    def _tk_error(self, exc, val, tb):
        try:
            p = os.path.join(config.logs_dir(), f"{datetime.now():%Y%m%d_%H%M%S}_gui.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"lablive {__version__}\n")
                traceback.print_exception(exc, val, tb, file=f)
        except OSError:
            p = None
        traceback.print_exception(exc, val, tb)
        messagebox.showerror("오류", f"{exc.__name__}: {val}\n기록: {p}")


# =====================================================================
class TextWindow(tk.Toplevel):
    def __init__(self, master, title, text, folder=None):
        super().__init__(master)
        self.title(title)
        self.geometry("820x420")
        fr = ttk.Frame(self)
        fr.pack(fill="both", expand=True)
        t = tk.Text(fr, wrap="none", font=("TkFixedFont", 10))
        ys = ttk.Scrollbar(fr, orient="vertical", command=t.yview)
        xs = ttk.Scrollbar(fr, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        t.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        fr.rowconfigure(0, weight=1)
        fr.columnconfigure(0, weight=1)
        t.insert("1.0", text)
        t.configure(state="disabled")
        b = ttk.Frame(self, padding=4)
        b.pack(fill="x")
        if folder:
            ttk.Button(b, text="회차 폴더 열기", command=lambda: open_path(folder)).pack(side="left")
        ttk.Button(b, text="닫기", command=self.destroy).pack(side="right")


# =====================================================================
class SettingsWindow(tk.Toplevel):
    """프로파일 + 측정 설정. 측정 중에는 실시간 적용 가능한 항목만 편집."""
    LIVE = {"compliance", "vf_guard", "tol_pct", "margin", "note", "i_max_mA"}
    FIELDS = [  # key, 라벨, 단위/설명
        ("i_max_mA", "전류 상한 i_max", "mA  (프로파일 값 이하. 측정 중엔 낮추기만)"),
        ("compliance", "전압 상한 (compliance)", "V"),
        ("vf_guard", "경고 전압 (vf_guard)", "V  (비우면 끔)"),
        ("lambda_nm", "파장 λ", "nm  (기록용, 단위 A에선 값에 영향 없음)"),
        ("tol_pct", "안정 판정 tol", "%"),
        ("margin", "SETTLED 여유 margin", "× tol  (0.5 = 예측 최종값 ±tol/2)"),
        ("window", "단기 판정 창", "s"),
        ("range", "1936-R range", "auto 또는 0–7"),
        ("pd_rate", "1936-R 읽기", "Hz"),
        ("k_rate", "2400 읽기", "Hz"),
        ("duration", "자동 종료", "s  (0 = 수동)"),
    ]

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("LED 설정")
        self.resizable(False, False)
        self.transient(app.root)
        run = app.running
        c = app.cfg
        fr = ttk.Frame(self, padding=10)
        fr.pack(fill="both")
        ttk.Label(fr, text="프로파일").grid(row=0, column=0, sticky="w")
        self.pvar = tk.StringVar(value=c["profile"] or NO_PROFILE)
        self.pbox = ttk.Combobox(fr, textvariable=self.pvar, values=[NO_PROFILE] + Profile.names(),
                                 state="disabled" if run else "readonly", width=26)
        self.pbox.grid(row=0, column=1, sticky="w")
        self.pbox.bind("<<ComboboxSelected>>", lambda _e: self.load_profile())
        ttk.Button(fr, text="새 프로파일...", command=self.new_profile,
                   state="disabled" if run else "normal").grid(row=0, column=2, sticky="w", padx=6)
        self.pinfo = ttk.Label(fr, text="", foreground="#555", wraplength=520, justify="left")
        self.pinfo.grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))

        self.vars = {}
        for i, (key, lab, unit) in enumerate(self.FIELDS, start=2):
            ttk.Label(fr, text=lab).grid(row=i, column=0, sticky="w", pady=1)
            v = tk.StringVar(value="" if c.get(key) is None else f"{c[key]:g}" if isinstance(c[key], float)
                             else str(c[key]))
            st = "normal" if (not run or key in self.LIVE) else "disabled"
            ttk.Entry(fr, textvariable=v, width=12, justify="right", state=st).grid(row=i, column=1, sticky="w")
            ttk.Label(fr, text=unit, foreground="#666").grid(row=i, column=2, sticky="w", padx=6)
            self.vars[key] = v
        i += 1
        ttk.Label(fr, text="4-wire (rsense)").grid(row=i, column=0, sticky="w")
        self.rs = tk.BooleanVar(value=bool(c["rsense"]))
        ttk.Checkbutton(fr, variable=self.rs, state="disabled" if run else "normal").grid(row=i, column=1, sticky="w")
        i += 1
        ttk.Label(fr, text="메모 (note)").grid(row=i, column=0, sticky="w")
        self.note = tk.StringVar(value=c["note"])
        ttk.Entry(fr, textvariable=self.note, width=50).grid(row=i, column=1, columnspan=2, sticky="w")
        i += 1
        ttk.Label(fr, text=("측정 중: 회색 항목은 다음 측정부터. 전류는 위 도구 막대에서." if run else
                            "전류는 위 도구 막대에서."), foreground="#a05000").grid(row=i, column=0, columnspan=3,
                                                                          sticky="w", pady=(8, 0))
        b = ttk.Frame(self, padding=(10, 0, 10, 10))
        b.pack(fill="x")
        ttk.Button(b, text="적용", command=self.apply).pack(side="left")
        self.b_save = ttk.Button(b, text="프로파일에 저장", command=self.save_profile)
        self.b_save.pack(side="left", padx=6)
        ttk.Button(b, text="닫기", command=self.destroy).pack(side="right")
        self._show_profile_info()

    def _show_profile_info(self):
        name = self.pvar.get()
        if name == NO_PROFILE:
            self.pinfo.configure(text="프로파일 없이: 전류 상한을 직접 입력해야 함. 기준 모델/회차 비교 없음.")
            self.b_save.configure(state="disabled")
            return
        try:
            p = Profile.load(name)
        except FileNotFoundError as e:
            self.pinfo.configure(text=str(e))
            return
        ms = ", ".join(f"{m['current_A'] * 1e3:g}" for m in p["models"]) or "없음"
        chk = p.get("check")
        self.pinfo.configure(text=f"{p.get('description', '')}\n기준 모델 [{ms}] mA,  회차 {len(p['runs'])}개,  "
                                  f"점검 기준 {('%.3f V' % chk['V']) if chk else '없음'}\n{p.path}")
        self.b_save.configure(state="normal")

    def load_profile(self):
        name = self.pvar.get()
        if name != NO_PROFILE:
            p = Profile.load(name)
            for key, pk, scale in (("i_max_mA", "i_max_A", 1e3), ("compliance", "compliance_V", 1),
                                   ("vf_guard", "vf_guard_V", 1), ("lambda_nm", "lambda_nm", 1)):
                v = p.get(pk)
                self.vars[key].set("" if v is None else f"{v * scale:g}")
        self._show_profile_info()

    def _read(self):
        out = {}
        for key, lab, _ in self.FIELDS:
            raw = self.vars[key].get().strip()
            if key == "range":
                if raw.lower() in ("", "auto"):
                    out[key] = "auto"
                elif raw.isdigit() and 0 <= int(raw) <= 7:
                    out[key] = int(raw)
                else:
                    raise ValueError("1936-R range는 auto 또는 0–7")
                continue
            if raw == "":
                if key in ("vf_guard", "lambda_nm", "i_max_mA"):
                    out[key] = None
                    continue
                raise ValueError(f"{lab} 값 없음")
            v = _f(raw)
            if v is None:
                raise ValueError(f"{lab}: 숫자 아님 ({raw})")
            out[key] = v
        if out["lambda_nm"] is not None:
            out["lambda_nm"] = int(out["lambda_nm"])
        if not 0 < out["compliance"] <= 21:
            raise ValueError("전압 상한은 0 < V ≤ 21")
        out["rsense"] = self.rs.get()
        out["note"] = self.note.get()
        out["profile"] = "" if self.pvar.get() == NO_PROFILE else self.pvar.get()
        return out

    def apply(self):
        app = self.app
        try:
            new = self._read()
            if app.running:
                s = app.s
                if new["compliance"] != s.compliance:
                    s.set_compliance_live(new["compliance"])
                if (new["vf_guard"] or None) != s.vf_guard:
                    s.set_vf_guard(new["vf_guard"])
                s.set_tol(new["tol_pct"] / 100, new["margin"])
                if new["note"] != s.note:
                    s.set_note(new["note"])
                if new["i_max_mA"] and new["i_max_mA"] / 1e3 < s.i_max:
                    if new["i_max_mA"] / 1e3 < s.current:
                        raise ValueError(f"현재 전류 {s.current * 1e3:g} mA보다 낮은 상한 — 전류를 먼저 낮출 것")
                    s.i_max = new["i_max_mA"] / 1e3
                    s.event(f"i_max -> {new['i_max_mA']:g} mA")
            app.cfg.update(new)
            if app.plot is None:
                app._placeholder()
        except (ValueError, RuntimeError, FileNotFoundError) as e:
            messagebox.showerror("적용 실패", str(e), parent=self)
            return
        messagebox.showinfo("LED 설정", "적용됨" + (" (측정 중 변경 가능한 항목만)" if app.running else ""), parent=self)

    def save_profile(self):
        try:
            new = self._read()
            p = Profile.load(new["profile"])
        except (ValueError, FileNotFoundError) as e:
            messagebox.showerror("저장 실패", str(e), parent=self)
            return
        ch = {"compliance_V": new["compliance"], "vf_guard_V": new["vf_guard"] or None, "lambda_nm": new["lambda_nm"]}
        if new["i_max_mA"]:
            ch["i_max_A"] = new["i_max_mA"] / 1e3
        diff = {k: (p.get(k), v) for k, v in ch.items() if p.get(k) != v}
        if not diff:
            messagebox.showinfo("프로파일", "바뀐 값 없음", parent=self)
            return
        msg = "\n".join(f"{k}: {a} → {b}" for k, (a, b) in diff.items())
        if "i_max_A" in diff and (diff["i_max_A"][0] or 0) < diff["i_max_A"][1]:
            msg += "\n\n전류 상한을 올립니다. LED 정격을 확인했나요?"
        if messagebox.askyesno(f"프로파일 {p.name} 저장", msg, parent=self):
            p.update(**ch)
            self._show_profile_info()

    def new_profile(self):
        name = simpledialog.askstring("새 프로파일", "이름 (공백/경로 문자 없이)", parent=self)
        if not name:
            return
        try:
            new = self._read()
            if not new["i_max_mA"]:
                raise ValueError("전류 상한(i_max) 먼저 입력")
            p = Profile.create(name, lambda_nm=new["lambda_nm"], i_max_A=new["i_max_mA"] / 1e3,
                               compliance_V=new["compliance"], vf_guard_V=new["vf_guard"] or None,
                               description=new["note"] or "")
        except (ValueError, FileExistsError) as e:
            messagebox.showerror("생성 실패", str(e), parent=self)
            return
        self.pbox.configure(values=[NO_PROFILE] + Profile.names())
        self.pvar.set(p.name)
        self._show_profile_info()


# =====================================================================
class PathsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("경로")
        self.resizable(False, False)
        self.transient(app.root)
        u = config.load_user()
        fr = ttk.Frame(self, padding=10)
        fr.pack(fill="both")
        self.home = tk.StringVar(value=config.user_home())
        self.runs = tk.StringVar(value=u.get("runs") or "")
        r = 0
        ttk.Label(fr, text="데이터 폴더 (home)", font=("TkDefaultFont", 10, "bold")).grid(row=r, column=0, sticky="w")
        r += 1
        ttk.Entry(fr, textvariable=self.home, width=64).grid(row=r, column=0, sticky="w")
        bb = ttk.Frame(fr)
        bb.grid(row=r, column=1, sticky="w", padx=4)
        ttk.Button(bb, text="찾아보기", command=lambda: self._browse(self.home)).pack(side="left")
        ttk.Button(bb, text="기본값", command=lambda: self.home.set(config.default_home())).pack(side="left", padx=2)
        ttk.Button(bb, text="열기", command=lambda: open_path(self.home.get())).pack(side="left")
        r += 1
        ttk.Label(fr, text=f"기본값 {config.default_home()}   (기존 데이터를 이어 쓰려면 예: C:\\code\\control)",
                  foreground="#666").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        ttk.Label(fr, text="회차 저장 폴더 (비우면 <home>\\runs)", font=("TkDefaultFont", 10, "bold")).grid(
            row=r, column=0, sticky="w", pady=(10, 0))
        r += 1
        ttk.Entry(fr, textvariable=self.runs, width=64).grid(row=r, column=0, sticky="w")
        bb = ttk.Frame(fr)
        bb.grid(row=r, column=1, sticky="w", padx=4)
        ttk.Button(bb, text="찾아보기", command=lambda: self._browse(self.runs)).pack(side="left")
        ttk.Button(bb, text="비우기", command=lambda: self.runs.set("")).pack(side="left", padx=2)
        ttk.Button(bb, text="열기", command=lambda: open_path(self.runs.get() or os.path.join(self.home.get(), "runs"))
                   ).pack(side="left")
        r += 1
        self.info = ttk.Label(fr, text="", foreground="#444", justify="left")
        self.info.grid(row=r, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._info()
        b = ttk.Frame(self, padding=(10, 0, 10, 10))
        b.pack(fill="x")
        ttk.Button(b, text="저장", command=self.save).pack(side="left")
        ttk.Button(b, text="닫기", command=self.destroy).pack(side="right")

    def _browse(self, var):
        d = filedialog.askdirectory(parent=self, initialdir=var.get() or config.home())
        if d:
            var.set(os.path.normpath(d))

    def _info(self):
        h = config.home()
        self.info.configure(text=(f"지금 사용 중:  {h}   ({config.home_source()})\n"
                                  f"  프로파일   {os.path.join(h, 'profiles')}\n"
                                  f"  회차       {config.runs_dir()}\n"
                                  f"  오류 기록  {os.path.join(h, 'logs')}\n"
                                  f"  1936-R 평소 설정  {os.path.join(h, 'pm_default.json')}\n"
                                  f"설정 파일  {config.USER_FILE}"
                                  + ("\n시뮬레이터 모드: 데이터는 <home>\\sim 에 따로" if self.app.sim else "")))

    def save(self):
        h = self.home.get().strip()
        if not h:
            messagebox.showerror("경로", "데이터 폴더 필요", parent=self)
            return
        try:
            os.makedirs(h, exist_ok=True)
            if self.runs.get().strip():
                os.makedirs(self.runs.get().strip(), exist_ok=True)
        except OSError as e:
            messagebox.showerror("경로", f"폴더를 만들 수 없음: {e}", parent=self)
            return
        config.save_user(home=os.path.abspath(h), runs=os.path.abspath(self.runs.get().strip())
                         if self.runs.get().strip() else None)
        if self.app.sim:
            config.set_home(None)
            config.set_home(os.path.join(config.home(), "sim"))
        self._info()
        if self.app.plot is None:
            self.app._placeholder()
        messagebox.showinfo("경로", "저장됨 (다음 측정부터)", parent=self)


# =====================================================================
class DevicesWindow(tk.Toplevel):
    """포트 선택. '자동' = 시작할 때마다 모든 COM 포트에 *IDN?을 보내 찾음 (USB-시리얼 어댑터는 꽂는 위치에 따라
    COM 번호가 바뀌므로 기본). [포트 찾기]는 각 포트를 잠깐 열어 무엇이 붙어 있는지 표로 보여 준다."""
    AUTO = "자동"

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("장비 / 포트")
        self.transient(app.root)
        self.geometry("760x420")
        busy = app.running or app.busy or app.attached
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        from .serialdev import list_ports_info
        ports = [p for p, _ in list_ports_info()]
        self.pm = tk.StringVar(value=app.pm_port or self.AUTO)
        self.k = tk.StringVar(value=app.k_port or self.AUTO)
        st = "disabled" if busy else "readonly"
        ttk.Label(top, text="Newport 1936-R (38400)").grid(row=0, column=0, sticky="w")
        self.cb_pm = ttk.Combobox(top, textvariable=self.pm, values=[self.AUTO] + ports, width=14, state=st)
        self.cb_pm.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(top, text="Keithley 2400 (9600)").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.cb_k = ttk.Combobox(top, textvariable=self.k, values=[self.AUTO] + ports, width=14, state=st)
        self.cb_k.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        self.b_scan = ttk.Button(top, text="포트 찾기", command=self.scan, state="disabled" if busy else "normal")
        self.b_scan.grid(row=0, column=2, rowspan=2, padx=10)
        if busy and app.s is not None and getattr(app.s, "pm", None) is not None:
            msg = f"측정 중 — 사용 중인 포트: 1936-R {app.s.pm.port}, 2400 {app.s.k.port} (변경은 정지 후)"
        else:
            msg = ("'자동' 권장: 시작할 때마다 모든 COM 포트에 *IDN?을 보내 찾음 (어댑터를 다른 USB 구멍에 꽂아도 동작). "
                   "고정하면 그 포트만 열고 *IDN?으로 맞는 장비인지 확인.")
        ttk.Label(top, text=msg, foreground="#555", wraplength=720, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        fr = ttk.Frame(self, padding=(10, 0))
        fr.pack(fill="both", expand=True)
        cols = ("port", "desc", "kind", "idn")
        self.tv = ttk.Treeview(fr, columns=cols, show="headings", height=8)
        for c, w, t in zip(cols, (70, 230, 80, 360), ("포트", "어댑터", "장비", "*IDN? 응답")):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w", stretch=c == "idn")
        self.tv.pack(fill="both", expand=True)
        for p, d in list_ports_info():
            self.tv.insert("", "end", iid=p, values=(p, d, "", "([포트 찾기]로 확인)"))
        self.status = ttk.Label(self, text="", foreground="#555", padding=(10, 2))
        self.status.pack(fill="x")
        b = ttk.Frame(self, padding=(10, 4, 10, 10))
        b.pack(fill="x")
        ttk.Button(b, text="적용", command=self.apply, state="disabled" if busy else "normal").pack(side="left")
        ttk.Button(b, text="모두 자동", command=lambda: (self.pm.set(self.AUTO), self.k.set(self.AUTO)),
                   state="disabled" if busy else "normal").pack(side="left", padx=6)
        ttk.Button(b, text="닫기", command=self.destroy).pack(side="right")

    def scan(self):
        from .newport1936 import echo_off
        from .serialdev import kick, scan_instruments
        self.b_scan.configure(state="disabled")
        self.status.configure(text="포트 확인 중 … (포트당 1~2초)")
        q = queue.Queue()

        def work():
            try:
                res = scan_instruments([("1936-R", 38400, "NEWPORT", echo_off),
                                        ("2400", 9600, "KEITHLEY", kick, lambda d: d.write("*CLS", 0.1))],
                                       log=lambda m: q.put(("log", m)), roles={"1936-R": "pm", "2400": "k"})
                q.put(("done", res))
            except Exception as e:  # noqa: BLE001
                q.put(("err", str(e)))
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not self.winfo_exists():
                return
            try:
                while True:
                    kind, val = q.get_nowait()
                    if kind == "log":
                        self.status.configure(text=f"확인 중: {val}")
                    elif kind == "err":
                        self.status.configure(text=f"실패: {val}")
                        self.b_scan.configure(state="normal")
                        return
                    else:
                        self._show(val)
                        return
            except queue.Empty:
                pass
            self.after(150, poll)
        poll()

    def _show(self, res):
        self.tv.delete(*self.tv.get_children())
        found = {}
        for r in res:
            self.tv.insert("", "end", values=(r["port"], r["desc"], r["kind"] or "-", r["idn"] or "응답 없음"))
            if r["kind"]:
                found.setdefault(r["kind"], r["port"])
        ports = [r["port"] for r in res]
        self.cb_pm.configure(values=[self.AUTO] + ports)
        self.cb_k.configure(values=[self.AUTO] + ports)
        self.b_scan.configure(state="normal")
        txt = ", ".join(f"{k} → {p}" for k, p in found.items()) or "장비 응답 없음 — 전원/케이블/2400 RS-232 설정 확인"
        self.status.configure(text=f"찾음: {txt}   (포트를 고정하려면 위에서 고르고 [적용], 보통은 '자동' 그대로)")

    def apply(self):
        pm = None if self.pm.get() == self.AUTO else self.pm.get()
        k = None if self.k.get() == self.AUTO else self.k.get()
        if pm and pm == k:
            messagebox.showerror("포트", "두 장비에 같은 포트", parent=self)
            return
        self.app.pm_port, self.app.k_port = pm, k
        if not self.app.sim:
            config.save_user(ports={"pm": pm, "k": k} if (pm or k) else None)
        self.app._refresh_buttons()
        messagebox.showinfo("포트", f"1936-R {pm or '자동'}, 2400 {k or '자동'}" +
                            ("" if self.app.sim else " (다음 실행에도 유지)"), parent=self)


# =====================================================================
class ColorsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("그래프 색")
        self.resizable(False, False)
        self.transient(app.root)
        fr = ttk.Frame(self, padding=10)
        fr.pack()
        self.sw = {}
        for i, (k, name) in enumerate(COLOR_NAMES.items()):
            ttk.Label(fr, text=name).grid(row=i, column=0, sticky="w", pady=2)
            sw = tk.Label(fr, width=6, bg=app.colors[k], relief="solid", bd=1)
            sw.grid(row=i, column=1, padx=6)
            sw.bind("<Button-1>", lambda _e, k=k: self.pick(k))
            ttk.Button(fr, text="변경", command=lambda k=k: self.pick(k)).grid(row=i, column=2)
            self.sw[k] = sw
        b = ttk.Frame(self, padding=(10, 0, 10, 10))
        b.pack(fill="x")
        ttk.Button(b, text="기본값", command=self.reset).pack(side="left")
        ttk.Button(b, text="저장", command=self.save).pack(side="left", padx=6)
        ttk.Button(b, text="닫기", command=self.destroy).pack(side="right")

    def pick(self, k):
        rgb, hx = colorchooser.askcolor(color=self.app.colors[k], parent=self, title=COLOR_NAMES[k])
        if hx:
            self.sw[k].configure(bg=hx)
            self.app.apply_colors({k: hx})

    def reset(self):
        for k, v in COLORS.items():
            self.sw[k].configure(bg=v)
        self.app.apply_colors(dict(COLORS))

    def save(self):
        changed = {k: v for k, v in self.app.colors.items() if COLORS.get(k) != v}
        config.save_user(colors=changed or None)
        messagebox.showinfo("색", "저장됨 (다음 실행에도 적용)", parent=self)


# =====================================================================
class TableTab:
    """Treeview 하나 (스크롤 + 점진 추가)."""
    LIMIT = 200000

    def __init__(self, nb, title, columns):
        self.frame = ttk.Frame(nb)
        nb.add(self.frame, text=title)
        self.columns = list(columns)
        tv = self.tv = ttk.Treeview(self.frame, columns=self.columns, show="headings")
        for c in self.columns:
            tv.heading(c, text=c)
            tv.column(c, width=130 if c != "msg" else 600, anchor="e" if c != "msg" else "w", stretch=c == "msg")
        ys = ttk.Scrollbar(self.frame, orient="vertical", command=tv.yview)
        xs = ttk.Scrollbar(self.frame, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.frame.rowconfigure(0, weight=1)
        self.frame.columnconfigure(0, weight=1)
        self.n = 0

    def add_rows(self, rows, autoscroll):
        last = None
        for r in rows:
            last = self.tv.insert("", "end", values=[_fmt(x) for x in r])
            self.n += 1
        kids = None
        if self.n > self.LIMIT:
            kids = self.tv.get_children()
            self.tv.delete(*kids[: self.n - self.LIMIT])
            self.n = self.LIMIT
        if autoscroll and last:
            self.tv.see(last)


class DataWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("데이터")
        self.geometry("900x560")
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        ttk.Button(bar, text="CSV 열기...", command=self.open_csv).pack(side="left")
        ttk.Button(bar, text="회차 폴더 열기", command=self.open_folder).pack(side="left", padx=4)
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="최신 행 따라가기", variable=self.auto).pack(side="left", padx=10)
        self.lbl = ttk.Label(bar, text="", foreground="#555")
        self.lbl.pack(side="right")
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.sess = None
        self.tabs = {}
        self._tick()

    def _bind_session(self):
        s = self.app.s
        if s is None or s is self.sess:
            return
        for t in self.tabs.values():
            self.nb.forget(t.frame)
        self.sess = s
        self.tabs = {"pd": TableTab(self.nb, "광전류 (pd.csv)", ("t_s", "I_pd_A", "range")),
                     "k": TableTab(self.nb, "2400 (k.csv)", ("t_s", "V", "I_A", "trip")),
                     "ev": TableTab(self.nb, "이벤트", ("t_s", "msg"))}
        self.nb.select(0)

    def _tick(self):
        if not self.winfo_exists():
            return
        self._bind_session()
        s = self.sess
        if s is not None:
            for key, fn in (("pd", s.snapshot_pd), ("k", s.snapshot_k)):
                a = fn()
                if a is not None and len(a) > self.tabs[key].n:
                    self.tabs[key].add_rows(a[self.tabs[key].n:].tolist(), self.auto.get())
            ev = s.events
            if len(ev) > self.tabs["ev"].n:
                self.tabs["ev"].add_rows([list(e) for e in ev[self.tabs["ev"].n:]], self.auto.get())
            self.lbl.configure(text=f"광전류 {self.tabs['pd'].n}행, 2400 {self.tabs['k'].n}행   {s.run_dir}")
        self.after(1000, self._tick)

    def open_folder(self):
        d = self.sess.run_dir if self.sess else config.runs_dir()
        open_path(d if os.path.isdir(d) else config.runs_dir())

    def open_csv(self):
        p = filedialog.askopenfilename(parent=self, initialdir=config.runs_dir(),
                                       filetypes=[("CSV", "*.csv"), ("모든 파일", "*.*")])
        if not p:
            return
        with open(p, newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.reader(f))
        if not rows:
            return
        tab = TableTab(self.nb, f"{os.path.basename(os.path.dirname(p))}/{os.path.basename(p)}",
                       [c or f"c{i}" for i, c in enumerate(rows[0])])
        self.nb.select(tab.frame)
        data = rows[1:]

        def feed(i=0):                    # 큰 파일도 창이 멈추지 않게 나눠서
            if not tab.frame.winfo_exists():
                return
            tab.add_rows(data[i:i + 3000], False)
            if i + 3000 < len(data):
                self.after(1, feed, i + 3000)
        feed()


def run_app(cfg=None, sim=False, pm_port=None, k_port=None, session=None, log=print):
    root = tk.Tk()
    app = LiveApp(root, cfg=cfg, sim=sim, pm_port=pm_port, k_port=k_port, session=session, log=log)
    root.mainloop()
    return app

