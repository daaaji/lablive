"""광원 프로파일 — LED 하나에 대해 '장비 설정 + 전류별 기준 열 모델 + 측정 회차 목록'을 JSON 하나로.

profiles/<name>.json
{
  "name": "uvc275", "description": "...",
  "lambda_nm": 275, "i_max_A": 0.25, "compliance_V": 12.0, "vf_guard_V": 6.30,
  "models": [   # 전류별 기준 모델 (회차 하나를 골라 등록)
    {"current_A": 0.1, "taus_s": [...], "amps_pct": [...], "I_final_A": ..., "V_start": ..., "V_final": ...,
     "t_settle_s": {"1%": ...}, "run": "runs/...", "registered": "..."}
  ],
  "runs": [ {"path": "runs/...", "added": "..."} ]   # 이 LED로 측정한 모든 회차 (history용)
}
경로는 profiles 폴더의 상위 폴더(= 데이터 home) 기준 상대경로로 저장 (home 통째로 옮겨도 동작).
추가 필드 (0.9): "check": 극성 점검 기록 {"I_A", "V"} — 다음 회차에서 같은 LED/배선인지 확인
                 "iv": I-V 스윕 회차 목록
"""
import json
import math
import os
from datetime import datetime

from . import config

_SAME_I = 0.02   # 전류가 2% 이내면 같은 전류로 취급


class Profile:
    def __init__(self, data, path):
        self.data, self.path = data, path
        self.base = os.path.dirname(os.path.dirname(os.path.abspath(path)))

    # ---- 파일 ----
    @classmethod
    def path_for(cls, name, root=None):
        return os.path.join(root or config.profiles_dir(), f"{name}.json")

    @classmethod
    def load(cls, name, root=None):
        p = name if name.endswith(".json") else cls.path_for(name, root)
        if not os.path.exists(p):
            have = ", ".join(cls.names(root)) or "없음"
            raise FileNotFoundError(f"프로파일 '{name}' 없음 (있는 것: {have}, 위치 {root or config.profiles_dir()}) "
                                    f"— led-profile new 로 생성")
        with open(p, encoding="utf-8") as f:
            return cls(json.load(f), p)

    @classmethod
    def create(cls, name, root=None, **fields):
        if not name or any(c in name for c in '\\/:*?"<>| '):
            raise ValueError(f"프로파일 이름에 공백/경로 문자 불가: {name!r}")
        p = cls.path_for(name, root)
        if os.path.exists(p):
            raise FileExistsError(f"이미 있음: {p}")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = {"name": name, "description": "", "lambda_nm": None, "i_max_A": None,
                "compliance_V": 12.0, "vf_guard_V": None, "check": None, "models": [], "runs": [], "iv": [],
                "created": datetime.now().isoformat(timespec="seconds")}
        data.update({k: v for k, v in fields.items() if v is not None})
        prof = cls(data, p)
        prof.save()
        return prof

    @staticmethod
    def names(root=None):
        root = root or config.profiles_dir()
        if not os.path.isdir(root):
            return []
        return sorted(f[:-5] for f in os.listdir(root) if f.endswith(".json"))

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---- 경로 ----
    def rel(self, path):
        """home 아래면 상대경로, 밖(다른 드라이브/공유 폴더)이면 절대경로로 저장."""
        ap = os.path.abspath(path)
        try:
            r = os.path.relpath(ap, self.base)
        except ValueError:          # Windows: 다른 드라이브
            return ap.replace("\\", "/")
        return ap.replace("\\", "/") if r.startswith("..") else r.replace("\\", "/")

    def abs(self, rel):
        return os.path.normpath(rel if os.path.isabs(rel) else os.path.join(self.base, rel))

    # ---- 속성 ----
    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    @property
    def name(self):
        return self.data["name"]

    # ---- 회차 / 모델 ----
    def register_run(self, run_dir):
        r = self.rel(run_dir)
        if all(x["path"] != r for x in self.data["runs"]):
            self.data["runs"].append({"path": r, "added": datetime.now().isoformat(timespec="seconds")})
            self.save()
        return r

    def register_iv(self, run_dir):
        r = self.rel(run_dir)
        self.data.setdefault("iv", [])
        if all(x["path"] != r for x in self.data["iv"]):
            self.data["iv"].append({"path": r, "added": datetime.now().isoformat(timespec="seconds")})
            self.save()
        return r

    def update(self, **fields):
        self.data.update(fields)
        self.save()

    def run_dirs(self):
        return [self.abs(x["path"]) for x in self.data["runs"]]

    def set_model(self, entry):
        """같은 전류(±2%)의 기존 기준 모델을 교체. 반환: 교체 여부."""
        I = entry["current_A"]
        ms = self.data["models"]
        old = [m for m in ms if abs(m["current_A"] - I) <= _SAME_I * I]
        self.data["models"] = sorted([m for m in ms if m not in old] + [entry], key=lambda m: m["current_A"])
        self.save()
        return bool(old)

    def model_for(self, current):
        """반환 (prior dict, 설명) — 없으면 (None, 이유).
        정확히 같은 전류 → 그대로. 두 기준 전류 사이 → τ는 log-log, 진폭은 log(I)에 선형 보간 (항 수가 같을 때만).
        범위 밖은 쓰지 않는다 (외삽된 열 모델로 '안정'을 판정하면 안 되므로)."""
        ms = sorted(self.data["models"], key=lambda m: m["current_A"])
        if not ms:
            return None, "no reference model yet"
        for m in ms:
            if abs(m["current_A"] - current) <= _SAME_I * current:
                return dict(m, how=f"exact @ {m['current_A'] * 1e3:g} mA"), "exact"
        lo = [m for m in ms if m["current_A"] < current]
        hi = [m for m in ms if m["current_A"] > current]
        have = ", ".join(f"{m['current_A'] * 1e3:g}" for m in ms)
        if not lo or not hi:
            return None, f"{current * 1e3:g} mA outside model range (have {have} mA)"
        a, b = lo[-1], hi[0]
        if len(a["taus_s"]) != len(b["taus_s"]):
            return None, f"{a['current_A'] * 1e3:g}/{b['current_A'] * 1e3:g} mA models differ in term count, no interpolation"
        w = (math.log(current) - math.log(a["current_A"])) / (math.log(b["current_A"]) - math.log(a["current_A"]))
        taus = [math.exp((1 - w) * math.log(x) + w * math.log(y)) for x, y in zip(a["taus_s"], b["taus_s"])]
        amps = [(1 - w) * x + w * y for x, y in zip(a["amps_pct"], b["amps_pct"])]
        out = {"current_A": current, "taus_s": taus, "amps_pct": amps,
               "how": f"interpolated {a['current_A'] * 1e3:g}–{b['current_A'] * 1e3:g} mA",
               "run": f"{a.get('run')} + {b.get('run')}"}
        for key in ("V_start", "V_final"):
            if a.get(key) is not None and b.get(key) is not None:
                out[key] = (1 - w) * a[key] + w * b[key]
        return out, "interpolated"
