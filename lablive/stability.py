"""광원 안정도 판정 — 장비와 무관한 순수 계산 모듈 ((t, y)만 받음).

두 기준을 따로 계산하고, 상태 이름에 어느 기준을 만족했는지를 그대로 드러낸다.

1) 단기 안정 (window 기준)
   최근 window_s 동안 선형 drift 크기 ≤ tol/2, 선형 잔차 노이즈 ≤ tol/4.
   '지금 변화가 느리다'는 뜻일 뿐 최종값에 가깝다는 보장은 없다
   (100 mA 실측: τ≈250 s 성분이 5% 남아 있어도 60 s 창 drift는 1% 미만).

2) 최종값 기준 (fit)
   다중 지수(thermfit)로 예측한 최종값 대비 현재값 편차 ≤ tol.
   데이터 길이가 최장 τ의 3배 미만이면 reliable=False (외삽 — 예측 최종값이 움직일 수 있음).

3) 프로파일 모델 (prior, 선택)
   같은 LED·같은 전류에서 미리 측정해 저장한 곡선 모양 g(t) = 1 + Σ r_k·exp(-t/τ_k)를 그대로 쓰고
   크기 하나만 맞춘다:  I(t) ≈ I∞·g(t),  I∞ = Σ I·g / Σ g²  (로그 시간 구간 평균 위에서).
   → 켠 지 수 초 만에 최종값과 ±tol 도달 시각을 예측.
   모양이 안 맞으면 (잔차 rms > max(0.15%, tol/4), 데이터 5 s 이상) MISMATCH로 고정하고
   자유 피팅(2)으로 돌아간다 — 예열된 상태로 시작, 고정대/정렬 변경, LED 열화 등.

   한계: 초반 데이터로는 τ가 긴 성분의 진폭을 구분하지 못해 모양 검사가 늦게(실측 ~900 s) 잡힌다.
   그래서 시작 상태(시작 전압)가 기준 회차와 다르면 처음부터 쓰지 않는다 (reject_prior).

SETTLED 여유(settle_margin): 측정 길이가 짧을 때 피팅은 가장 느린 성분을 과소평가해 최종값을 높게 잡는다
   (11/06 100 mA 30분 실측: 600 s까지로 피팅하면 최종값 +0.42%, 900 s면 +0.29%).
   그대로 '최종값 ±tol'로 판정하면 실제 편차 +1.35%일 때 SETTLED가 떴다. 기본 margin 0.5 → |편차| ≤ tol/2.

상태: waiting → settling → quasi(단기 안정만) → settled(둘 다)
      anomaly: 최근 chunk_s 구간 노이즈가 (a) 창 내 중앙값의 5배 이상이고 0.2% 이상 (급증), 또는
               (b) 절대값 0.5% 이상 (처음부터 계속 발진하는 경우 — 비교할 정상 구간이 없음)
               정상 노이즈 실측 0.01–0.03%, 이 모듈의 6.26 V 문턱 발진은 50 ms 필터 후 ±1–2%
"""
import math
import threading

import numpy as np

from . import thermfit


