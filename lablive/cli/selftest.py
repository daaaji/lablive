"""lablive-selftest — 설치 확인. 장비 없이 시뮬레이터로 전체 흐름을 돌린다 (약 1분).

  lablive-selftest            임시 폴더에서 (실제 데이터 폴더는 건드리지 않음)
  lablive-selftest --keep     결과 폴더를 지우지 않고 경로 출력

점검: 패키지/버전, 그래프 창(tkinter), 포트 자동 탐색, 프로파일, I-V 스윕(발진 구간 검출),
      냉간 live 측정(SETTLED, OFF 확인, 1936-R 원복), 기준 모델 등록, 데워진 재시작 감지,
      compliance trip → FAULT/OFF, 다른 LED(점검 전압 불일치) 차단, 전류 상한, 회차 비교 그림,
      펄스 모드(셔터 흉내) + data store 고속 캡처 + led-pulses
"""
import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

RESULTS = []


def check(name, fn):
    t0 = time.time()
    try:
        r = fn()
        ok, note = (r if isinstance(r, tuple) else (bool(r), ""))
    except Exception as e:  # noqa: BLE001
        ok, note = False, f"{type(e).__name__}: {e}"
        traceback.print_exc()
    tag = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
    RESULTS.append((tag, name, note))
    print(f"\n--> [{tag}] {name}  ({time.time() - t0:.1f} s)  {note}\n", flush=True)
    return ok


def _latest(home, pat):
    ds = sorted(glob.glob(os.path.join(home, "runs", pat)))
    if not ds:
        raise FileNotFoundError(pat)
    d = ds[-1]
    with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
        return d, json.load(f)


