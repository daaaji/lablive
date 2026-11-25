"""주기 샘플러: 장비 하나를 스레드 하나가 일정 간격으로 읽어 버퍼와 CSV에 쌓는다.

장비마다 스레드를 따로 두므로 RS-232 왕복 지연(1936-R 약 40 ms, 2400 약 100 ms)이 서로를 막지 않는다.
read_fn이 예외를 내면 error에 기록하고 멈춘다 — 처리(LED OFF 등)는 상위(watchdog)의 몫.
"""
import csv
import threading
import time

import numpy as np


class Sampler(threading.Thread):
    def __init__(self, name, read_fn, period_s, clock, columns, csv_path=None, on_sample=None, stamp="after"):
        """read_fn() -> 값 튜플 (None이면 이번 회차 건너뜀)
        clock() -> 타임스탬프 (예: LED ON 기준 경과 s)
        on_sample(row) -> 행마다 호출 (row = (t, *values))
        stamp: "after" = 응답을 받은 뒤 clock(), "fn" = read_fn이 (t, *값)으로 시각을 직접 돌려줌.
          1936-R은 명령을 받는 순간의 값을 돌려주므로 명령을 보내기 직전 시각(포트 잠금 안에서 잰 것)이
          실제 측정 시각에 가깝고 흔들림이 적다 ("after"에는 응답 전송 + 다른 스레드가 GIL을 쥔 동안
          기다린 시간이 섞임)."""
        super().__init__(name=name, daemon=True)
        self.read_fn, self.period_s, self.clock = read_fn, period_s, clock
        self.columns = ("t_s",) + tuple(columns)
        self.csv_path, self.on_sample = csv_path, on_sample
        self.stamp_fn = stamp == "fn"
        self._rows = []
        self._lock = threading.Lock()
        self._halt = threading.Event()   # Thread._stop과 이름이 겹치지 않게
        self.error = None

    def run(self):
        f = w = None
        if self.csv_path:
            f = open(self.csv_path, "w", newline="", encoding="utf-8")
            w = csv.writer(f)
            w.writerow(self.columns)
        n = 0
        try:
            while not self._halt.is_set():
                t_start = time.perf_counter()
                try:
                    vals = self.read_fn()
                except Exception as e:  # noqa: BLE001
                    self.error = e
                    break
                if vals is not None:
                    row = tuple(vals) if self.stamp_fn else (self.clock(),) + tuple(vals)
                    with self._lock:
                        self._rows.append(row)
                    if self.on_sample:
                        self.on_sample(row)
                    if w:
                        w.writerow(row)
                        n += 1
                        if n % 20 == 0:
                            f.flush()
                rest = self.period_s - (time.perf_counter() - t_start)
                if rest > 0:
                    self._halt.wait(rest)
        finally:
            if f:
                f.close()

    def stop(self, timeout=5.0):
        self._halt.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def snapshot(self):
        """지금까지의 전체 데이터 (n, 1+len(columns)) float 배열."""
        with self._lock:
            rows = list(self._rows)
        if not rows:
            return np.empty((0, len(self.columns)))
        return np.array(rows, dtype=float)

    def last(self):
        with self._lock:
            return self._rows[-1] if self._rows else None
