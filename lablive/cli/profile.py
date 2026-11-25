"""led-profile — 광원 프로파일 관리 (장비 연결 불필요)

  led-profile new uvc275 --lambda 275 --i-max 0.25 --vf-guard 6.30 --desc "275 nm 모듈, 고정대 v1"
  led-profile set uvc275 --vf-guard 6.30          (값 수정; 0이면 끔)   --clear-check (극성 점검 기준 지우기)
  led-profile add uvc275 <회차폴더>                 이 회차를 그 전류의 기준 모델로 (회차도 등록)
  led-profile link uvc275 <회차폴더>                기준 모델은 그대로, 비교용 회차로만 등록
  led-profile show uvc275
  led-profile list
  led-profile history uvc275 [--current 0.1] [--tol 0.01]   → <home>/profiles/uvc275_history.png/.csv

회차 폴더는 이름만 줘도 됨 (<home>/runs/ 아래에서 찾음). led-live(live)와 led_settle(settle) 형식 모두 가능.
기준 모델로는 측정 길이가 가장 긴 τ의 3배 이상인(reliable) 냉간 시작 회차를 고르세요.
"""
import os

from .. import config, fmt_t
from ..history import collect, plot, write_table
from ..profiles import Profile
from ..runfit import model_entry, summarize
from . import common


def _model_line(m):
    ts = m.get("t_settle_s") or {}
    return (f"{m['current_A'] * 1e3:6g} mA  τ " + " / ".join(f"{x:.3g}" for x in m["taus_s"])
            + "  a% " + " / ".join(f"{x:+.2g}" for x in m["amps_pct"])
            + f"  ±1% {fmt_t(ts.get('1%'))}  I_final {m['I_final_A'] * 1e6:.3f} µA"
            + f"  V {m.get('V_start') or 0:.3f}→{m.get('V_final') or 0:.3f}"
            + ("" if m.get("reliable", True) else "  [외삽 모델]") + f"  ({m.get('run')})")


def _gap(r):
    g = r.get("gap_prev_min")
    return "-" if g is None else f"{g:.0f}m"


def _run_path(d):
    if os.path.isdir(d):
        return os.path.normpath(d)
    alt = os.path.join(config.runs_dir(), d)
    if os.path.isdir(alt):
        return os.path.normpath(alt)
    raise FileNotFoundError(f"회차 폴더 없음: {d} (또는 {alt})")


def cmd_new(a):
    if a.i_max <= 0 or a.i_max > 1.0:
        common.fail("--i-max는 0 < I ≤ 1 A (2400 1 A range 한계)")
    p = Profile.create(a.name, description=a.desc or "", lambda_nm=a.lam, i_max_A=a.i_max,
                       compliance_V=a.compliance, vf_guard_V=a.vf_guard or None)
    print(f"생성: {p.path}")
    print(f"  I ≤ {a.i_max * 1e3:g} mA, 상한 {a.compliance:g} V, λ {a.lam} nm, 경고 전압 {a.vf_guard or '-'}")
    print(f"다음: led-iv --profile {a.name}   (I-V 스윕으로 특성 확인)")


def cmd_set(a):
    p = Profile.load(a.name)
    ch = {}
    if a.i_max is not None:
        ch["i_max_A"] = a.i_max
    if a.compliance is not None:
        ch["compliance_V"] = a.compliance
    if a.vf_guard is not None:
        ch["vf_guard_V"] = a.vf_guard or None
    if a.lam is not None:
        ch["lambda_nm"] = a.lam
    if a.desc is not None:
        ch["description"] = a.desc
    if a.clear_check:
        ch["check"] = None
    if not ch:
        common.fail("바꿀 값 없음 (--i-max/--compliance/--vf-guard/--lambda/--desc/--clear-check)")
    for kk, v in ch.items():
        print(f"  {kk}: {p.get(kk)} → {v}")
    p.update(**ch)


def cmd_add(a, as_model=True):
    p = Profile.load(a.name)
    for d in a.runs:
        d = _run_path(d)
        s = summarize(d, use_cache=not a.refit)
        rel = p.register_run(d)
        if not as_model:
            print(f"등록 (비교용): {rel}")
            continue
        e = model_entry(s)
        e["run"] = rel
        same = [m for m in p["models"] if abs(m["current_A"] - e["current_A"]) <= 0.02 * e["current_A"]]
        for m in same:
            if m.get("V_start") and e.get("V_start") and e["V_start"] < m["V_start"] - 0.01:
                print(f"  주의: 이 회차 시작 V {e['V_start']:.3f} < 기존 기준 {m['V_start']:.3f} V — "
                      f"더 데워진 상태에서 시작한 회차일 수 있음 (기준은 가장 차가운 시작이 맞음)")
        if not e["reliable"]:
            print(f"  주의: {rel} — 가장 긴 τ가 측정 길이의 1/3보다 김. 이 모델의 최종값/도달시각은 외삽입니다.")
        replaced = p.set_model(e)
        print(f"{'교체' if replaced else '추가'}: {_model_line(e)}")


