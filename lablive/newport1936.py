"""Newport 1936-R (RS-232) — 광전류(A) 측정용 최소 드라이버.

이 연구실 개체(fw v1.2.4)에서 실측으로 확인한 동작:
- 38400 8N1 고정, 명령 끝 CR, 응답 끝 CRLF. ECHO가 켜져 있을 수 있어 ECHO 0으로 시작
- 과범위 또는 range 전환 중에는 PM:P?가 3.4E+38(FLT_MAX)을 반환
- PM:FILTer 명령이 디지털 필터 크기를 100으로 되돌림 → 반드시 FILTer 다음에 DIGITALFILTER
- PM:DS:INTerval?는 ms가 아니라 설정 정수를 그대로 반환
"""
import math
import re

from .serialdev import SerialDevice, find_port, kick, remember_port

FS = [2.5e-9 * 10 ** k for k in range(8)]  # 전류 range 0..7 full scale (A)
BW = ["1.2 Hz", "4.8 Hz", "48 Hz", "480 Hz", "4 kHz", "43 kHz", "170 kHz", "400 kHz"]
OVER = 1e30
_NUM = re.compile(r"[-+]?\d+(?:\.\d*)?(?:[Ee][-+]?\d+)?")
STATE_QUERIES = ("PM:MODE?", "PM:UNITs?", "PM:Lambda?", "PM:AUTO?", "PM:RANge?",
                 "PM:FILTer?", "PM:ANALOGFILTER?", "PM:DIGITALFILTER?", "PM:DS:INTerval?")


def _int(txt):
    m = _NUM.search(txt or "")
    return int(float(m.group())) if m else None


def echo_off(dev):
    _echo_off(dev)


def _echo_off(dev):
    kick(dev)
    dev.write("ECHO 0", 0.2)
    dev.s.reset_input_buffer()


