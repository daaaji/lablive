"""장비 없이 돌려보는 시뮬레이터 — 가상 1936-R(SIM-PM) + 2400(SIM-K).

모든 CLI의 --sim 이 이것을 켠다. pyserial의 Serial/comports를 바꿔치기하므로 lablive 코드는 그대로 실행된다.
모델 (uvc275 모듈을 시간 압축해 흉내):
  - 열 경로 2개 τ = 0.4 s, 4 s (실물 수십~수백 s), 진폭 2%, 6% (광출력 감소)
  - 단자 전압 V = 6.20 + 5.3·I − 0.2·θ·(I/0.1),  V < 6.26 V면 출력에 ±1.5% 요동 (문턱 발진 흉내)
  - 꺼진 동안 냉각 (열 기억) → 연달아 켜면 '데워진 시작' 재현
  - 정전압 모드: I = (V − 6.20)/5.3 (문턱 아래는 누설), 전류 compliance
  - 1936-R 펌웨어 버릇: PM:FILTer가 DIGITALFILTER를 100으로 되돌림, 과범위는 3.4E+38
"""
import math
import random
import threading
import time
import types

FS = [2.5e-9 * 10 ** k for k in range(8)]
P = {"tau": (0.4, 4.0), "amp": (0.02, 0.06), "eta": 1.33e-3, "r": 5.3, "v0": 6.20, "vdrop": 0.20,
     "v_osc": 6.26, "leak": 6e-5, "dark": 5e-12}
# 환경변수 LABLIVE_SIM_LED=bare → 단일 접합 bare LED (InGaN 청색 비슷: V(1 mA) ≈ 2.7 V, n = 3, Rs 12 Ω,
#   발진 없음, 효율 = SRH 상승(I_srh 0.2 mA) × droop(I_d 30 mA) → 최고 ~2.4 mA, 시정수는 같음)
BARE = {"kind": "bare", "n": 3.0, "v1": 2.70, "r": 12.0, "vdrop": 0.05, "v_osc": 0.0, "leak": 1e-8,
        "eta": 4e-3, "i_droop": 0.03, "i_srh": 2e-4}
P["kind"] = "module"
P["i_droop"] = None
P["i_srh"] = None
PM_PORT, K_PORT = "SIM-PM", "SIM-K"
ST = {}
HEAT = {}
DS = {"t0": None, "vals": [], "n": 0, "iv": 1}
_lock = threading.Lock()


# 환경변수 LABLIVE_SIM_SHUTTER="100,140,200:8:800:5" → 셔터 펄스 흉내 (폭 ms 목록 : 반복 : 사이 어둠 ms : LED ON 후 시작 s)
#   각 폭을 반복 횟수만큼 (열림 폭 → 어둠) 하고, 폭이 바뀔 때 2 s 쉼. 셔터 전이 3 ms, 닫힘 누설 0.08%.
#   닫힘 시간이 폭보다 OPEN_EXTRA_MS 길게 (11/25 실측의 +12 ms 흉내).
SHUTTER = {"plan": None, "edge_s": 0.003, "leak": 8e-4, "extra_s": 0.012}


def shutter_plan(spec):
    w, rep, dark, start = (spec.split(":") + ["8", "800", "5"])[:4] if ":" in spec else (spec, "8", "800", "5")
    t = float(start)
    plan = []
    for x in w.split(","):
        for _ in range(int(rep)):
            plan.append((t, t + float(x) / 1e3 + SHUTTER["extra_s"]))
            t += float(x) / 1e3 + float(dark) / 1e3
        t += 2.0
    return plan


def transmission(t):
    """셔터 투과율 0(+누설)…1 — 열림/닫힘 모두 edge_s 동안 직선."""
    plan = SHUTTER["plan"]
    if plan is None:
        return 1.0
    e, lk = SHUTTER["edge_s"], SHUTTER["leak"]
    for a, b in plan:
        if a <= t < b + e:
            up = min(1.0, (t - a) / e)
            down = 1.0 if t < b else max(0.0, 1 - (t - b) / e)
            return lk + (1 - lk) * min(up, down)
    return lk


