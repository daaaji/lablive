"""RS-232 공통 계층: 포트 탐색, 잠금(lock)으로 보호된 write/query, 재연결.

장비 드라이버(newport1936, keithley2400)는 이 위에 명령만 얹는다.
한 장비를 여러 스레드가 만질 수 있으므로(샘플러 + watchdog) 모든 왕복은 lock 안에서 수행.
"""
import threading
import time

import serial
from serial.tools import list_ports


class SerialDevice:
    def __init__(self, port, baud, term="\r", eol=b"\r", timeout=2.0):
        self.port, self.baud, self.term, self.eol, self.timeout = port, baud, term, eol, timeout
        self.lock = threading.RLock()
        self.s = None
        self.open()

    def open(self):
        self.s = serial.Serial(self.port, self.baud, bytesize=8, parity="N", stopbits=1,
                               xonxoff=False, rtscts=False, dsrdtr=False, timeout=self.timeout)

    def close(self):
        try:
            if self.s:
                self.s.close()
        except Exception:  # noqa: BLE001
            pass

    def reopen(self):
        """USB-serial 어댑터가 재열거된 뒤 같은 이름으로 다시 연다."""
        with self.lock:
            self.close()
            time.sleep(0.5)
            self.open()

    def write(self, cmd, pause=0.03):
        with self.lock:
            self.s.write((cmd + self.term).encode("ascii"))
        if pause:
            time.sleep(pause)

    def query(self, cmd):
        with self.lock:
            self.s.reset_input_buffer()
            self.s.write((cmd + self.term).encode("ascii"))
            return self.s.read_until(self.eol).decode("ascii", "replace").strip()


def list_ports_info():
    """[(포트, 설명)] — 설명은 USB-시리얼 어댑터 이름 등 (없으면 '')."""
    out = []
    for p in list_ports.comports():
        out.append((p.device, getattr(p, "description", "") or ""))
    return sorted(out, key=lambda x: (len(x[0]), x[0]))


def scan_instruments(probes, ports=None, log=None, roles=None):
    """각 포트에 probes = [(이름, baud, idn_key, prep)] 순서로 *IDN?을 보내 응답을 모은다.
    반환: [{"port", "desc", "kind"(이름 또는 None), "idn"}]. 포트를 잠깐 열었다 닫으므로 측정 중에는 쓰지 말 것."""
    res = []
    lp = _last_ports()
    for port, desc in (ports or list_ports_info()):
        hit = {"port": port, "desc": desc, "kind": None, "idn": ""}
        # 지난번 이 포트에 있던 장비의 baud부터 (다른 baud의 깨진 문자를 덜 보내게)
        order = sorted(probes, key=lambda pr: 0 if roles and lp.get(roles.get(pr[0])) == port else 1)
        for probe in order:
            name, baud, key, prep = probe[:4]
            post = probe[4] if len(probe) > 4 else None
            if log:
                log(f"{port} @ {baud} …")
            try:
                d = SerialDevice(port, baud, timeout=0.6)
            except (serial.SerialException, OSError) as e:
                hit["idn"] = f"(열 수 없음: {e})"
                break
            try:
                if prep:
                    prep(d)
                idn = d.query("*IDN?")
                if key in idn.upper() and post:
                    post(d)
            except (serial.SerialException, OSError):
                idn = ""
            finally:
                d.close()
            if key in idn.upper():
                hit.update(kind=name, idn=idn)
                break
        res.append(hit)
    return res


def _last_ports():
    try:
        from . import config
        return config.load_user().get("last_ports") or {}
    except Exception:  # noqa: BLE001
        return {}


def remember_port(role, port):
    """찾은 포트를 기억 (다음 탐색 때 먼저 시도). 시뮬레이터 포트는 기록 안 함."""
    if not port or str(port).startswith("SIM"):
        return
    try:
        from . import config
        lp = dict(_last_ports())
        if lp.get(role) != port:
            lp[role] = port
            config.save_user(last_ports=lp)
    except Exception:  # noqa: BLE001
        pass


def find_port(baud, idn_key, prep=None, exclude=(), term="\r", eol=b"\r", role=None, other=None):
    """COM 포트에 *IDN?을 보내 idn_key가 들어 있는 포트 이름을 반환 (없으면 None).
    어댑터 시리얼 번호가 비어 있어 COM 번호가 꽂는 위치에 따라 바뀌므로 하드코딩하지 않는다.
    순서: 지난번 이 장비(role)가 있던 포트 → 나머지 → 지난번 다른 장비(other)가 있던 포트.
    (다른 baud로 보낸 *IDN?은 상대 장비에 깨진 문자로 들어가 -101 Invalid character 등을 남기므로
     남의 포트는 최대한 건드리지 않는다)"""
    lp = _last_ports()
    mine, theirs = lp.get(role), lp.get(other)
    ports = [p.device for p in list_ports.comports() if p.device not in exclude]
    ports.sort(key=lambda p: (0 if p == mine else 2 if p == theirs else 1))
    for port in ports:
        try:
            d = SerialDevice(port, baud, term, eol, timeout=0.6)
        except (serial.SerialException, OSError):
            continue
        try:
            if prep:
                prep(d)
            if idn_key in d.query("*IDN?").upper():
                return port
        except (serial.SerialException, OSError):
            pass
        finally:
            d.close()
    return None


def kick(dev, settle=0.1):
    """다른 baud의 탐색 등으로 장비 입력 버퍼에 남은 깨진 문자열을 CR로 끝내고(그 줄은 오류로 처리됨) 응답을 버린다."""
    dev.write("", settle)
    try:
        dev.s.reset_input_buffer()
    except (serial.SerialException, OSError):
        pass
