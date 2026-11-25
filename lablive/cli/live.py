"""led-live — LED 정전류 구동(전압 상한) + 광전류 실시간 표시 + 안정 판정

  led-live --profile uvc275 --current 0.1
  led-live --profile uvc275 --current 0.1 --tol 0.005 --note "고정대 v2"
  led-live --profile uvc275 --current 0.1 --no-plot --duration 600
  led-live --no-profile --i-max 0.05 --current 0.02          (프로파일 없이, 상한 필수)
  led-live --sim --profile demo --current 0.1                (시뮬레이터)
  led-live --profile blue --current 0.1 --pulse --range 6 --pd-rate 50 --pulse-widths 100,140,200,300,400,500,700,1000
                                                             (셔터 캘리브레이션: 피팅 없이 펄스 폭·도달률)
  led-live ... --pulse --capture 100 --capture-ms 1          (켠 직후 1936-R 내부 data store로 100 s, 1 ms 간격 캡처)
  (창: 도구 막대에서 LED 설정, 실시간 전류 변경, 색, 데이터 표.  설정부터 GUI로 하려면 led-gui)

창 닫기 / q / Ctrl+C → LED OFF 확인, 1936-R 설정 원복
결과: <home>/runs/<시각>_live_<프로파일>_<mA>mA/  pd.csv k.csv meta.json final.png console.log summary.txt
"""
import json
import os
import time

from .. import LedSession, fmt_t, status_line
from . import common