def transmission_avg(t, win):
    """1936-R 디지털 이동평균(win s) 흉내."""
    if SHUTTER["plan"] is None or win <= 0:
        return transmission(t)
    n = max(2, min(40, int(win / 5e-4)))
    return sum(transmission(t - win * (k + 0.5) / n) for k in range(n)) / n


def use(kind):
    """'module'(uvc275 흉내, 기본) 또는 'bare'"""
    if kind == "bare":
        P.update(BARE)
    P["kind"] = kind


def reset():
    ST.update(func="CURR", I=0.0, V=0.0, out=False, t_on=None, vcomp=21.0, icomp=0.105,
              k_garbage=False, k_err=[])
    HEAT.update(c0=[0.0, 0.0], c_off=[0.0, 0.0], t_off=None)


reset()


def comps(now=None):
    """열 경로별 가열 정도 (0 = 냉, 1 = 평형)"""
    now = now or time.perf_counter()
    if not ST["out"] or ST["t_on"] is None:
        if HEAT["t_off"] is None:
            return [0.0, 0.0]
        return [c * math.exp(-(now - HEAT["t_off"]) / tau) for c, tau in zip(HEAT["c_off"], P["tau"])]
    t = now - ST["t_on"]
    return [1 - (1 - c0) * math.exp(-t / tau) for c0, tau in zip(HEAT["c0"], P["tau"])]


def _theta():
    c = comps()
    a1, a2 = P["amp"]
    return (a1 * c[0] + a2 * c[1]) / (a1 + a2)


def _v_of_i(i):
    if P["kind"] == "bare":
        vt = 0.025693 * P["n"]
        return (P["v1"] + vt * math.log(max(i, 1e-12) / 1e-3) + P["r"] * (i - 1e-3)
                - P["vdrop"] * _theta() * (i / 0.02))
    return P["v0"] + P["r"] * i - P["vdrop"] * _theta() * (i / 0.1)


def _i_of_v(v):
    if P["kind"] == "bare":
        lo, hi = 1e-12, 1.0
        for _ in range(60):
            mid = math.sqrt(lo * hi)
            lo, hi = (mid, hi) if _v_of_i(mid) < v else (lo, mid)
        return lo
    return (v - P["v0"]) / P["r"] if v > P["v0"] + P["r"] * P["leak"] else P["leak"] * max(v, 0) / P["v0"]


def operating_point():
    """(V, I, tripped)"""
    if not ST["out"]:
        return 0.0, 0.0, False
    if ST["func"] == "CURR":
        i = ST["I"]
        v = _v_of_i(i)
        if v > ST["vcomp"]:
            return ST["vcomp"], _i_of_v(ST["vcomp"]), True
        return v, i, False
    v = ST["V"]
    i = _i_of_v(v)
    if i > ST["icomp"]:
        return _v_of_i(ST["icomp"]), ST["icomp"], True
    return v, i, False


def light(filtered=True, now=None, win=0.0):
    dark = P["dark"] * (1 + random.gauss(0, 0.2))
    if not ST["out"]:
        return dark
    sh = 1.0
    if SHUTTER["plan"] is not None:
        now = now or time.perf_counter()
        sh = transmission_avg(now - ST["t_on"], win)
    v, i, _ = operating_point()
    c = comps()
    a1, a2 = P["amp"]
    y = P["eta"] * max(i - P["leak"], 0) * (1 - a1 * c[0] - a2 * c[1]) * (1 + random.gauss(0, 1e-4))
    if P["i_droop"]:
        y /= 1 + i / P["i_droop"]
    if P["i_srh"]:
        y *= i / (i + P["i_srh"])
    if v < P["v_osc"]:                          # 1.4 kHz 이완 발진: 50 ms 필터 후 ±1.5%, 필터 없으면 ±60%
        y *= 1 + random.uniform(-1, 1) * (0.015 if filtered else 0.6)
    return y * sh + dark


