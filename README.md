# lablive 0.9.0b5 (beta)

Keithley 2400(정전류 LED 구동) + Newport 1936-R(광전류)로 LED를 켜고, 광출력이 안정 범위에 들어왔는지 실시간으로 판정한다.
LED마다 프로파일(전류 상한, 전압 상한, 경고 전압, 전류별 기준 열 모델, 측정 회차)을 두고 회차 간 변화를 비교한다.

## 설치 — 설치 파일 (권장)

`lablive_Setup_0.9.0b5.exe` 실행 → 다음 → 설치 → 마침. Python 설치 불필요 (64비트 Windows 10/11).
- 서명 없는 프로그램이라 "Windows의 PC 보호" 창이 뜨면 `추가 정보` → `실행`
- 설치 위치 `C:\Program Files\lablive` (관리자 권한). 이전 버전 위에 그대로 설치하면 업데이트
- 시작 메뉴 `lablive` 폴더: lablive(측정 창), 명령 창(led-iv/led-profile 등), 시뮬레이터(장비 없이 연습),
  자가 진단(selftest), 비상 - 2400 출력 끄기, 사용 설명, 제거. 바탕 화면 바로가기(선택)
- 처음 실행 때 `C:\code\control\profiles`가 있으면 그 폴더를 데이터 폴더로, 없으면 `%USERPROFILE%\lablive_data`.
  측정 창의 `경로`에서 변경
- 제거해도 측정 데이터와 설정(`%USERPROFILE%\.lablive.json`)은 남음
- 창 모드 출력/오류: `%LOCALAPPDATA%\lablive\lablive_gui.log`, 예외 기록은 `<데이터 폴더>\logs`
- 설치 파일 다시 만들기(코드 수정 후): `packaging\build_windows.bat` (Python 3.12 + NSIS 3 필요)

## 설치 — 소스에서 (개발용)

1. Python 3.9 이상 (python.org 설치본 권장 — 실시간 그래프 창에 쓰는 tkinter 포함). 설치할 때 "Add python.exe to PATH" 체크
2. 이 폴더를 경로에 괄호/한글이 없는 곳에 둔다 (예: `C:\lablive\lablive-0.9.0b1`)
3. `install.bat` 더블클릭
   - `.venv` 가상환경을 만들고 pyserial, numpy, matplotlib과 lablive를 설치
   - 데이터 폴더를 묻는다. 기본값은 `%USERPROFILE%\lablive_data`, `C:\code\control\profiles`가 있으면 `C:\code\control`
     → 기존 uvc275 프로파일과 회차를 그대로 이어 쓰려면 `C:\code\control`
   - 마지막에 selftest(시뮬레이터, 약 1분)가 돈다. "모두 통과"가 나와야 함 (그래프 창 WARN은 tkinter가 없을 때만)
4. 이후에는 `lablive_gui.bat` 더블클릭 → 측정 창, 또는 `lablive_shell.bat` → 명령 창
   (데이터 폴더는 `%USERPROFILE%\.lablive.json`에 저장됨. 측정 창의 "경로"에서 바꿀 수 있음)

**인터넷이 없는 PC:** 같은 Python 버전의 다른 PC에서
`pip download -d wheels pyserial numpy matplotlib "setuptools>=64" wheel`
로 받은 `wheels` 폴더를 이 폴더에 넣고 install.bat 실행 (wheels 폴더가 있으면 오프라인 설치)

**업데이트:** 새 버전 폴더에서 install.bat 다시 실행 (데이터 폴더는 그대로 사용)

## 명령

