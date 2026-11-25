"""led-gui — 측정 창 (설정 → 시작 → 실시간 판정/전류 조정 → 정지 → 요약)

  led-gui                                  마지막에 쓴 설정 없이 기본값으로
  led-gui --profile uvc275 --current 0.1   미리 채워서
  led-gui --sim                            시뮬레이터
"""
from .. import config
from . import common


def build_parser():
    ap = common.parser("led-gui", "LED 측정 창")
    ap.add_argument("--profile", help="미리 고를 프로파일")
    ap.add_argument("--current", type=float, help="미리 채울 전류 (A)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if not a.home and not a.sim:
        config.first_run()
    common.setup(a)
    from ..gui import DEFAULTS, run_app
    from ..profiles import Profile
    cfg = dict(DEFAULTS)
    last = config.load_user().get("last") or {}
    cfg.update({k: v for k, v in last.items() if k in DEFAULTS})
    if a.profile:
        p = Profile.load(a.profile)
        cfg.update(profile=p.name, i_max_mA=(p.get("i_max_A") or 0) * 1e3 or None,
                   compliance=p.get("compliance_V") or 12.0, vf_guard=p.get("vf_guard_V"), lambda_nm=p.get("lambda_nm"))
    if cfg.get("profile") and cfg["profile"] not in Profile.names():
        cfg["profile"] = ""
    if a.current:
        cfg["current_mA"] = a.current * 1e3
    app = run_app(cfg=cfg, sim=a.sim, pm_port=a.pm_port, k_port=a.k_port)
    if not a.sim:
        try:
            config.save_user(last={k: app.cfg[k] for k in DEFAULTS if k != "duration"})
        except OSError:
            pass
    return 0


def entry():
    raise SystemExit(common.run(main, "gui"))


if __name__ == "__main__":
    entry()