class StabilityTracker:
    PRIOR_RMS_MAX = 0.15     # %
    PRIOR_CHECK_S = 5.0      # 이 길이 이상 데이터가 있어야 MISMATCH 판정

    def __init__(self, tol=0.01, window_s=60.0, avg_s=1.0, chunk_s=2.0,
                 fit_every_s=15.0, fit_min_s=20.0, nmax=3, prior=None, settle_margin=0.5):
        self.tol, self.window_s, self.avg_s, self.chunk_s = tol, window_s, avg_s, chunk_s
        self.fit_every_s, self.fit_min_s, self.nmax = fit_every_s, fit_min_s, nmax
        self.prior = prior
        self.settle_margin = settle_margin
        self._pfit = None
        self.prior_mismatch_at = None
        self.prior_reject_reason = None
        self.prior_t_settle = None
        if prior:
            shape = {"taus": np.array(prior["taus_s"], float),
                     "amps": np.array(prior["amps_pct"], float) / 100, "y_inf": 1.0}
            st = thermfit.settle_times(shape, 1e-2, 1e9, tols=(tol,))
            self.prior_t_settle = float(next(iter(st.values()))["t_s"])
        self._t, self._y = [], []
        self._lock = threading.Lock()
        self._fit = None
        self._fit_err = None
        self._halt = threading.Event()
        self._thread = None
        self.t_first_settled = None

    # ---- 데이터 ----
    def add(self, t, y):
        if t is None or y is None or not (math.isfinite(t) and math.isfinite(y)) or t < 0:
            return
        with self._lock:
            self._t.append(float(t))
            self._y.append(float(y))

    def arrays(self):
        with self._lock:
            return np.array(self._t), np.array(self._y)

    # ---- 피팅 (백그라운드) ----
    def start(self):
        self._halt.clear()
        self._thread = threading.Thread(target=self._fit_loop, name="fit", daemon=True)
        self._thread.start()

    def stop(self):
        self._halt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(10)

    def _fit_loop(self):
        last_free = 0.0
        while not self._halt.wait(1.0):
            self.refit_prior()
            if self._halt.is_set():
                break
            last_free += 1.0
            if self.fit_every_s and last_free >= self.fit_every_s:      # 0/None = 자유 피팅 안 함 (펄스 모드)
                last_free = 0.0
                self.refit()

    def reject_prior(self, reason):
        """시작 상태가 기준 회차와 달라 프로파일 곡선을 쓰지 않음 (MISMATCH로 고정)."""
        self.prior_reject_reason = reason
        if self.prior_mismatch_at is None:
            self.prior_mismatch_at = 0.0
        if self._pfit is not None:
            self._pfit = dict(self._pfit, mismatch=True, mismatch_at=0.0, reason=reason)

    def refit_prior(self):
        """프로파일 곡선 모양을 고정하고 크기 I∞만 맞춘다 (1 파라미터, 닫힌 해)."""
        p = self.prior
        if not p:
            return None
        t, y = self.arrays()
        if len(t) < 20 or t[-1] - t[0] < 1.0:
            return None
        tb, yb = thermfit.logbin(t, y)
        g = 1 + sum(r / 100 * np.exp(-tb / tau) for r, tau in zip(p["amps_pct"], p["taus_s"]))
        s = float(yb @ g / (g @ g))
        rms = float(100 * np.sqrt(np.mean((yb - s * g) ** 2)) / abs(s))
        span = float(t[-1] - t[0])
        limit = max(self.PRIOR_RMS_MAX, 25 * self.tol)
        if self.prior_mismatch_at is None and span >= self.PRIOR_CHECK_S and rms > limit:
            self.prior_mismatch_at = float(t[-1])
        self._pfit = {"at": float(t[-1]), "y_inf": s, "rms_pct": rms, "rms_limit_pct": limit,
                      "mismatch": self.prior_mismatch_at is not None,
                      "mismatch_at": self.prior_mismatch_at, "t_settle": self.prior_t_settle,
                      "reason": self.prior_reject_reason or ("shape" if self.prior_mismatch_at else None),
                      "how": p.get("how"), "source_run": p.get("run")}
        return self._pfit

    def refit(self):
        t, y = self.arrays()
        if len(t) < 20 or t[-1] - t[0] < self.fit_min_s:
            return None
        try:
            res = thermfit.fit_signal(t, y, self.nmax)
        except Exception as e:  # noqa: BLE001
            self._fit_err = f"{type(e).__name__}: {e}"
            return None
        if "error" in res:
            self._fit_err = res["error"]
            return None
        m = res["_model"]
        st = thermfit.settle_times(m, float(t.min()), float(t[-1]), tols=(self.tol,))
        y_inf = float(m["y_inf"])
        fit = {
            "at": float(t[-1]),
            "n_terms": int(res["n_terms"]),
            "y_inf": y_inf,
            "taus": [float(x) for x in m["taus"]],
            "amps_pct": [float(100 * a / y_inf) for a in m["amps"]],
            "t_settle": float(next(iter(st.values()))["t_s"]),
            "reliable": not bool(res["tau_max_poorly_constrained"]),
            "rms_resid_pct": float(res["rms_resid_pct"]),
        }
        self._fit_err = None
        with self._lock:
            self._fit = fit
        return fit

    def _use_prior(self):
        return self._pfit is not None and not self._pfit["mismatch"]

    def model(self):
        """현재 최종값 판정에 쓰는 곡선 (taus, amps, y_inf, source) 또는 None."""
        if self._use_prior():
            p, y_inf = self.prior, self._pfit["y_inf"]
            return (np.array(p["taus_s"]), np.array(p["amps_pct"]) * y_inf / 100, y_inf, "profile")
        f = self._fit
        if not f:
            return None
        return (np.array(f["taus"]), np.array(f["amps_pct"]) * f["y_inf"] / 100, f["y_inf"], "free")

    # ---- 판정 ----
    BURST_ABS_PCT = 0.5

    def _burst(self, tw, yw):
        """최근 chunk 노이즈 vs 창 내 chunk 노이즈 중앙값 (%)."""
        if len(tw) < 10:
            return False, math.nan, math.nan
        idx = ((tw[-1] - tw) // self.chunk_s).astype(int)
        vals = {}
        for c in np.unique(idx):
            sel = idx == c
            if sel.sum() < 5:
                continue
            tc, yc = tw[sel], yw[sel]
            if tc[-1] - tc[0] <= 0:
                continue
            b, a = np.polyfit(tc - tc[0], yc, 1)
            r = yc - (a + b * (tc - tc[0]))
            if yc.mean() == 0:
                continue
            vals[int(c)] = 100 * r.std() / abs(yc.mean())
        if 0 not in vals:
            return False, math.nan, math.nan
        burst = float(vals[0])
        others = [v for c, v in vals.items() if c != 0]
        base = float(np.median(others)) if len(others) >= 2 else math.nan
        jump = math.isfinite(base) and burst > max(5 * base, 0.2)
        return bool(jump or burst > self.BURST_ABS_PCT), burst, base

    def status(self):
        t, y = self.arrays()
        out = {"n": int(len(t)), "state": "waiting", "tol_pct": 100 * self.tol,
               "window_s": self.window_s, "settle_margin": self.settle_margin,
               "fit": None, "fit_error": self._fit_err,
               "t_first_settled": self.t_first_settled}
        if len(t) < 10:
            return out
        t_now = float(t[-1])
        y_now = float(y[t >= t_now - self.avg_s].mean())
        w = t >= t_now - self.window_s
        tw, yw = t[w], y[w]
        span = float(tw[-1] - tw[0])
        mean = float(yw.mean())
        slope_pct_min = drift_pct = noise_pct = math.nan
        if len(tw) >= 5 and span > 0 and mean != 0:
            b, a = np.polyfit(tw - tw[0], yw, 1)
            r = yw - (a + b * (tw - tw[0]))
            slope_pct_min = float(100 * b / mean * 60)
            drift_pct = float(abs(100 * b * self.window_s / mean))
            noise_pct = float(100 * r.std() / abs(mean))
        full = span >= 0.9 * self.window_s
        short_stable = bool(full and drift_pct <= 50 * self.tol and noise_pct <= 25 * self.tol)
        anomaly, burst, base = self._burst(tw, yw)

        fit_out = None
        f = self._fit
        if self._use_prior():
            pf = self._pfit
            f = {"source": "profile", "at": pf["at"], "n_terms": len(self.prior["taus_s"]),
                 "y_inf": pf["y_inf"], "taus": list(self.prior["taus_s"]),
                 "amps_pct": list(self.prior["amps_pct"]), "t_settle": pf["t_settle"],
                 "reliable": True, "rms_resid_pct": pf["rms_pct"]}
        elif f:
            f = dict(f, source="free")
        if f:
            fit_out = dict(f)
            fit_out["dev_pct"] = 100 * (y_now - f["y_inf"]) / f["y_inf"]
            fit_out["eta_s"] = f["t_settle"] - t_now
            fit_out["age_s"] = t_now - f["at"]

        if anomaly:
            state = "anomaly"
        elif fit_out and abs(fit_out["dev_pct"]) <= 100 * self.tol * self.settle_margin and short_stable:
            state = "settled"
        elif short_stable:
            state = "quasi"
        else:
            state = "settling"
        if state == "settled" and self.t_first_settled is None:
            self.t_first_settled = t_now

        out.update(t=t_now, y_now=y_now, state=state, short_stable=short_stable,
                   window={"span_s": span, "full": full, "mean": mean, "slope_pct_min": slope_pct_min,
                           "drift_pct": drift_pct, "noise_pct": noise_pct},
                   anomaly=anomaly, burst_noise_pct=burst, base_noise_pct=base,
                   fit=fit_out, t_first_settled=self.t_first_settled, prior=self._pfit)
        return out