def build_parser():
    ap = common.parser("led-live", "LED 정전류 구동 + 광전류 실시간 안정도 모니터")
    common.add_led_args(ap)
    ap.add_argument("--current", type=float, required=True, help="LED 전류 (A)")
    ap.add_argument("--note", default="", help="회차 메모 (고정대/거리/정렬 변경 등)")
    ap.add_argument("--tol", type=float, default=0.01, help="안정 판정 밴드 (비율, 0.01 = ±1%%)")
    ap.add_argument("--window", type=float, default=None, help="단기 안정 판정 창 (s, 기본 60)")
    ap.add_argument("--margin", type=float, default=0.5,
                    help="SETTLED = 예측 최종값 대비 |편차| ≤ margin×tol (기본 0.5)")
    ap.add_argument("--range", type=int, default=None, help="1936-R range 고정 (기본: 켠 직후 자동)")
    ap.add_argument("--vf-guard", type=float, default=None,
                    help="단자 전압이 이 값 아래로 가면 경고 (기본: 프로파일 값, 없으면 끔). 0 = 끔")
    ap.add_argument("--pd-rate", type=float, default=20.0, help="1936-R 읽기 (Hz)")
    ap.add_argument("--pd-filter", default="auto",
                    help="1936-R 디지털 필터 점 수 (10 kHz 기준, 1–10000). auto = 판독 간격에 맞춤 (20 Hz → 500 = 50 ms)")
    ap.add_argument("--k-rate", type=float, default=2.0, help="2400 읽기 (Hz)")
    ap.add_argument("--fit-every", type=float, default=None, help="피팅 주기 (s, 기본 15)")
    ap.add_argument("--fit-min", type=float, default=None, help="첫 피팅까지 최소 데이터 (s, 기본 20)")
    ap.add_argument("--duration", type=float, default=0, help="자동 종료 (s), 0 = 수동")
    ap.add_argument("--no-plot", action="store_true", help="그래프 없이 콘솔 출력만")
    ap.add_argument("--rsense", action="store_true", help="2400 4-wire")
    ap.add_argument("--no-trip-stop", action="store_true", help="compliance trip에도 LED 유지 (비권장)")
    ap.add_argument("--pulse", action="store_true",
                    help="펄스 모드: 셔터 등으로 켜졌다 꺼졌다 하는 빛. 안정도 피팅 없이 펄스 폭·도달률 (pulses.csv)")
    ap.add_argument("--pulse-widths", default=None,
                    help="공칭 펄스 폭 목록 ms (예: 100,140,200) — 그룹 이름표와 폭 오차 계산에 씀")
    ap.add_argument("--capture", type=float, default=0,
                    help="LED ON 후 1936-R data store로 이 길이(s)만큼 고속 캡처 → 덤프 (초당 약 270점, 폴링은 그동안 멈춤)")
    ap.add_argument("--capture-ms", type=float, default=1.0, help="캡처 간격 ms (0.1 단위, 기본 1)")
    ap.add_argument("--capture-after", type=float, default=0.0, help="LED ON 후 캡처 시작까지 (s)")
    ap.add_argument("--reset-check", action="store_true",
                    help="극성 점검 전압이 프로파일 기록과 달라도 진행하고 기록을 갱신 (같은 LED가 확실할 때)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    common.setup(a)
    led = common.resolve_led(a)
    prof = led["profile"]
    # 시뮬레이터는 열 시정수가 압축돼 있어 판정 창/피팅도 짧게
    window = a.window or (5.0 if a.sim else 60.0)
    fit_every = a.fit_every if a.fit_every is not None else (2.0 if a.sim else 15.0)
    fit_min = a.fit_min or (3.0 if a.sim else 20.0)
    guard = a.vf_guard if a.vf_guard is not None else led["vf_guard"]
    if prof:
        prior, how = prof.model_for(a.current)
        print(f"  기준 모델: {prior['how'] if prior else how}")

    pm, k = common.open_instruments(a)
    holder = {}
    log = common.Tee(lambda: holder.get("dir"))
    s = LedSession(pm, k, a.current, led["compliance"], i_max=led["i_max"], lambda_nm=led["lambda_nm"],
                   pm_range=a.range, tol=a.tol, window_s=window, vf_guard=guard or None,
                   pd_rate=a.pd_rate, k_rate=a.k_rate, fit_every_s=fit_every, fit_min_s=fit_min,
                   digital_filter=None if str(a.pd_filter).lower() == "auto" else int(a.pd_filter),
                   stop_on_trip=not a.no_trip_stop, rsense=a.rsense, profile=prof, note=a.note,
                   settle_margin=a.margin, log=log, reset_check=a.reset_check,
                   mode="pulse" if a.pulse else "settle", pulse_widths_ms=parse_widths(a.pulse_widths))
    holder["dir"] = s.run_dir
    plot = None
    try:
        s.start()
        if a.capture:
            start_capture_thread(s, a, log)
        if a.no_plot:
            while True:
                st = s.status()
                log(status_line(st))
                if st["state"] == "fault" or (a.duration and s.clock() >= a.duration):
                    break
                time.sleep(1.0)
        else:
            try:
                from ..gui import run_app
            except ImportError:          # tkinter 없음 → matplotlib 창만
                from ..liveplot import LivePlot
                plot = LivePlot(s, duration_s=a.duration or None)
                plot.run()
            else:
                run_app(cfg={"duration": a.duration}, sim=a.sim, session=s, log=log)   # 닫을 때 정지 + final.png
    except KeyboardInterrupt:
        log("중단 (Ctrl+C)")
    finally:
        s.stop()
        if plot is not None:
            try:
                plot.save(os.path.join(s.run_dir, "final.png"))
            except Exception as e:  # noqa: BLE001
                log(f"final.png 저장 실패: {e}")
        pm.close()
        k.close()
    report(s, a, prof, log)
    return 1 if s.fault else 0


def parse_widths(txt):
    if not txt:
        return None
    try:
        w = [float(x) for x in str(txt).replace(" ", "").split(",") if x]
    except ValueError:
        raise SystemExit(f"--pulse-widths: 숫자 목록(ms)이 아님: {txt}") from None
    if not w or min(w) <= 0:
        raise SystemExit("--pulse-widths: 양수 ms 목록")
    return w


def start_capture_thread(s, a, log):
    import threading
    iv = int(round(a.capture_ms * 10))
    n = int(round(a.capture * 10000 / max(iv, 1)))
    log(f"data store 캡처 예약: {a.capture_after:g} s 후 {a.capture:g} s, {iv / 10:g} ms 간격 = {n}점 "
        f"(덤프 예상 {n / 270 / 60:.1f}분)")

    def work():
        if a.capture_after and s._halt.wait(a.capture_after):
            return
        last = [0.0]

        def prog(phase, done, total):
            if time.perf_counter() - last[0] > 10 or done >= total:
                last[0] = time.perf_counter()
                log(f"  {'캡처' if phase == 'capture' else '덤프'} {done}/{total}")
        try:
            s.ds_capture(a.capture, iv, progress=prog)
        except Exception as e:  # noqa: BLE001
            log(f"캡처 실패: {type(e).__name__}: {e}")
    threading.Thread(target=work, name="ds-capture", daemon=True).start()


def report(s, a, prof, log=print):
    m = s.meta
    fs = m.get("final_status") or {}
    L = ["", "=== 요약 ===",
         f"켜져 있던 시간      {fmt_t(m.get('duration_s'))}",
         f"처음 SETTLED        {fmt_t(m.get('t_first_settled'))}"]
    if fs.get("fit"):
        f = fs["fit"]
        L.append(f"예측 최종값         {f['y_inf'] * 1e6:.4f} µA  (±{a.tol * 100:g}% 도달 {fmt_t(f['t_settle'])}, "
                 f"{'reliable' if f['reliable'] else '외삽'})")
        L.append("τ (s) / 진폭 (%)    " + ", ".join(f"{t:.3g}/{x:+.2g}" for t, x in zip(f["taus"], f["amps_pct"])))
    sg = m.get("segments") or []
    if sg:
        L.append(f"전류 변경 {len(sg)}회   {sg[0]['from_A'] * 1e3:g} → " + " → ".join(f"{x['to_A'] * 1e3:g}" for x in sg)
                 + f" mA (첫 변경 {fmt_t(sg[0]['t_s'])})")
        L.append("                    위 최종값/τ는 마지막 구간 기준, 처음 SETTLED와 회차 비교(history)는 첫 구간 기준")
    if m.get("fault"):
        L.append(f"FAULT               {m['fault']}")
    pt = m.get("pd_timing") or {}
    if pt.get("median_ms"):
        L.append(f"1936-R 판독          설정 {pt['set_hz']:g} Hz → 실제 {pt['achieved_hz']:.1f} Hz "
                 f"(간격 중앙 {pt['median_ms']:.1f} / p95 {pt['p95_ms']:.1f} / 최대 {pt['max_ms']:.0f} ms, "
                 f"100 ms 넘는 공백 {pt['over_100ms']}개)")
    if m.get("pulse_summary"):
        from ..pulses import summary_lines
        L += [x for x in summary_lines({"info": m["pulse_summary"]["info"], "groups": m["pulse_summary"]["groups"],
                                        "pulses": []}, "pd.csv") if not x.lstrip().startswith("제외")]
        L.append("                    → pulses.csv, pulses_groups.csv, pulses.png")
    for c in m.get("ds_captures") or []:
        L.append(f"고속 캡처           {c['csv']}: {c['n']}점, {c['interval_ms']:g} ms 간격 "
                 f"(덤프 {c['dump_s']:.0f} s) → {c['csv'][:-4]}_pulses.*")
    L.append(f"결과                {s.run_dir}")
    if prof and s.mode == "settle":
        pf = m.get("prior_fit")
        if pf:
            L.append(f"프로파일 모양 일치   rms {pf['rms_pct']:.3f}% (한계 {pf['rms_limit_pct']:.2f}%) "
                     f"{'MISMATCH' if pf['mismatch'] else 'OK'}"
                     + (f" — {pf['reason']}" if pf.get("reason") and pf["reason"] != "shape" else ""))
        if s.t_on:
            L.append(f"회차 비교:           led-profile history {prof.name}")
            L.append(f"기준 모델로 등록:    led-profile add {prof.name} \"{s.run_dir}\"")
    if m.get("pm_state_after") and m.get("pm_state_before"):
        b, af = m["pm_state_before"], m["pm_state_after"]
        skip = {"PM:RANge?"} if b.get("PM:AUTO?", "").startswith("1") else set()   # auto-range면 range는 움직임
        same = all(af.get(x) == b[x] for x in b if x not in skip)
        L.append(f"1936-R 설정 원복     {'OK' if same else '차이 있음 — meta.json 확인'}")
    for x in L:
        log(x)
    if os.path.isdir(s.run_dir):
        with open(os.path.join(s.run_dir, "summary.txt"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"t_first_settled": m.get("t_first_settled"), "fault": m.get("fault"),
                                "fit": fs.get("fit")}, ensure_ascii=False, indent=2, default=str))


def entry():
    raise SystemExit(common.run(main, "live"))


if __name__ == "__main__":
    entry()
