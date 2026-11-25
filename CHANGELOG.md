# Changelog

## 0.9.0b6 — 2026-11-25
- 1936-R 디지털 필터 설정 (LED 설정 '1936-R 디지털 필터', `--pd-filter`). 기본 auto = 판독 간격에 맞춤 (10000 ÷ Hz점)
  — 이전에는 500점(50 ms) 고정이라 판독 Hz를 바꾸면 필터와 간격이 어긋났음. 실제 설정값을 읽어 meta.json에 기록
- `led-check --speed`: 1936-R / 2400 통신·판독 속도 측정 (2400은 0 A, 전압 상한 2 V로 잠깐 출력 ON)
- 측정 창: 1936-R 읽기 ≤ 50 Hz, 2400 읽기 ≤ 10 Hz 입력 제한, 정보 막대에 판독 Hz/필터 ms
- README: 장비 명령 목록, 판독 간격·필터·속도 한계, 트러블슈팅 기록
- 설치 파일 b6: 빌드용 wine이 동작하지 않아, b5 실행 파일의 PYZ 안 lablive 모듈만 새 소스로 교체해 만듦
  (`packaging/repyz.py`; 새 모듈이 생기거나 의존 패키지가 바뀌면 이 방법은 안 되고 정식 빌드 필요). 파일 속성 버전 표시는 0.9.0.5로 남음

## 0.9.0b5 — 2026-11-12
- 2400 "-101 Invalid character" 수정: 포트 자동 탐색이 1936-R을 찾으며 2400 포트에 38400 baud로 *IDN?을 보내면
  2400 입력 버퍼에 깨진 문자열이 남아 다음 명령이 오염됨 (2400이 1936-R보다 앞 COM 번호일 때)
  - 탐색 순서: 지난번 찾은 포트 먼저, 다른 장비가 있던 포트는 맨 뒤 (~/.lablive.json last_ports)
  - 2400 열 때/설정 전 CR + *CLS로 버퍼·오류 큐 정리 (clear), 탐색 시에도 CR로 먼저 정리
- 측정 창 '장비' 버튼: 포트 표시/선택 (기본 자동), [포트 찾기]로 각 COM 포트의 어댑터·*IDN? 표. 고정 포트는 IDN 확인
- led-check --scan
- 시뮬레이터: 2400을 앞 번호로, 다른 baud 문자열 → -101 재현

## 0.9.0b4 — 2026-11-12
- Windows 설치 파일 lablive_Setup_0.9.0b4.exe (PyInstaller onedir: lablive.exe 창 + lablive-cli.exe 명령, NSIS 설치/제거,
  시작 메뉴: 측정 창·명령 창·시뮬레이터·자가 진단·비상 OFF). packaging/ (spec, installer.nsi, build_windows.bat, 아이콘)
- 처음 실행 시 C:\code\control\profiles가 있으면 그 폴더를 데이터 폴더로 (config.first_run)
- 창 모드 exe의 stdout/stderr → %LOCALAPPDATA%\lablive\lablive_gui.log
- 번들 numpy 2.1.3 (빌드 환경 사정, 기능 차이 없음)

## 0.9.0b3 — 2026-11-12
- 측정 창 led-gui (tkinter + matplotlib): LED 설정 창, 경로 창, 실시간 전류 조정(▲▼), 그래프 색 설정, 데이터 표 창(스크롤,
  실시간, CSV 열기), 요약 창. led-live 그래프 창도 같은 창 사용 (tkinter 없으면 이전 matplotlib 창)
- LedSession: set_current_live (구간별 안정도 판정 재시작, segments 기록), set_compliance_live, set_vf_guard, set_tol, set_note
- runfit: 전류를 바꾼 회차는 첫 구간만 (회차 비교/기준 모델)
- 사용자 설정 파일 ~/.lablive.json (home, runs, colors, last). home 우선순위: --home > 설정 파일 > LABLIVE_HOME > 기본
  install.bat은 setx 대신 설정 파일에 기록, lablive_gui.bat 생성
- 프로파일 회차 경로: home 밖(다른 드라이브)이면 절대경로
- 한글 글꼴 fallback 수정: 일반 이름('sans-serif')으로는 fallback이 안 돼서 실제 글꼴 이름 목록으로, 상태 패널(고정폭)도
- selftest: 측정 창 시험 추가

## 0.9.0b2 — 2026-11-12
- 실시간 그림 상태 패널의 한글 깨짐 수정: 프로파일 모델 상태 문구 영어화 + 그림 글꼴 fallback (DejaVu Sans → 맑은 고딕 등, plotstyle.py)
- led-iv --unknown: 정보 없는 LED 탐색 (UnknownGuard: 전력 예측, dwell 중 V 급강하, 효율 < 75%, roll-over에서 정지),
  n/Rs 피팅, 제안 i_max/compliance, --save-profile
- led-iv: dark 잡음 수준 신호는 '불안정' 판정에서 제외, iv.csv 열 합집합
- 시뮬레이터: LABLIVE_SIM_LED=bare (단일 접합 InGaN 모델, SRH 상승 + droop)
- selftest: 글꼴, 미상 LED 탐색 추가
- matplotlib ≥ 3.6

## 0.9.0b1 — 2026-11-12
- 설치형 패키지 (pyproject, install.bat, lablive_shell.bat). 명령: led-live, led-iv, led-profile, led-check, lablive-selftest
- 데이터 폴더 고정: LABLIVE_HOME (기본 ~/lablive_data), 모든 명령 --home
- 새 LED 대응
  - 프로파일 필수 (또는 --no-profile --i-max). led-profile new는 --lambda, --i-max 필수. led-profile set 추가
  - LedSession: i_max 필수 인자, vf_guard 기본 None, λ 기본 None (단위 A면 무관)
  - 극성 점검 일반화: 하한 0.5 V(단락), 상한 = compliance trip. 프로파일에 점검 전압 기록, ±0.3 V 넘게 다르면 중단
- Keithley2400: 소스 range 자동 선택 (range↑는 range→level, range↓는 level→range 순서), 정전압 소스, 모드별 trip 질의
- Newport1936: configure_current(digital_filter=0) = 필터 없음, read_stats(소프트웨어 auto-range + 평균/표준편차)
- ivsweep / led-iv: 정전류(기본)·정전압 스윕, dark 보정, 점별 광출력 표준편차로 발진 구간 검출, 경고 전압 제안, iv.png 4패널
- sim: 시뮬레이터 (--sim). 정전압 모드, 필터 유무에 따른 발진 진폭, 열 기억
- 오류 기록 <home>/logs, 회차별 console.log
- 회차 폴더 이름에 프로파일 이름 포함

## v3 — 2026-11-06 (스크립트 묶음)
- profiles, runfit, history, led_profile.py. 시작 전압으로 냉간/데워진 시작 판별, 모양 RMS 검사
- SETTLED 여유 0.5×tol 기본
