"""led-iv — I-V-L 스윕 (새 LED 특성 파악: 문턱, 발진 구간, 효율, 동저항)

  led-iv --profile newled                               정전류 0.1 mA → i_max, log 25점 (기본, 안전)
  led-iv --profile newled --start 1e-3 --stop 0.05 --points 30
  led-iv --profile newled --lin --start 0.01 --stop 0.1 --step 0.005
  led-iv --profile newled --back                        내려오는 스윕도 (히스테리시스)
  led-iv --profile newled --mode voltage --vstop 8 --vstep 0.05 --ilim 0.02
                                                        정전압 (모듈 내장 드라이버/UVLO 판별용, ilim ≤ i_max)
  led-iv --unknown                                      정보 없는 LED: 10 µA부터 올리며 과구동 징후에서 정지
  led-iv --unknown --ceiling 0.05 --p-max 0.3 --save-profile blue2
                                                        (상한/전력 한계 조정, 결과로 프로파일 생성)
결과: <home>/runs/<시각>_iv_<프로파일>_<mode>/  iv.csv iv.png meta.json console.log
"""
import math
import os
from datetime import datetime

from .. import config
from ..ivsweep import NOISY_PCT, lin_levels, log_levels, run_iv
from ..profiles import Profile
from . import common


def build_parser():
    ap = common.parser("led-iv", "LED I-V-L 스윕")
    common.add_led_args(ap)
    ap.add_argument("--mode", choices=("current", "voltage"), default="current")
    ap.add_argument("--start", type=float, default=1e-4, help="current: 시작 전류 (A, 기본 0.1 mA)")
    ap.add_argument("--stop", type=float, help="current: 끝 전류 (A, 기본 i_max)")
    ap.add_argument("--points", type=int, default=25, help="current log 스윕 점 수")
    ap.add_argument("--lin", action="store_true", help="current: 선형 간격 (--step)")
    ap.add_argument("--step", type=float, help="current --lin 간격 (A)")
    ap.add_argument("--vstart", type=float, default=0.0)
    ap.add_argument("--vstop", type=float, help="voltage: 끝 전압 (V, 필수)")
    ap.add_argument("--vstep", type=float, default=0.05)
    ap.add_argument("--ilim", type=float, help="voltage: 전류 제한 (A, 기본 i_max)")
    ap.add_argument("--dwell", type=float, default=0.3, help="점당 대기 (s)")
    ap.add_argument("--navg", type=int, default=8, help="점당 1936-R 판독 수")
    ap.add_argument("--back", action="store_true", help="내려오는 스윕 추가")
    ap.add_argument("--note", default="")
    ap.add_argument("--rsense", action="store_true")
    u = ap.add_argument_group("미상 LED (--unknown): 프로파일/정격 없이 안전 탐색")
    u.add_argument("--unknown", action="store_true",
                   help="10 µA부터 log 6점/decade로 올리며 전력·급가열·효율 저하·roll-over에서 정지")
    u.add_argument("--ceiling", type=float, default=0.02, help="탐색 전류 상한 (A, 기본 20 mA — 소형 LED 통상 정격)")
    u.add_argument("--p-max", type=float, default=0.15, help="전력 상한 V·I (W, 기본 0.15 — 방열 미상 가정)")
    u.add_argument("--save-profile", metavar="NAME", help="탐색 결과(제안 i_max, compliance)로 프로파일 생성")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    common.setup(a)
    if a.unknown:
        return main_unknown(a)
    led = common.resolve_led(a)
    prof = led["profile"]
    if a.mode == "current":
        stop = a.stop or led["i_max"]
        if stop > led["i_max"]:
            common.fail(f"--stop {stop:g} A > i_max {led['i_max']:g} A")
        if a.lin:
            if not a.step:
                common.fail("--lin 이면 --step 필요")
            levels = [x for x in lin_levels(a.start, stop, a.step) if x > 0]
        else:
            levels = log_levels(a.start, stop, a.points)
        print(f"정전류 스윕 {levels[0] * 1e3:g} → {levels[-1] * 1e3:g} mA, {len(levels)}점, 상한 {led['compliance']:g} V, "
              f"점당 {a.dwell:g} s")
    else:
        if a.vstop is None:
            common.fail("--mode voltage 이면 --vstop 필요")
        levels = lin_levels(a.vstart, a.vstop, a.vstep)
        print(f"정전압 스윕 {levels[0]:g} → {levels[-1]:g} V, {len(levels)}점, 전류 제한 "
              f"{(a.ilim or led['i_max']) * 1e3:g} mA")
    pm, k = common.open_instruments(a)
    holder = {}
    log = common.Tee(lambda: holder.get("dir"))
    tag = f"_{prof.name}" if prof else ""
    holder["dir"] = os.path.join(config.runs_dir(), datetime.now().strftime("%Y%m%d_%H%M%S") + f"_iv{tag}_{a.mode}")
    try:
        run_dir, s = run_iv(pm, k, mode=a.mode, levels=levels, compliance=led["compliance"], i_max=led["i_max"],
                            ilim=a.ilim, lambda_nm=led["lambda_nm"], dwell=a.dwell, n_avg=a.navg, back=a.back,
                            profile=prof, note=a.note, run_dir=holder["dir"], log=log, rsense=a.rsense)
    finally:
        pm.close()
        k.close()
    report(s, prof, run_dir, log)
    return 0