| 명령 | 하는 일 | 장비 |
|---|---|---|
| `led-gui` | 측정 창: 설정 → 시작 → 실시간 판정/전류 조정 → 정지 → 요약 | LED 켬 |
| `led-check` | 포트 자동 탐색, *IDN?, 1936-R 설정, 오류 큐, 2400 출력 상태 | LED 안 켬 |
| `led-check --off` | 2400 OUTPUT OFF (확인까지 재시도) | 비상용 |
| `led-check --scan` | 모든 COM 포트에 무엇이 붙어 있는지 (어댑터 이름, *IDN?) | LED 안 켬 |
| `led-check --pm-restore` | 1936-R을 평소 설정으로 복구 (`--pm-save-default`로 지금 설정을 평소 설정으로 저장) | |
| `led-profile new/set/show/list/add/link/history` | 프로파일 관리 | 불필요 |
| `led-iv --profile X` | I-V-L 스윕 (문턱, 발진 구간, 효율, 동저항) | LED 켬 |
| `led-iv --unknown` | 정보 없는 LED 안전 탐색 → 제안 i_max/compliance, 프로파일 생성 | LED 켬 |
| `led-live --profile X --current I` | 정전류 구동 + 실시간 안정 판정 | LED 켬 |
| `lablive-selftest` | 시뮬레이터로 전체 흐름 확인 | 불필요 |

모든 명령에 `-h`(도움말), `--sim`(시뮬레이터; 데이터는 `<home>\sim`에 따로), `--home <폴더>`(데이터 폴더 지정).
포트는 매번 `*IDN?`으로 찾는다 (USB-시리얼 어댑터에 시리얼 번호가 없어 COM 번호가 꽂는 위치를 따라 바뀜).

## 측정 창 (led-gui, 그리고 led-live의 그래프 창)

도구 막대: `▶ 시작/■ 정지` `LED 설정` `경로` `장비` | `전류 [ ] mA [적용]` `단계 [ ] ▲ ▼` | `색` `데이터` `PNG 저장` `요약`

- **LED 설정**: 프로파일 선택(값 자동 채움), 전류 상한, 전압 상한, 경고 전압, λ, tol, margin, 판정 창,
  1936-R range/읽기 속도, 자동 종료, 4-wire, 메모. `프로파일에 저장`(상한을 올릴 때는 확인), `새 프로파일...`
  - 측정 중 바로 적용되는 것: 전압 상한(현재 단자 V + 0.3 V 이상만), 경고 전압, tol, margin, 메모, 전류 상한 낮추기.
    나머지는 회색 (다음 측정부터)
- **장비**: 1936-R/2400 포트. 기본 `자동`(시작할 때마다 *IDN?으로 탐색, 지난번 포트 먼저). `포트 찾기`로 각 COM 포트에
  붙은 어댑터와 *IDN? 응답을 표로 확인, 필요하면 고정 (고정 포트도 *IDN?으로 맞는 장비인지 확인)
- **경로**: 데이터 폴더(home)와 회차 저장 폴더(비우면 `<home>\runs`, 공유 드라이브 등 따로 가능). `기본값`, `찾아보기`, `열기`
- **전류 (측정 중)**: 값 입력 후 적용 또는 ▲▼(단계만큼). 확인 후 LED를 켠 채 바꿈
  - 바꾼 시각부터 안정도 판정(최종값 피팅)을 새로 시작. 프로파일 기준 곡선은 쓰지 않음 (냉간 시작이 아니므로)
  - 그래프에 세로 점선으로 표시, meta.json `segments`에 기록
  - 회차 비교(history)와 기준 모델 등록에는 **첫 전류 구간만** 쓰임
  - 2400 소스 range가 바뀌는 전류(예: 100 → 120 mA는 100 mA → 1 A range)는 순간적인 전환이 있을 수 있음
- **색**: 광전류, 피팅/예측 최종값, 프로파일 곡선, ±tol 밴드, 편차, 전압, 경고선, 전류 변경 표시, 배경. 저장하면 다음 실행에도
- **데이터**: 광전류(pd.csv)/2400(k.csv)/이벤트 표, 스크롤, 실시간 추가(최신 행 따라가기). `CSV 열기...`로 다른 회차 파일도
- **요약**: 정지 후 요약 (회차 폴더 열기)
- 창을 닫으면 LED OFF 확인 후 종료. 측정 중 FAULT면 자동 정지 + 알림
- led-gui는 마지막 설정(프로파일, 전류 등)을 기억함

## 아무 정보도 없는 LED

