"""led-pulses — 기록된 광전류에서 펄스(셔터 열림) 폭·도달률을 다시 계산 (장비 불필요)

  led-pulses <회차 폴더>                                   pd.csv 분석 → pulses.csv, pulses_groups.csv, pulses.png
  led-pulses <회차 폴더> --widths 100,140,200,300,400,500,700,1000   공칭 폭으로 그룹 이름표 + 폭 오차
  led-pulses <회차 폴더>\\ds_201530.csv                    data store 고속 캡처 파일
  led-pulses <폴더> --gap 2.5                              샘플 간격이 기준의 2.5배를 넘는 펄스는 제외 (기본)

방법은 lablive/pulses.py 머리말: 50% 통과 시각(경사 위 샘플 보간), 다 열림 수준의 시간 변화 보정, 공백 낀 펄스 제외.
b6 이하에서 펄스 모드 없이 잰 회차(예: 11/25 셔터 캘리브레이션)도 그대로 분석된다.
"""
import csv
import json
import math
import os

import numpy as np

from .. import pulses
from . import common


def build_parser():
    ap = common.parser("led-pulses", "기록된 광전류에서 펄스 폭·도달률 계산", ports=False)
    ap.add_argument("path", help="회차 폴더 (pd.csv) 또는 CSV 파일 (첫 열 t_s, 둘째 열 광전류 A)")
    ap.add_argument("--widths", default=None, help="공칭 폭 목록 ms (예: 100,140,200)")
    ap.add_argument("--filter-ms", type=float, default=None,
                    help="1936-R 디지털 필터 길이 ms (기본: meta.json 값, 없으면 판독 간격)")
    ap.add_argument("--gap", type=float, default=2.5, help="제외 기준: 샘플 간격 > 기준 간격 × 이 값")
    ap.add_argument("--out", default=None, help="결과 폴더 (기본: 입력과 같은 폴더)")
    return ap


def load(path):
    if os.path.isdir(path):
        d, f = path, os.path.join(path, "pd.csv")
    else:
        d, f = os.path.dirname(os.path.abspath(path)), path
    if not os.path.isfile(f):
        raise SystemExit(f"파일 없음: {f}")
    t, y = [], []
    with open(f, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)
        for row in r:
            if len(row) < 2:
                continue
            try:
                tt = float(row[0])
            except ValueError:
                continue
            try:
                yy = float(row[1]) if row[1] != "" else math.nan
            except ValueError:
                yy = math.nan
            t.append(tt)
            y.append(yy)
    meta = {}
    mp = os.path.join(d, "meta.json")
    if os.path.isfile(mp):
        try:
            with open(mp, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            meta = {}
    return d, f, np.array(t), np.array(y), meta


def main(argv=None):
    a = build_parser().parse_args(argv)
    common.setup(a)
    d, f, t, y, meta = load(a.path)
    widths = None
    if a.widths:
        from .live import parse_widths
        widths = parse_widths(a.widths)
    elif (meta.get("args") or {}).get("pulse_widths_ms"):
        widths = meta["args"]["pulse_widths_ms"]
    t_off = meta.get("t_off")
    m = t >= 0
    if t_off is not None and os.path.basename(f) == "pd.csv":
        m &= t <= t_off
    t, y = t[m], y[m]
    base = os.path.splitext(os.path.basename(f))[0]
    is_ds = base.startswith("ds_")
    if a.filter_ms is not None:
        filt = a.filter_ms / 1e3
    elif not is_ds and (meta.get("pm_filter") or {}).get("ms"):
        filt = meta["pm_filter"]["ms"] / 1e3
    else:
        filt = 0.0
    dt_ref = None
    if is_ds:
        dd = np.diff(t)
        dt_ref = float(np.median(dd)) if len(dd) else None
    res = pulses.analyze(t, y, nominal_ms=widths, dt_ref=dt_ref, filter_s=filt, gap_factor=a.gap)
    pl = res["pulses"]
    out = a.out or d
    os.makedirs(out, exist_ok=True)
    prefix = "pulses" if base == "pd" else f"{base}_pulses"
    err = pulses.save_all(out, t, y, res, prefix=prefix, title=f"{os.path.basename(d)}  {os.path.basename(f)}")
    dt = np.diff(t)
    if len(dt):
        print(f"샘플 {len(t)}개, 간격 중앙 {1e3 * np.median(dt):.2f} ms / 평균 {1e3 * dt.mean():.2f} ms / "
              f"최대 {1e3 * dt.max():.0f} ms, 필터 {1e3 * filt:g} ms")
    for ln in pulses.summary_lines(res, os.path.basename(f)):
        print(ln)
    if err:
        print(err)
    print(f"→ {os.path.join(out, prefix)}.csv, {prefix}_groups.csv, {prefix}.png, {prefix}_summary.txt")
    return 0 if pl else 1


def entry():
    raise SystemExit(common.run(main, "pulses"))


if __name__ == "__main__":
    entry()