def _restored(m):
    b, a = m.get("pm_state_before") or {}, m.get("pm_state_after") or {}
    skip = {"PM:RANge?"} if b.get("PM:AUTO?", "").startswith("1") else set()
    return bool(b) and all(a.get(x) == b[x] for x in b if x not in skip)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lablive-selftest")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    import matplotlib
    matplotlib.use("Agg")
    from .. import __version__, config, sim
    from . import common
    common._utf8_console()
    home = tempfile.mkdtemp(prefix="lablive_selftest_")
    H = ["--sim", "--home", home]
    print(f"lablive {__version__} selftest — 임시 폴더 {home}")

    def env():
        import numpy
        import serial
        return True, (f"Python {sys.version.split()[0]}, pyserial {serial.__version__}, numpy {numpy.__version__}, "
                      f"matplotlib {matplotlib.__version__}")
    check("패키지", env)

    def tk():
        try:
            import tkinter  # noqa: F401
            return True, "실시간 그래프 창 사용 가능 (TkAgg)"
        except ImportError:
            return None, "tkinter 없음 → 실시간 창 대신 --no-plot 사용 (python.org 설치본에는 포함)"
    check("그래프 창", tk)

    def font():
        from ..plotstyle import setup
        kr = setup()
        return (True, f"한글 글꼴 {kr} (fallback)") if kr else \
            (None, "한글 글꼴 없음 — 그림 안 문구는 영어라 동작에는 지장 없음")
    check("그림 글꼴", font)

    from . import check as ccheck
    from . import iv as civ
    from . import live as clive
    from . import profile as cprof
    check("포트 자동 탐색 / led-check", lambda: ccheck.main(H) == 0)
    check("led-profile new", lambda: cprof.main(H + ["new", "t", "--lambda", "275", "--i-max", "0.25",
                                                     "--desc", "selftest"]) == 0)

    def iv():
        rc = civ.main(H + ["--profile", "t", "--points", "8", "--dwell", "0.05", "--navg", "5"])
        d, m = _latest(home, "*_iv_*")
        s = m["summary"]
        ok = (rc == 0 and m["k_off_ok"] and _restored(m) and s.get("noisy_points")
              and s.get("suggest_vf_guard_V") and os.path.exists(os.path.join(d, "iv.png")))
        return bool(ok), f"발진 구간 ≤ {s.get('noisy_I_max_mA', 0):.3g} mA, 제안 경고 전압 {s.get('suggest_vf_guard_V')} V"
    check("led-iv (정전류 스윕, 발진 구간 검출)", iv)

    def live_cold():
        rc = clive.main(H + ["--profile", "t", "--current", "0.1", "--no-plot", "--duration", "16"])
        d, m = _latest(home, "*_live_*")
        ok = (rc == 0 and m["k_off_ok"] and not m["fault"] and m["t_first_settled"] is not None
              and _restored(m) and not sim.ST["out"])
        return bool(ok), f"SETTLED {m['t_first_settled']:.1f} s, LED OFF 확인, 1936-R 원복"
    check("led-live 냉간 시작 (판정/OFF/원복)", live_cold)

    def add():
        d, _ = _latest(home, "*_live_*")
        cprof.main(["--home", home, "--sim", "add", "t", d])
        from ..profiles import Profile
        p = Profile.load("t")
        return bool(p["models"]) and p.get("check") is not None, f"모델 {len(p['models'])}개, 점검 기준 {p['check']['V']:.3f} V"
    check("led-profile add (기준 모델)", add)

    # 이하 sim 상태(열 기억)를 유지하려고 라이브러리로 직접
    from .. import Keithley2400, LedSession, Newport1936
    from ..liveplot import LivePlot
    from ..profiles import Profile
    config.set_home(home)

    def session(**kw):
        pm = Newport1936()
        k = Keithley2400(exclude=(pm.port,))
        kw.setdefault("i_max", 0.25)
        kw.setdefault("log", lambda m: print("   ", m))
        return pm, k, LedSession(pm, k, window_s=5, fit_every_s=2, fit_min_s=3, **kw)

    def warm():
        pm, k, s = session(current=0.1, compliance=12.0, profile=Profile.load("t"))
        plot = LivePlot(s)
        try:
            s.start()
            for _ in range(6):
                time.sleep(1)
                plot.update()
            plot.save(os.path.join(s.run_dir, "final.png"))
        finally:
            s.stop()
            pm.close()
            k.close()
        hit = any("START STATE" in e[1] for e in s.events)
        return hit and os.path.exists(os.path.join(s.run_dir, "final.png")), \
            "데워진 시작 → 프로파일 곡선 미사용, 자유 피팅" if hit else "감지 안 됨"
    check("데워진 재시작 감지 + 실시간 그림", warm)

    def trip():
        pm, k, s = session(current=0.25, compliance=7.0)
        try:
            s.start()
            ok = s.wait_stable(timeout=8) is False and s.fault is not None
        finally:
            s.stop()
            pm.close()
            k.close()
        return bool(ok and s.meta["k_off_ok"] and not sim.ST["out"]), f"FAULT: {s.fault}"
    check("compliance trip → FAULT, LED OFF", trip)

    def wrong_led():
        v0 = sim.P["v0"]
        sim.P["v0"] = 3.0
        pm, k, s = session(current=0.1, compliance=12.0, profile=Profile.load("t"))
        try:
            s.start()
            return False, "차단 안 됨"
        except RuntimeError as e:
            return (not sim.ST["out"] and not s.led_on and s.t_on is None), str(e)[:70]
        finally:
            sim.P["v0"] = v0
            s.stop()
            pm.close()
            k.close()
    check("다른 LED (점검 전압 불일치) → 시작 차단", wrong_led)

    def limits():
        msgs = []
        try:
            session(current=0.3, compliance=12.0)
            return False, "i_max 초과가 통과됨"
        except ValueError as e:
            msgs.append(str(e)[:40])
        try:
            clive.main(H + ["--current", "0.1", "--no-plot"])
            return False, "프로파일 없이 통과됨"
        except ValueError:
            msgs.append("프로파일/--i-max 요구")
        return True, "; ".join(msgs)
    check("전류 상한 / 프로파일 요구", limits)

    def unknown():
        os.environ["LABLIVE_SIM_LED"] = "bare"
        try:
            rc = civ.main(H + ["--unknown", "--ceiling", "0.2", "--p-max", "0.5", "--dwell", "0.05", "--navg", "4",
                               "--save-profile", "u1"])
        finally:
            os.environ.pop("LABLIVE_SIM_LED", None)
        d, m = _latest(home, "*_iv_unknown")
        c = m["summary"]["unknown"]
        p = Profile.load("u1")
        ok = (rc == 0 and m["k_off_ok"] and "efficiency" in (c.get("stop_reason") or "")
              and 0 < p["i_max_A"] < 0.2 and c["I_top_mA"] < 200)
        return bool(ok), (f"정지 {c['I_top_mA']:.3g} mA ({c.get('stop_reason')}), 제안 i_max {p['i_max_A'] * 1e3:.3g} mA, "
                          f"n {c.get('n_ideality', 0):.2f}")
    check("미상 LED 탐색 (--unknown, bare LED 모델)", unknown)

    def gui_smoke():
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
        except Exception as e:  # noqa: BLE001
            return None, f"창을 만들 수 없음 ({type(e).__name__}) — 화면 없는 환경이면 정상"
        from .. import gui
        saved = {n: getattr(messagebox, n) for n in ("showinfo", "showerror", "showwarning", "askyesno")}
        msgs = []
        for n in saved:
            setattr(messagebox, n, lambda *x, _n=n, **k: msgs.append((_n, x[:2])) or True)
        try:
            app = gui.LiveApp(root, cfg={"profile": "t", "current_mA": 100, "i_max_mA": 250}, sim=True,
                              log=lambda m: None)

            def pump(cond, timeout):
                t0 = time.time()
                while not cond() and time.time() - t0 < timeout:
                    root.update()
                    time.sleep(0.05)
                return cond()
            dv = gui.DevicesWindow(app)
            dv.scan()
            pump(lambda: "찾음" in str(dv.status.cget("text")), 20)
            kinds = {str(dv.tv.item(i)["values"][2]) for i in dv.tv.get_children()}
            dv.destroy()
            if not {"1936-R", "2400"} <= kinds:
                return False, f"포트 찾기 결과 {kinds}"
            app.start()
            if not pump(lambda: app.running and app.plot is not None, 15):
                return False, f"시작 안 됨 {msgs}"
            pump(lambda: False, 4)
            for W in (gui.SettingsWindow, gui.ColorsWindow, gui.PathsWindow, gui.CaptureWindow):
                W(app).destroy()
            dw = gui.DataWindow(app)
            app.apply_colors({"I_pd": "#000000"})
            app.cur_var.set("120")
            app.apply_current()
            ok_seg = pump(lambda: len(app.s.segments) == 1, 10)
            pump(lambda: False, 2)
            n_rows = dw.tabs["pd"].n if dw.tabs else 0
            app.stop()
            pump(lambda: not app.busy and not app.running, 15)
            s = app.s
            ok = (ok_seg and s.meta.get("k_off_ok") and n_rows > 20 and not sim.ST["out"]
                  and os.path.exists(os.path.join(s.run_dir, "final.png")))
            return bool(ok), f"포트 찾기 OK, 전류 변경 {len(s.segments)}회, 데이터 표 {n_rows}행, LED OFF 확인"
        finally:
            for n, f in saved.items():
                setattr(messagebox, n, f)
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass
    check("측정 창 (led-gui: 포트 찾기/시작/전류 변경/표/색/정지)", gui_smoke)

    def pulse_mode():
        """셔터 펄스 흉내(폭 +12 ms) → 펄스 모드 실시간 분석 + data store 캡처 + led-pulses 재분석."""
        from ..cli import pulses as cpulses
        sim.SHUTTER["plan"] = sim.shutter_plan("100,200:4:800:2")
        try:
            pm, k, s = session(current=0.1, compliance=12.0, profile=Profile.load("t"), pd_rate=50,
                               mode="pulse", pulse_widths_ms=[100, 200])
            try:
                s.start()
                time.sleep(9.0)
                cap = s.ds_capture(2.0, 10)
                time.sleep(1.0)
            finally:
                s.stop()
                pm.close()
                k.close()
        finally:
            sim.SHUTTER["plan"] = None
        g = (s.meta.get("pulse_summary") or {}).get("groups") or []
        we = (s.meta["pulse_summary"]["info"].get("width_error_ms") or {}) if g else {}
        files = all(os.path.exists(os.path.join(s.run_dir, f)) for f in ("pulses.csv", "pulses_groups.csv", "pulses.png"))
        rc = cpulses.main([s.run_dir, "--widths", "100,200", "--home", home])
        ok = (files and rc == 0 and len(g) == 2 and sum(x["n_ok"] for x in g) >= 5
              and abs(we.get("mean", 99) - 12) < 4 and cap["n"] == 2000 and "pd_timing" in s.meta
              and s.meta["pm_filter"]["auto"] and not s.tracker._fit)
        return ok, (f"정상 펄스 {sum(x['n_ok'] for x in g)}개, 폭 오차 {we.get('mean', float('nan')):+.1f} ms "
                    f"(sim +12), 캡처 {cap['n']}점, 피팅 안 함")
    check("펄스 모드 + 고속 캡처 + led-pulses", pulse_mode)

    def hist():
        cprof.main(["--home", home, "--sim", "history", "t"])
        return os.path.exists(os.path.join(home, "profiles", "t_history.png")), "t_history.png"
    check("led-profile history", hist)

    print("=" * 72)
    for tag, name, note in RESULTS:
        print(f"[{tag}] {name:38s} {note}")
    n_fail = sum(t == "FAIL" for t, _, _ in RESULTS)
    print("=" * 72)
    print("모두 통과" if not n_fail else f"실패 {n_fail}개 — 위 출력과 함께 공유해 주세요")
    if a.keep:
        print(f"결과 폴더: {home}")
    else:
        shutil.rmtree(home, ignore_errors=True)
    return 1 if n_fail else 0


def entry():
    raise SystemExit(main())


if __name__ == "__main__":
    entry()