```
led-iv --unknown                                   10 µA → 20 mA, 6점/decade, 점당 0.3 s, 전력 ≤ 150 mW
led-iv --unknown --save-profile blue2              결과로 프로파일까지 생성 (λ는 비워 둠)
led-iv --unknown --ceiling 0.05 --p-max 0.3        상한을 올려 다시 (보통 2배씩)
```
- 정지 조건 (하나라도 걸리면 그 점에서 멈춤)
  - 다음 점의 예상 전력 V·I > `--p-max` (점에 들어가기 전에 판단)
  - dwell 동안 단자 V가 50 mV 넘게 떨어짐: 정전류에서 V 하강 = 접합 온도 상승(약 −2 mV/K), 급가열
  - 광전류/전류(상대 효율)가 최고치의 75% 미만: droop/발열 영역
  - 전류를 올렸는데 광출력이 줄어듦: 열 roll-over
  - 전압 상한(기본 12 V) 도달: 첫 점부터면 극성 반대/개방/Vf가 12 V 넘는 직렬 소자
- 출력
  - 발광 시작 전류, V @ 1 mA·10 mA, `V = a + n·kT/q·ln I + Rs·I` 피팅의 n, Rs
    (n < 1이면 단일 다이오드 아님 → 문턱 소자/드라이버 내장 모듈 의심. uvc275가 이 경우)
  - Vf로 본 계열 추정(참고만), 발진 의심 구간
  - 제안 i_max: 효율 최고점 위에서 효율 ≥ 최고치 85%이고 전력 ≤ p_max인 최대 시험 전류 (보수적. 정격이 아님)
  - 제안 compliance: 관측 최대 V + 1.5 V
- 효율 저하 감시는 '안정적으로 발광한 점'이 3개 이상이어야 작동. 발진하는 모듈처럼 안정 점이 적으면 경고가 나옴
- 광출력 기준이라 검출기에 빛이 들어가야 의미가 있음 (정렬 먼저). 파장은 분광으로 확인해서 `led-profile set X --lambda`
- 시뮬레이터 연습: `set LABLIVE_SIM_LED=bare` 후 `led-iv --sim --unknown` (bare InGaN 모델), 기본은 uvc275 모듈 모델

## 새 LED 측정 순서 (정격을 아는 경우)

```
0) 정격 확인: 파장, 최대 전류, bare LED인지 드라이버 내장 모듈인지, 예상 Vf
1) led-profile new newled --lambda 365 --i-max 0.05 --desc "365 nm bare, 고정대 v1"
       --i-max는 정격 이하로 (이후 모든 명령이 이 값을 넘는 전류를 거부)
       --compliance 기본 12 V. bare LED면 Vf + 1~2 V 정도로 낮춰 두면 더 안전
2) led-check                                  두 장비가 보이는지
3) led-iv --profile newled                    0.1 mA → i_max, log 25점, 점당 0.3 s
       iv.png: (a) V-I  (b) L-I  (c) 상대 효율  (d) 점 내 광출력 표준편차 (발진 검출)
       불안정 구간이 있으면 경고 전압 제안이 나옴 → led-profile set newled --vf-guard <V>
       드라이버 내장이 의심되면: led-iv --profile newled --mode voltage --vstop <V> --ilim <A>
4) led-live --profile newled --current <A>    냉간 시작(최소 30분 이상 꺼 둔 상태), 충분히 길게
5) led-profile add newled <회차폴더>          이 회차를 그 전류의 기준 모델로
6) 이후 회차: led-live --profile newled --current <A> --note "..."
              led-profile history newled
```

기준 모델이 생기면 다음 회차부터 시작하자마자 최종값과 도달 시각을 예측한다
(시작 전압으로 냉간/데워진 시작 구분 → 다르면 기준 곡선을 쓰지 않고 자유 피팅).

## 안전 동작

