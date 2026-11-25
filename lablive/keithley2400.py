"""Keithley 2400 (RS-232, 9600 8N1, CR) — LED 정전류 구동용 최소 드라이버.

실측으로 확인한 점:
- 흐름제어가 없어 설정 명령을 연달아 보내면 일부를 놓칠 수 있음
  → 설정 명령은 전부 '명령;*OPC?'로 보내 완료 응답을 받은 뒤 다음으로
- 구동 직전 :SOUR:FUNC? / :SOUR:CURR:LEV?를 다시 읽어 실제 상태를 검증
- 소스 range는 전류에 맞춰 선택 (1 A range 고정이면 1 mA 정확도가 ±0.9 mA 수준 — 사양 0.27% + 900 µA)
"""
import math
import re
import time

import serial

from .serialdev import SerialDevice, find_port, kick, remember_port

_NUM = re.compile(r"[-+]?\d+(?:\.\d*)?(?:[Ee][-+]?\d+)?")


class Keithley2400:
    BAUD = 9600

    def __init__(self, port=None, exclude=()):
        port = port or find_port(self.BAUD, "KEITHLEY", prep=kick, exclude=exclude, role="k", other="pm")
        if not port:
            raise RuntimeError("2400을 찾지 못함 — 전면 MENU→COMMUNICATION이 RS-232인지, 케이블/포트 확인")
        self.port = port
        try:
            self.dev = SerialDevice(port, self.BAUD)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"{port}를 열 수 없음 ({e}) — 다른 프로그램이 사용 중이거나 없는 포트") from e
        self.clear()
        idn = self.idn()
        if "KEITHLEY" not in idn.upper():
            self.dev.close()
            raise RuntimeError(f"{port}의 장비가 2400이 아님 (응답: {idn!r}) — 장비 창에서 포트를 '자동'으로")
        remember_port("k", port)
        self.ws("*CLS")
        self.stale_errors = self.errors()     # 탐색 중 깨진 문자 등으로 남은 오류 (기록용, *CLS 뒤라 보통 비어 있음)
        self.mode = None
        self._rng = None

    def close(self):
        self.dev.close()

    def idn(self):
        return self.dev.query("*IDN?")

    def clear(self):
        """입력 버퍼의 깨진 문자열을 CR로 끝내고 *CLS (오류 큐·상태 정리). 흐름제어가 없어 응답 대기 없이."""
        kick(self.dev)
        self.dev.write("*CLS", 0.15)
        try:
            self.dev.s.reset_input_buffer()
        except (serial.SerialException, OSError):
            pass

    def ws(self, cmd, pause=0.0):
        """명령 + *OPC? → 실행 완료 응답('1')까지 대기. 반환: 완료 확인 여부."""
        with self.dev.lock:
            self.dev.s.reset_input_buffer()
            self.dev.s.write((cmd + ";*OPC?" + self.dev.term).encode("ascii"))
            ok = self.dev._read_line().decode("ascii", "replace").strip().endswith("1")
        if pause:
            time.sleep(pause)
        return ok

    def errors(self, limit=20):
        out = []
        for _ in range(limit):
            e = self.dev.query(":SYST:ERR?")
            if not e or e.lstrip("+").startswith("0"):
                break
            out.append(e)
        return out

    # ---- 정전류 소스 ----
    def setup_current_source(self, compliance_v, rsense=False, nplc=1.0):
        """반환: (완료 확인 실패한 명령 목록, 오류 큐)"""
        vr = 20 if compliance_v <= 21 else 200
        self.clear()
        cmds = ["*CLS", ":OUTP OFF", f":SYST:RSEN {'ON' if rsense else 'OFF'}",
                ":SOUR:FUNC CURR", ":SOUR:CURR:MODE FIX", ":SOUR:CURR:RANG:AUTO OFF",
                ":SOUR:CURR:LEV 0", ":SOUR:CURR:RANG 1e-3",
                ":SENS:FUNC:CONC OFF", ':SENS:FUNC "VOLT"', f":SENS:VOLT:PROT {compliance_v:g}",
                f":SENS:VOLT:RANG {vr}", f":SENS:VOLT:NPLC {nplc:g}", ":FORM:ELEM VOLT,CURR"]
        failed = [c for c in cmds if not self.ws(c)]
        self.mode, self._rng = "I", 1e-3
        return failed, self.errors()

    def set_current(self, amps, auto_range=True):
        """auto_range: 소스 range를 |amps|를 담는 가장 작은 range로 (1µ/10µ/100µ/1m/10m/100m/1 A).
        올라갈 때는 range → level, 내려갈 때는 level → range 순서 (level이 range를 넘는 순간이 없게)."""
        if not auto_range:
            return self.ws(f":SOUR:CURR:LEV {amps:.6g}")
        rng = next((r for r in (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0) if abs(amps) <= 1.05 * r), 1.0)
        if self._rng is None or rng >= self._rng:
            ok = self.ws(f":SOUR:CURR:RANG {rng:g}") and self.ws(f":SOUR:CURR:LEV {amps:.6g}")
        else:
            ok = self.ws(f":SOUR:CURR:LEV {amps:.6g}") and self.ws(f":SOUR:CURR:RANG {rng:g}")
        self._rng = rng
        return ok

    # ---- 정전압 소스 (I-V 스윕용) ----
    def setup_voltage_source(self, ilim, rsense=False, nplc=1.0, vrange=20):
        """V 소스, 전류 측정(auto range), 전류 compliance = ilim. 반환: (미확인 명령, 오류 큐)"""
        self.clear()
        cmds = ["*CLS", ":OUTP OFF", f":SYST:RSEN {'ON' if rsense else 'OFF'}",
                ":SOUR:FUNC VOLT", ":SOUR:VOLT:MODE FIX", f":SOUR:VOLT:RANG {vrange:g}", ":SOUR:VOLT:LEV 0",
                ":SENS:FUNC:CONC ON", ':SENS:FUNC "VOLT","CURR"', f":SENS:CURR:PROT {ilim:g}",
                ":SENS:CURR:RANG:AUTO ON", f":SENS:CURR:NPLC {nplc:g}", ":FORM:ELEM VOLT,CURR"]
        failed = [c for c in cmds if not self.ws(c)]
        self.mode = "V"
        return failed, self.errors()

    def set_voltage(self, volts):
        return self.ws(f":SOUR:VOLT:LEV {volts:.6g}")

    def output(self, on):
        return self.ws(":OUTP ON" if on else ":OUTP OFF")

    def verify_current(self, amps):
        func = self.dev.query(":SOUR:FUNC?").strip('"')
        lev = self.dev.query(":SOUR:CURR:LEV?")
        try:
            ok = func.startswith("CURR") and abs(float(lev) - amps) <= 1e-6 + 1e-4 * abs(amps)
        except ValueError:
            ok = False
        return ok, func, lev

    # ---- 측정 ----
    def read(self):
        """(단자 전압 V, 소스 전류 A)"""
        v = [float(x) for x in _NUM.findall(self.dev.query(":READ?"))]
        return (v[0], v[1]) if len(v) >= 2 else (math.nan, math.nan)

    def tripped(self):
        """compliance에 걸려 요청값을 못 내는 중인지.
        정전류 모드 → 전압 compliance(:SENS:VOLT:PROT:TRIP?), 정전압 모드 → 전류 compliance."""
        q = ":SENS:CURR:PROT:TRIP?" if self.mode == "V" else ":SENS:VOLT:PROT:TRIP?"
        return self.dev.query(q).strip().endswith("1")

    def output_is_on(self):
        return self.dev.query(":OUTP?").strip().endswith("1")

    def safe_off(self, tries=10):
        """OUTPUT OFF를 반드시 전달하고 :OUTP?로 확인. 포트가 죽었으면 다시 열어 재시도."""
        for _ in range(tries):
            try:
                self.dev.write(":OUTP OFF", 0.05)
                self.dev.write(":SOUR:VOLT:LEV 0" if self.mode == "V" else ":SOUR:CURR:LEV 0", 0.05)
                if self.dev.query(":OUTP?").strip().endswith("0"):
                    return True
            except (serial.SerialException, OSError):
                pass
            try:
                self.dev.reopen()
            except (serial.SerialException, OSError):
                time.sleep(1.0)
        return False