def cmd_show(a):
    p = Profile.load(a.name)
    d = p.data
    print(f"{d['name']}  ({p.path})  {d.get('description', '')}")
    print(f"  λ {d.get('lambda_nm')} nm | I ≤ {(d.get('i_max_A') or 0) * 1e3:g} mA | 상한 {d.get('compliance_V')} V | "
          f"경고 전압 {d.get('vf_guard_V')}")
    c = d.get("check")
    print("  극성 점검 기준: " + (f"{c['V']:.3f} V @ {c['I_A'] * 1e3:g} mA ({c.get('recorded', '')})" if c else "없음"))
    print("  기준 모델:" if d["models"] else "  기준 모델: 없음 (add로 등록)")
    for m in d["models"]:
        print("   ", _model_line(m))
    print(f"  I-V 스윕 {len(d.get('iv', []))}개")
    for r in d.get("iv", []):
        print(f"    {r['path']}")
    print(f"  회차 {len(d['runs'])}개")
    for r in d["runs"]:
        print(f"    {r['path']}")


def cmd_list(a):
    names = Profile.names()
    if not names:
        print(f"프로파일 없음 ({config.profiles_dir()})")
    for n in names:
        p = Profile.load(n)
        cur = ", ".join(f"{m['current_A'] * 1e3:g}" for m in p["models"]) or "-"
        print(f"{n:16s} I≤{(p.get('i_max_A') or 0) * 1e3:g} mA  모델 [{cur}] mA, 회차 {len(p['runs'])}  "
              f"{p.get('description', '')}")


def cmd_history(a):
    p = Profile.load(a.name)
    rows, bad = collect(p, a.current, tols=(a.tol, 0.005, 0.01))
    tk = f"{a.tol * 100:g}%"
    print(f"{p.name}: 회차 {len(rows)}개" + (f" ({a.current * 1e3:g} mA)" if a.current else ""))
    print(f"{'시작':16s} {'mA':>5s} {'길이':>7s} {'간격':>7s} {'I_final µA':>11s} {'±' + tk:>9s} "
          f"{'V시작':>7s} {'V끝':>7s}  τ (s)")
    for r in rows:
        ts = (r.get("t_settle_s") or {}).get(tk)
        ext = (r.get("t_settle_extrapolated") or {}).get(tk)
        print(f"{(r.get('started') or '')[:16]:16s} {(r.get('current_A') or 0) * 1e3:5g} "
              f"{fmt_t(r.get('duration_s')):>7s} "
              f"{_gap(r):>7s} "
              f"{(r.get('I_final_A') or float('nan')) * 1e6:11.3f} "
              f"{fmt_t(ts) + ('*' if ext else ''):>9s} {r.get('V_start', float('nan')):7.3f} "
              f"{r.get('V_final', float('nan')):7.3f}  " + " / ".join(f"{x:.3g}" for x in r.get("taus_s", [])))
    print("  * = 측정 길이 밖 외삽,  간격 = 앞 회차 종료→이번 시작 (냉각 시간)")
    for b in bad:
        print(f"  건너뜀: {b['dir']} — {b['error']}")
    if not rows:
        return
    base = os.path.join(config.profiles_dir(), f"{p.name}_history" + (f"_{a.current * 1e3:g}mA" if a.current else ""))
    write_table(rows, base + ".csv", tk)
    plot(rows, base + ".png", a.tol, title=f"{p.name} — {len(rows)} runs")
    print(f"→ {base}.png, {base}.csv")


def build_parser():
    ap = common.parser("led-profile", "광원 프로파일 관리", ports=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="새 LED 프로파일 (정격 확인 후)")
    n.add_argument("name")
    n.add_argument("--desc")
    n.add_argument("--lambda", dest="lam", type=int, required=True, help="파장 (nm)")
    n.add_argument("--i-max", type=float, required=True, help="전류 상한 (A) — LED 정격 이하")
    n.add_argument("--compliance", type=float, default=12.0, help="전압 상한 (V, 기본 12)")
    n.add_argument("--vf-guard", type=float, default=None, help="경고 전압 (V) — 보통 led-iv 결과를 보고 set으로")
    st = sub.add_parser("set", help="프로파일 값 수정")
    st.add_argument("name")
    st.add_argument("--desc")
    st.add_argument("--lambda", dest="lam", type=int)
    st.add_argument("--i-max", type=float)
    st.add_argument("--compliance", type=float)
    st.add_argument("--vf-guard", type=float, help="0 = 끔")
    st.add_argument("--clear-check", action="store_true", help="극성 점검 기준 지우기 (다음 측정값으로 다시 기록)")
    for c in ("add", "link"):
        x = sub.add_parser(c)
        x.add_argument("name")
        x.add_argument("runs", nargs="+")
        x.add_argument("--refit", action="store_true", help="캐시 무시하고 다시 피팅")
    s = sub.add_parser("show")
    s.add_argument("name")
    sub.add_parser("list")
    h = sub.add_parser("history")
    h.add_argument("name")
    h.add_argument("--current", type=float)
    h.add_argument("--tol", type=float, default=0.01)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    common.setup(a)
    {"new": cmd_new, "set": cmd_set, "add": cmd_add, "link": lambda x: cmd_add(x, as_model=False),
     "show": cmd_show, "list": cmd_list, "history": cmd_history}[a.cmd](a)
    return 0


def entry():
    raise SystemExit(common.run(main, "profile"))


if __name__ == "__main__":
    entry()