- 전류 상한: 프로파일의 `i_max_A` (명령에서 더 낮게만 지정 가능). 프로파일 없이(`--no-profile`) 쓸 때는 `--i-max` 필수
- 시작 전 극성 점검: 1 mA(목표 전류가 더 작으면 그 값)로 0.3 s
  - 전압 상한에 걸림 → 극성 반대 / 개방 / 접촉 불량 / compliance가 Vf보다 낮음 → 중단
  - 0.5 V 미만 → 단락 의심 → 중단
  - 프로파일에 기록된 점검 전압과 ±0.3 V 넘게 다름 → 다른 LED가 연결됐거나 배선 문제로 보고 중단
    (같은 LED가 확실하면 `led-live ... --reset-check` 또는 `led-profile set X --clear-check`)
  - 프로파일의 첫 측정 때 점검 전압이 자동 기록됨
- 구동 직전 2400 상태(FUNC, LEVEL)를 다시 읽어 확인
- 측정 중 compliance trip / 통신 끊김 → 즉시 LED OFF, FAULT
- 어떤 경로로 끝나든 OUTPUT OFF를 `:OUTP?`로 확인, 1936-R 설정은 시작 전 상태로 원복
- 예상 못 한 오류는 `<home>\logs\`에 기록 → 그 파일을 공유

## 데이터 폴더

```
<home>\profiles\<이름>.json        프로파일 (회차 경로는 home 기준 상대경로 → 폴더째 옮겨도 동작)
<home>\profiles\<이름>_history.png  회차 비교
<home>\runs\<시각>_live_<프로파일>_<mA>mA\  pd.csv k.csv meta.json final.png console.log summary.txt
<home>\runs\<시각>_iv_<프로파일>_<mode>\    iv.csv iv.png meta.json console.log
<home>\logs\                       오류 기록
<home>\pm_default.json             1936-R 평소 설정 (led-check --pm-save-default)
```

## v3(이전 스크립트)와 달라진 점

- 설치형 패키지. `python led_live.py` 대신 `led-live`, 실행 위치와 상관없이 데이터는 항상 `<home>`
- 프로파일 필수. 전류 상한 기본값(0.25 A), 극성 점검 하한 2.0 V, 경고 전압 6.30 V 같은 uvc275 전용 기본값 제거
  → uvc275 프로파일에 `vf_guard_V` 6.30이 들어 있으면 동작은 같음 (`led-profile show uvc275`로 확인, 없으면
    `led-profile set uvc275 --vf-guard 6.30`)
- 2400 소스 range를 전류에 맞춰 선택 (이전: 1 A range 고정 → 1 mA 점검의 정확도가 사양상 ±0.9 mA였음).
  100 mA는 100 mA range, 250 mA는 1 A range. **처음 실측 때 1 mA 점검 전압이 이전(6.24 V 근처)과 달라도 정상**
- led-iv 추가 (정전류 스윕 기본, 1936-R 필터 없이 읽어 kHz 발진을 표준편차로 검출)
- led-check 추가 (1936reset.py 대체)

## 알려진 한계 (beta)

- led-iv의 발진 판정 기준(점 내 표준편차 2%)은 시뮬레이터와 uvc275 경험값 기준. 1936-R을 필터 없이 읽으므로
  신호가 range FS의 몇 % 수준이면 장비 잡음만으로 넘을 수 있음 → iv.png (d)를 직접 볼 것
- 10 kHz 고속 캡처(`extras\led_settle.py`)는 아직 패키지에 안 들어감. 단독 스크립트, 전류 상한 0.25 A와 uvc275 기준값이 코드에 고정
- 시뮬레이터는 열 시정수를 압축한 uvc275 흉내 (τ 0.4 s, 4 s). 판정/피팅 파라미터도 짧게 바뀜
- 그림 안 고정 문구는 영어. 한글 글꼴(맑은 고딕 등)이 있으면 글자 단위 fallback으로 같이 씀 (matplotlib ≥ 3.6)

## 통합 모듈에서 쓰기

```python
from lablive import Newport1936, Keithley2400, LedSession, Profile
prof = Profile.load("uvc275")
pm = Newport1936(); k = Keithley2400(exclude=(pm.port,))
with LedSession(pm, k, 0.1, prof["compliance_V"], i_max=prof["i_max_A"], profile=prof,
                vf_guard=prof.get("vf_guard_V"), log=print) as s:
    if s.wait_stable(timeout=1800):
        ...   # LED 켜진 채 다른 측정
```