class FakeSerial:
    def __init__(self, port, baud=9600, **kw):
        self.port, self.baud = port, baud
        if port not in (PM_PORT, K_PORT):
            raise OSError(f"no such port {port}")
        self.kind = "pm" if port == PM_PORT else "k"
        self.ok = (self.kind == "pm" and baud == 38400) or (self.kind == "k" and baud == 9600)
        self.buf = bytearray()
        self.timeout = kw.get("timeout", 1)
        self.reg = {"PM:MODE?": "0", "PM:UNITs?": "3", "PM:Lambda?": "275", "PM:AUTO?": "1", "PM:RANge?": "0",
                    "PM:FILTer?": "3", "PM:ANALOGFILTER?": "4", "PM:DIGITALFILTER?": "10000",
                    "PM:DS:INTerval?": "1", "PM:DS:SIZE?": "1000"} if self.kind == "pm" else {}

    def reset_input_buffer(self):
        self.buf.clear()

    def flush(self):
        pass

    def close(self):
        pass

    @property
    def in_waiting(self):
        return len(self.buf)

    def read(self, n=1):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_until(self, eol=b"\n"):
        i = self.buf.find(eol)
        if i < 0:
            out = bytes(self.buf)
            self.buf.clear()
            return out
        out = bytes(self.buf[:i + len(eol)])
        del self.buf[:i + len(eol)]
        return out

    def write(self, b):
        if not self.ok:              # 다른 baud → 장비에는 CR 없는 깨진 문자열로 들어감 (실물 2400: 다음 줄과 합쳐져 -101)
            if self.kind == "k":
                ST["k_garbage"] = True
            return len(b)
        time.sleep(0.03 if self.kind == "pm" else 0.06)   # 왕복 지연 흉내
        with _lock:
            segs = b.decode().split("\r")
            if self.kind == "k" and ST["k_garbage"] and len(segs) > 1:
                ST["k_garbage"] = False          # 깨진 문자열 + 첫 줄 → 그 줄은 실행되지 않고 오류
                ST["k_err"].append('-101,"Invalid character"')
                segs = segs[1:]
            for c in segs:
                if c.strip():
                    self.handle(c.strip())
        return len(b)

    def resp(self, s):
        self.buf += (s + ("\r\n" if self.kind == "pm" else "\r")).encode()

    def handle(self, c):
        if self.kind == "pm":
            return self._pm(c)
        if c.endswith(";*OPC?"):
            self._k(c[:-6])
            return self.resp("1")
        return self._k(c)

    def _pm(self, c):
        if c == "*IDN?":
            return self.resp("NEWPORT 1936-R v1.2.4 (SIMULATOR)")
        if c == "ERRSTR?":
            return self.resp("0,No Error")
        if c == "PM:P?":
            return self.resp(self._value(time.perf_counter()))
        if c == "PM:DS:ENable 1":
            DS.update(t0=time.perf_counter(), n=int(self.reg.get("PM:DS:SIZE?", "1000")),
                      iv=int(self.reg["PM:DS:INTerval?"]), vals=[])
            return
        if c == "PM:DS:ENable 0":
            self._ds_fill()
            DS["t0"] = None
            return
        if c == "PM:DS:CLear":
            DS.update(t0=None, vals=[])
            return
        if c == "PM:DS:Count?":
            self._ds_fill()
            return self.resp(str(len(DS["vals"])))
        if c.startswith("PM:DS:GET? "):
            a, b = (int(x) for x in c.split()[1].split("-"))
            body = "\r\n".join(DS["vals"][a - 1:b])
            self.buf += ("Responsivity: 9.800000E-02\r\nEnd of Header\r\n" + body + "\r\nEnd of Data\r\n").encode()
            return
        if c in self.reg:
            return self.resp(self.reg[c])
        if " " in c:
            key, val = c.split(" ", 1)
            if key + "?" in self.reg:
                self.reg[key + "?"] = val
            if key == "PM:FILTer":            # 실측 버릇
                self.reg["PM:DIGITALFILTER?"] = "100"
            if key == "PM:DS:SIZE":
                self.reg["PM:DS:SIZE?"] = str(min(250000, max(1, int(val))))

    def _value(self, now):
        r = int(self.reg["PM:RANge?"])
        filt = self.reg["PM:FILTer?"] in ("2", "3")
        win = int(self.reg["PM:DIGITALFILTER?"]) * 1e-4 if filt else 0.0
        v = light(filt and win >= 0.01, now=now, win=win)
        if abs(v) > FS[r]:
            return "3.402823E+38"
        lsb = FS[r] / 32768
        return f"{round(v / lsb) * lsb:.6E}"

    def _ds_fill(self):
        """data store: ENable 1 이후 지난 시간만큼 (interval × 0.1 ms 간격) 값을 채움."""
        if DS.get("t0") is None:
            return
        dt = DS["iv"] * 1e-4
        upto = min(DS["n"], int((time.perf_counter() - DS["t0"]) / dt))
        while len(DS["vals"]) < upto:
            DS["vals"].append(self._value(DS["t0"] + len(DS["vals"]) * dt))

    def _k(self, c):
        if c == "*IDN?":
            return self.resp("KEITHLEY INSTRUMENTS INC.,MODEL 2400,SIMULATOR,C34")
        if c == ":SYST:ERR?":
            return self.resp(ST["k_err"].pop(0) if ST["k_err"] else '0,"No error"')
        if c == "*CLS":
            ST["k_err"].clear()
            return
        arg = c.split()[1] if " " in c else None
        if c.startswith(":SOUR:FUNC "):
            ST["func"] = arg
        elif c.startswith(":SOUR:CURR:LEV "):
            ST["I"] = float(arg)
        elif c.startswith(":SOUR:VOLT:LEV "):
            ST["V"] = float(arg)
        elif c.startswith(":SENS:VOLT:PROT "):
            ST["vcomp"] = float(arg)
        elif c.startswith(":SENS:CURR:PROT "):
            ST["icomp"] = float(arg)
        elif c == ":SOUR:FUNC?":
            return self.resp(ST["func"])
        elif c == ":SOUR:CURR:LEV?":
            return self.resp(f"{ST['I']:.6E}")
        elif c == ":OUTP ON" and not ST["out"]:
            HEAT["c0"] = comps()
            ST["out"], ST["t_on"] = True, time.perf_counter()
        elif c == ":OUTP OFF" and ST["out"]:
            HEAT["c_off"], HEAT["t_off"] = comps(), time.perf_counter()
            ST["out"] = False
        elif c == ":OUTP?":
            return self.resp("1" if ST["out"] else "0")
        elif c == ":READ?":
            v, i, _ = operating_point()
            if ST["func"] == "CURR":
                i = ST["I"] if ST["out"] else 0.0
            return self.resp(f"{v:+.6E},{i:+.6E}")
        elif c in (":SENS:VOLT:PROT:TRIP?", ":SENS:CURR:PROT:TRIP?"):
            _, _, trip = operating_point()
            return self.resp("1" if trip else "0")


_installed = False


def install():
    """pyserial을 시뮬레이터로 교체 (프로세스 전체). 이미 import된 lablive 모듈에도 적용된다."""
    global _installed
    import serial
    from serial.tools import list_ports
    import os
    use(os.environ.get("LABLIVE_SIM_LED", "module"))
    sp = os.environ.get("LABLIVE_SIM_SHUTTER")
    SHUTTER["plan"] = shutter_plan(sp) if sp else None
    serial.Serial = FakeSerial
    # 2400을 앞 번호로 (랩 PC처럼): 1936-R 탐색이 2400 포트에 38400 baud로 *IDN?을 보내는 경우를 재현
    list_ports.comports = lambda: [types.SimpleNamespace(device=K_PORT, description="SIM 2400"),
                                   types.SimpleNamespace(device=PM_PORT, description="SIM 1936-R")]
    reset()
    _installed = True


def installed():
    return _installed