def main_unknown(a):
    if a.profile or a.mode != "current":
        common.fail("--unknown은 프로파일 없이, 정전류 모드로만 (결과로 --save-profile)")
    if a.save_profile and a.save_profile in Profile.names():
        common.fail(f"프로파일 {a.save_profile} 이미 있음")
    if not 1e-4 <= a.ceiling <= 1.0:
        common.fail("--ceiling은 0.1 mA ~ 1 A")
    compliance = a.compliance if a.compliance is not None else 12.0
    start = min(1e-5, a.ceiling)
    levels = log_levels(start, a.ceiling, max(2, round(6 * math.log10(a.ceiling / start)) + 1))
    print(f"미상 LED 탐색: {start * 1e6:g} µA → 최대 {a.ceiling * 1e3:g} mA ({len(levels)}점), 전압 상한 {compliance:g} V, "
          f"전력 상한 {a.p_max * 1e3:g} mW, 점당 {a.dwell:g} s")
    print("  정지 조건: 전력 초과 예상 / dwell 중 V 급강하(급가열) / 효율 < 최고치 75% / 광출력 감소 / 전압 상한")
    pm, k = common.open_instruments(a)
    holder = {"dir": os.path.join(config.runs_dir(), datetime.now().strftime("%Y%m%d_%H%M%S") + "_iv_unknown")}
    log = common.Tee(lambda: holder.get("dir"))
    try:
        run_dir, s = run_iv(pm, k, mode="current", levels=levels, compliance=compliance, i_max=a.ceiling,
                            lambda_nm=a.lambda_nm, dwell=a.dwell, n_avg=a.navg, note=a.note or "unknown LED probe",
                            run_dir=holder["dir"], log=log, rsense=a.rsense, unknown={"p_max": a.p_max})
    finally:
        pm.close()
        k.close()
    report(s, None, run_dir, log)
    report_unknown(s.get("unknown") or {}, run_dir, a, log)
    return 0