class Newport1936:
    BAUD = 38400

    def __init__(self, port=None, exclude=()):
        port = port or find_port(self.BAUD, "NEWPORT", prep=_echo_off, exclude=exclude, role="pm", other="k")
        if not port:
            raise RuntimeError("1936-R를 찾지 못함 — 전원/케이블, 다른 프로그램이 포트를 잡고 있는지 확인")
        self.port = port
        try:
            self.dev = SerialDevice(port, self.BAUD)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"{port}를 열 수 없음 ({e}) — 다른 프로그램이 사용 중이거나 없는 포트") from e
        _echo_off(self.dev)
        self.range = None
        idn = self.idn()
        if "NEWPORT" not in idn.upper():
            self.dev.close()
            raise RuntimeError(f"{port}의 장비가 1936-R이 아님 (응답: {idn!r}) — 장비 창에서 포트를 '자동'으로")
        remember_port("pm", port)

    def close(self):
        self.dev.close()

    def idn(self):
        return self.dev.query("*IDN?")

    def errors(self, limit=20):
        out = []
        for _ in range(limit):
            e = self.dev.query("ERRSTR?")
            if not e or e.startswith("0"):
                break
            out.append(e)
        return out

    # ---- 설정 저장/복구 ----
    def save_state(self):
        return {c: self.dev.query(c) for c in STATE_QUERIES}

    def restore_state(self, st):
        def i(c):
            return int(float(st[c]))
        w = self.dev.write
        w("PM:DS:ENable 0")
        w(f"PM:MODE {i('PM:MODE?')}")
        w(f"PM:UNITs {i('PM:UNITs?')}")
        w(f"PM:Lambda {i('PM:Lambda?')}")
        w(f"PM:FILTer {i('PM:FILTer?')}")            # 먼저 (digital 크기를 100으로 되돌리므로)
        w(f"PM:ANALOGFILTER {i('PM:ANALOGFILTER?')}")
        w(f"PM:DIGITALFILTER {i('PM:DIGITALFILTER?')}")
        if i("PM:AUTO?") == 0:
            w("PM:AUTO 0")
            w(f"PM:RANge {i('PM:RANge?')}")
        else:
            w("PM:AUTO 1")
        w(f"PM:DS:INTerval {max(1, i('PM:DS:INTerval?'))}")

    # ---- 측정 설정 ----
    def configure_current(self, lambda_nm=None, digital_filter=500):
        """단위 A, CW continuous, 아날로그 필터 OFF, 디지털 이동평균 digital_filter점
        (내부 10 kHz 기준 → 500점 = 50 ms, polling 20 Hz의 anti-alias). 반환: 필터 크기 readback.
        단위가 A면 λ는 값에 영향 없음 (기록용) — None이면 건드리지 않음."""
        lam = [f"PM:Lambda {int(lambda_nm)}"] if lambda_nm else []
        filt = ["PM:FILTer 2", "PM:ANALOGFILTER 0", f"PM:DIGITALFILTER {digital_filter}"] if digital_filter else \
               ["PM:FILTer 0", "PM:ANALOGFILTER 0"]      # 0 = 필터 없음 (순간값 — 발진 검출용)
        for c in ["PM:DS:ENable 0", "PM:MODE 0", "PM:UNITs 0"] + lam + filt:
            self.dev.write(c, 0.05)
        return self.dev.query("PM:DIGITALFILTER?")

    def set_range(self, r):
        r = max(0, min(7, int(r)))
        self.dev.write("PM:AUTO 0")
        self.dev.write(f"PM:RANge {r}", 0.3)
        self.range = r
        return self.dev.query("PM:RANge?")

    def read_A(self):
        """현재 광전류 (A). 과범위/파싱 실패는 NaN."""
        m = _NUM.search(self.dev.query("PM:P?"))
        if not m:
            return math.nan
        v = float(m.group())
        return math.nan if abs(v) >= OVER else v

    def read_A_at(self, clock):
        """(명령 직전 시각, 광전류 A). 시각은 포트 잠금 안에서 재므로 다른 스레드가 포트를 쓰는 동안의 대기가 안 섞임."""
        with self.dev.lock:
            t = clock()
            return t, self.read_A()

    def round_trip(self, n=12):
        """PM:P? 왕복 시간 (s) 중앙값과 전체 목록. 판독 간격 하한 = 이 값 (요청 Hz가 더 빠르면 못 따라감)."""
        import time
        dts = []
        for _ in range(n):
            t0 = time.perf_counter()
            self.read_A()
            dts.append(time.perf_counter() - t0)
        dts.sort()
        return dts[len(dts) // 2], dts

    def set_digital_filter(self, points):
        """디지털 이동평균 크기만 바꿈 (PM:FILTer는 건드리지 않음 — 그 명령이 크기를 100으로 되돌리므로)."""
        self.dev.write(f"PM:DIGITALFILTER {int(points)}", 0.05)
        return self.dev.query("PM:DIGITALFILTER?")

    # ---- data store (장비 내부 10 kHz 버퍼) ----
    DS_MAX = 250000          # 1936-R 사용설명서의 data store 최대 점 수. 실제 값은 SIZE? 확인값을 씀

    def ds_start(self, n, interval):
        """n점, interval×0.1 ms 간격으로 저장 시작 (가득 차면 정지). 반환: (확인된 SIZE, 확인된 INTerval)."""
        w = self.dev.write
        w("PM:DS:ENable 0", 0.05)
        w("PM:DS:CLear", 0.1)
        w("PM:DS:BUFfer 0", 0.05)
        w(f"PM:DS:INTerval {int(interval)}", 0.05)
        w(f"PM:DS:SIZE {int(n)}", 0.2)
        size = _int(self.dev.query("PM:DS:SIZE?"))
        iv = _int(self.dev.query("PM:DS:INTerval?"))
        w("PM:DS:ENable 1", 0)
        return size, iv

    def ds_count(self):
        return _int(self.dev.query("PM:DS:Count?"))

    def ds_stop(self):
        self.dev.write("PM:DS:ENable 0", 0.05)

    def ds_get(self, a, b, timeout=60.0):
        """a–b번 점 (1부터). 응답 = 헤더(Responsivity 등) / End of Header / 값들 / End of Data.
        과범위 값(FLT_MAX)은 NaN. 반환: 값 목록 (개수가 b−a+1과 다를 수 있음 — 호출한 쪽에서 확인)."""
        import time
        with self.dev.lock:
            s = self.dev.s
            s.reset_input_buffer()
            s.write((f"PM:DS:GET? {a}-{b}" + self.dev.term).encode("ascii"))
            buf = bytearray()
            t_end = time.monotonic() + timeout
            while time.monotonic() < t_end:
                chunk = s.read(max(1, s.in_waiting))
                if chunk:
                    buf += chunk
                    if b"End of Data" in buf:
                        time.sleep(0.02)
                        buf += s.read(s.in_waiting or 0) if s.in_waiting else b""
                        break
                elif b"End of Data" in buf:
                    break
        txt = buf.decode("ascii", "replace")
        body = txt.split("End of Header", 1)[-1].split("End of Data", 1)[0] if "End of Header" in txt else ""
        out = []
        for m in _NUM.finditer(body):
            v = float(m.group())
            out.append(math.nan if abs(v) >= OVER else v)
        return out

    def read_stats(self, n=5, period=0.06, autorange=True, low=0.03):
        """n번 읽어 (평균, 표준편차, range, 유효 개수). autorange: 과범위면 올리고, FS의 low 미만이면 내려 다시 읽음.
        (장비 auto-range는 전환 중 FLT_MAX를 내므로 쓰지 않고 소프트웨어로)"""
        import statistics
        import time
        for _ in range(9):
            r = self.range if self.range is not None else 7
            vals = []
            for _ in range(n):
                vals.append(self.read_A())
                time.sleep(period)
            good = [v for v in vals if math.isfinite(v) and abs(v) <= 0.95 * FS[r]]
            if autorange and len(good) < len(vals) and r < 7:
                self.set_range(r + 1)
                continue
            if autorange and good and r > 0 and max(abs(v) for v in good) < low * FS[r]:
                nr = self.range_for(max(abs(v) for v in good))
                if nr < r:
                    self.set_range(nr)
                    continue
            if not good:
                return math.nan, math.nan, r, 0
            sd = statistics.stdev(good) if len(good) > 1 else 0.0
            return statistics.fmean(good), sd, r, len(good)
        return math.nan, math.nan, self.range, 0

    @staticmethod
    def filter_for(rate_hz):
        """판독 간격에 맞춘 디지털 이동평균 점 수 (내부 10 kHz 기준): 10000 / Hz, 1–10000."""
        return int(min(10000, max(1, round(10000.0 / rate_hz))))

    @staticmethod
    def range_for(i_abs, headroom=1.15, fill=0.9):
        """i_abs를 여유(headroom) 두고 담을 수 있는 가장 낮은 range."""
        return next((r for r, fs in enumerate(FS) if i_abs * headroom <= fill * fs), 7)
