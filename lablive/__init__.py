"""lablive — 연구실 계측 모듈 (RS-232 장비 드라이버 + 샘플링 + 안정도 판정 + 실시간 화면).

계층 (아래로 갈수록 장비와 무관):
  serialdev     포트 탐색, lock 걸린 write/query, 재연결
  newport1936   1936-R 드라이버 (광전류)
  keithley2400  2400 드라이버 (정전류 구동, *OPC? 동기화)
  sampler       장비 1대 = 스레드 1개 주기 샘플링 → 버퍼 + CSV
  stability     (t, y)만 받는 안정도 판정 (window drift + 다중지수 최종값 예측)
  thermfit      다중 지수 피팅 / settling 외삽
  led_session   위를 묶은 LED 구동 세션 (안전 점검, watchdog, 원복)
  liveplot      세션을 읽어 그리는 실시간 화면 (matplotlib)
  profiles      광원 프로파일 (장비 설정 + 전류별 기준 열 모델 + 회차 목록) JSON
  runfit        회차 폴더(live/settle 형식) → 시계열 + 열 모델 요약 (runfit.json 캐시)
  history       한 프로파일의 회차 비교 표/그림
  ivsweep       I-V-L 스윕 (새 LED 특성 파악, 발진 구간 검출)
  config        데이터 폴더 (LABLIVE_HOME)
  sim           장비 없는 시뮬레이터 (--sim)
  cli/          명령: led-live, led-iv, led-profile, led-check, lablive-selftest
"""
__version__ = "0.9.0b6"

from .keithley2400 import Keithley2400
from .led_session import LedSession, fmt_t, status_line
from .newport1936 import BW, FS, Newport1936
from .sampler import Sampler
from .profiles import Profile
from .stability import StabilityTracker

__all__ = ["Keithley2400", "Newport1936", "LedSession", "Sampler", "StabilityTracker", "Profile",
           "status_line", "fmt_t", "FS", "BW"]