def report_unknown(c, run_dir, a, log=print):
    log("")
    log("=== 미상 LED 특성 (참고값 — 데이터시트가 있으면 그쪽 우선) ===")
    if "V_top" not in c:
        log("유효한 점 없음 — 첫 점부터 전압 상한: 극성 반대, 개방, 또는 Vf가 상한보다 높은 소자(직렬 칩/모듈)")
        return
    log(f"정지 사유           {c.get('stop_reason') or ('상한 ' + format(a.ceiling * 1e3, 'g') + ' mA 도달')}")
    if not c.get("light_detected"):
        log("빛 검출 안 됨       정렬/검출기 위치, 검출기 감도 밖 파장, 또는 발광 안 하는 소자 — 확인 후 재시도")
    else:
        log(f"발광 시작           {c['light_onset_mA']:.3g} mA / {c['light_onset_V']:.3f} V 에서 검출")
        log(f"효율 최고           {c['peak_eff_I_mA']:.3g} mA, 마지막 점은 최고치의 {100 * c['eff_top_rel']:.0f}%")
    for key, lab in (("V_at_1mA", "V @ 1 mA"), ("V_at_10mA", "V @ 10 mA")):
        if c.get(key):
            log(f"{lab:20s}{c[key]:.3f} V")
    if c.get("n_ideality") is not None:
        log(f"V = a + n·kT/q·ln I + Rs·I   n = {c['n_ideality']:.2f}, Rs = {c['R_s_ohm']:.2f} Ω  (rms {c['fit_rms_mV']:.0f} mV)"
            + ("  — n이 매우 큼: 직렬 칩/보호소자/모듈 가능" if c["n_ideality"] > 8 else
               "  — ln I 의존 거의 없음: 단일 다이오드 아님 (문턱 소자/드라이버 내장 모듈?)" if c["n_ideality"] < 1 else ""))
    log(f"Vf 기준 계열 추정   {c['family_hint']}  (Vf만으로는 부정확 — 파장은 분광으로 확인)")
    if c.get("noisy_I_mA"):
        log("광출력 불안정 점    " + ", ".join(f"{x:.3g}" for x in c["noisy_I_mA"]) + " mA  (발진 구간 의심, iv.png (d))")
    sim = c.get("suggest_i_max_A")
    if c.get("light_detected") and c.get("n_stable_lit", 0) < 3:
        log(f"주의: 안정 발광 점이 {c.get('n_stable_lit', 0)}개뿐 — 효율 저하(과구동) 감시가 거의 작동하지 않은 상태. "
            f"제안값은 근거가 약함")
    if sim:
        more = (c.get("ceiling_reached") and c.get("eff_top_rel", 0) >= 0.9 and not c.get("stop_reason")
                and c.get("n_stable_lit", 0) >= 3)
        log(f"제안 i_max          {sim * 1e3:.3g} mA  (효율 ≥ 최고치 85%, 전력 ≤ {a.p_max * 1e3:g} mW인 최대 시험 전류 — 보수적)"
            + ("\n                    탐색 상한에서도 효율이 유지됨 → 더 올릴 여지 있음: --ceiling을 2배씩" if more else ""))
    log(f"제안 compliance     {c['suggest_compliance_V']:g} V  (관측 최대 V + 1.5 V)")
    if a.save_profile and sim:
        p = Profile.create(a.save_profile, description=f"unknown LED probe {os.path.basename(run_dir)}",
                           lambda_nm=a.lambda_nm, i_max_A=round(sim, 6), compliance_V=c["suggest_compliance_V"])
        p.register_iv(run_dir)
        log(f"프로파일 생성 → {p.path}")
        log(f"다음: led-iv --profile {a.save_profile}   (발진 구간 확인) → led-live --profile {a.save_profile} --current <A>")
    elif sim:
        log(f"프로파일로: led-profile new <이름> --lambda <nm> --i-max {sim:.4g} --compliance {c['suggest_compliance_V']:g}")


def report(s, prof, run_dir, log=print):
    log("")
    log("=== 요약 ===")
    if not s.get("V_max"):
        log("유효한 점 없음")
        return
    log(f"최대 {s['I_max_mA']:.4g} mA @ {s['V_max']:.3f} V" + ("  (compliance 도달)" if s["any_trip"] else ""))
    if s.get("R_dyn_top_ohm") is not None:
        log(f"상단 동저항 dV/dI   {s['R_dyn_top_ohm']:.2f} Ω")
    if s.get("peak_eff_I_mA"):
        log(f"상대 효율 최대      {s['peak_eff_I_mA']:.3g} mA  (최대 전류에서 {100 * s['eff_at_top_rel']:.0f}% — droop/발열)")
    if s.get("I_spread_top_pct") is not None:
        log(f"상단 전류 평탄도    {s['I_spread_top_pct']:.1f}%  (작으면 내부 정전류 드라이버 의심)")
    nz = s.get("noisy_points") or []
    if nz:
        log(f"광출력 불안정 (점 내 표준편차 > {NOISY_PCT:g}%) {len(nz)}점: "
            + ", ".join(f"{p['I_mA']:.3g} mA/{p['V']:.3f} V/{p['noise_pct']:.0f}%" for p in nz[:8])
            + (" ..." if len(nz) > 8 else ""))
        if s.get("stable_from_mA"):
            log(f"  → {s['stable_from_mA']:.3g} mA 이상에서 안정. 정전류 구동은 그 위에서, "
                f"구동 중 단자 V가 내려가 발진 구간에 들어가는지 감시 권장")
            if prof:
                log(f"  경고 전압 제안: led-profile set {prof.name} --vf-guard {s['suggest_vf_guard_V']:g}"
                    f"   (발진 최고 V {s['noisy_V_max']:.3f} / 안정 최저 V {s['stable_V_min']:.3f} 의 중간;"
                    f" 실제 구동 중 V는 발열로 더 내려감)")
    else:
        log(f"광출력 불안정 구간 없음 (모든 점 표준편차 ≤ {NOISY_PCT:g}%)")
    log(f"결과 {run_dir}  (iv.png)")
    if prof:
        log(f"다음: led-live --profile {prof.name} --current <A>   (냉간 시작, 충분히 길게 → led-profile add)")


def entry():
    raise SystemExit(common.run(main, "iv"))


if __name__ == "__main__":
    entry()

