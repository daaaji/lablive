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

## 장비 명령 (RS-232)

lablive가 실제로 보내는 명령만 정리했다. 확인한 장비: 1936-R 펌웨어 v1.2.4, 2400 펌웨어 C34.

### 통신 규칙

| | Newport 1936-R | Keithley 2400 |
|---|---|---|
| 포트 설정 | 38400 8N1 (고정) | 9600 8N1, 흐름제어 없음 (앞면 메뉴 COMMUNICATION → RS-232) |
| 명령 끝 / 응답 끝 | CR / CRLF | CR / CR |
| 시작할 때 | `ECHO 0` (에코가 켜져 있으면 응답 앞에 명령이 되돌아옴) | CR 한 번 + `*CLS` (입력 버퍼에 남은 깨진 문자열 정리) |
| 설정 명령 | 그대로 보내고 다시 읽어 확인 | `명령;*OPC?` → `1`이 올 때까지 대기 (연달아 보내면 명령이 사라짐) |
| 오류 큐 | `ERRSTR?` (비워질 때까지) | `:SYST:ERR?` (`0,"No error"`까지) |

### Newport 1936-R

| 명령 | 의미 | 비고 |
|---|---|---|
| `*IDN?` | 장비 식별 | 포트 자동 탐색 (`NEWPORT` 포함 여부) |
| `ECHO 0` | 명령 에코 끄기 | |
| `ERRSTR?` | 오류 문자열 하나 읽기 | |
| `PM:MODE 0` | CW continuous | |
| `PM:UNITs 0` | 단위 A (광전류) | 3 = W/cm² (이 연구실 평소 설정) |
| `PM:Lambda <nm>` | 파장 | 단위가 A면 값에 영향 없음 (W 환산용) |
| `PM:AUTO 0` / `1` | auto range 끔 / 켬 | 측정 중에는 0 (아래 `PM:P?` 참고) |
| `PM:RANge <r>` | 전류 range, r = 0–7 | 최대 2.5 nA × 10ʳ (r7 = 25 mA), 대역 1.2 Hz · 4.8 Hz · 48 Hz · 480 Hz · 4 kHz · 43 kHz · 170 kHz · 400 kHz, 분해능 ≈ 최대/32768 |
| `PM:FILTer <n>` | 0 없음 · 1 아날로그 · 2 디지털 · 3 둘 다 | **`PM:DIGITALFILTER`를 100으로 되돌린다 → 반드시 먼저 보낼 것** |
| `PM:ANALOGFILTER <n>` | 아날로그 필터 | 측정은 0(끔), 평소 설정 4 |
| `PM:DIGITALFILTER <n>` | 디지털 이동평균 점 수 (내부 10 kHz 기준) | 500 = 50 ms (20 Hz 판독의 anti-alias). 원격으로 10000까지 설정됨 |
| `PM:P?` | 현재 값 | 과범위나 range 전환 중에는 `3.402823E+38` (FLT_MAX) → 무시하고 range를 올림 |
| `PM:DS:ENable 0` / `1` | data store 정지 / 시작 | `extras/led_settle.py` 10 kHz 캡처 |
| `PM:DS:CLear` | data store 비우기 | |
| `PM:DS:BUFfer 0` | 버퍼 방식 (가득 차면 정지) | |
| `PM:DS:INTerval <n>` | 저장 간격, 내부 샘플(0.1 ms) 단위 정수 | `PM:DS:INTerval?`도 ms가 아니라 이 정수를 돌려줌 |
| `PM:DS:SIZE <n>` | 저장할 점 수 | |
| `PM:DS:Count?` | 저장된 점 수 | |
| `PM:DS:GET? a-b` | a–b번 점 덤프 | 응답에 헤더(`Responsivity: …` 등)가 붙음 → `End of Header`와 `End of Data` 사이만 데이터 |
| `PM:MODE?` `PM:UNITs?` `PM:Lambda?` `PM:AUTO?` `PM:RANge?` `PM:FILTer?` `PM:ANALOGFILTER?` `PM:DIGITALFILTER?` `PM:DS:INTerval?` | 설정 읽기 | 측정 전에 저장해 두고 끝나면 원복. 원복 순서: FILTer → ANALOGFILTER → DIGITALFILTER |

### Keithley 2400

| 명령 | 의미 | 비고 |
|---|---|---|
| `*IDN?` | 장비 식별 | 포트 자동 탐색 (`KEITHLEY` 포함 여부) |
| `*CLS` | 오류 큐·상태 정리 | |
| `*OPC?` | 앞 명령 완료 시 `1` | 설정 명령 뒤에 `;*OPC?`로 붙여 보냄 |
| `:SYST:ERR?` | 오류 하나 읽기 | -101 Invalid character = 깨진 문자열 수신 (아래 트러블슈팅 참고) |
| `:SYST:RSEN ON` / `OFF` | 4-wire / 2-wire | |
| `:SOUR:FUNC CURR` / `VOLT` | 정전류 / 정전압 소스 | 정전류가 기본 (전류가 상한을 넘을 수 없음) |
| `:SOUR:CURR:MODE FIX` | 고정값 소스 | |
| `:SOUR:CURR:RANG:AUTO OFF` | 소스 range 자동 끔 | lablive가 직접 고름 |
| `:SOUR:CURR:RANG <A>` | 소스 range (1 µ/10 µ/100 µ/1 m/10 m/100 m/1 A) | 1 A range의 정확도는 0.27% + 900 µA → 1 mA 점검은 1 mA range로. 올릴 때 range → level, 내릴 때 level → range |
| `:SOUR:CURR:LEV <A>` | 출력 전류 | |
| `:SOUR:FUNC?` `:SOUR:CURR:LEV?` | 소스 상태 읽기 | LED를 켜기 직전에 다시 읽어 확인 |
| `:SENS:FUNC:CONC OFF` / `ON` | 동시 측정 끔 / 켬 | 정전압 스윕은 V와 I를 같이 |
| `:SENS:FUNC "VOLT"` (`,"CURR"`) | 측정 기능 | 정전류에서는 단자 전압만 측정 |
| `:SENS:VOLT:PROT <V>` | 전압 상한 (compliance) | 정전류 모드의 안전 한계. 측정 중 변경 가능 (현재 V + 0.3 V 이상만) |
| `:SENS:VOLT:RANG <V>` `:SENS:VOLT:NPLC <n>` | 전압 측정 range / 적분 시간 | 20 V, NPLC 1 |
| `:SOUR:VOLT:MODE FIX` `:SOUR:VOLT:RANG` `:SOUR:VOLT:LEV <V>` | 정전압 소스 | `led-iv --mode voltage` |
| `:SENS:CURR:PROT <A>` | 전류 상한 (정전압 모드) | ≤ i_max |
| `:SENS:CURR:RANG:AUTO ON` `:SENS:CURR:NPLC <n>` | 전류 측정 | 정전압 모드 |
| `:FORM:ELEM VOLT,CURR` | `:READ?` 응답을 V, I 두 값으로 | |
| `:OUTP ON` / `OFF`, `:OUTP?` | 출력 켜기/끄기, 상태 | 끝날 때 `:OUTP?`가 0인지 확인 (안 되면 포트를 다시 열어 재시도) |
| `:READ?` | 측정 1회 → `V,I` | 정전류 모드의 I는 측정값이 아니라 설정값 |
| `:SENS:VOLT:PROT:TRIP?` | 정전류 모드에서 전압 상한에 걸렸는지 (1/0) | 걸리면 요청 전류를 못 흘리는 중 → FAULT, LED OFF |
| `:SENS:CURR:PROT:TRIP?` | 정전압 모드에서 전류 상한에 걸렸는지 | |

## 트러블슈팅 기록

개발과 실측 중 실제로 겪은 문제와 해결 순서. 같은 증상이 다시 보이면 여기부터 확인.

| 증상 | 원인 | 해결 |
|---|---|---|
| `ModuleNotFoundError: No module named 'lablive'` | 실행 스크립트를 `lablive` 폴더 안에 넣고 실행 | 스크립트를 한 단계 위로 (지금은 설치형 패키지라 해당 없음) |
| 어제 되던 COM 번호로 열리지 않음 | USB-시리얼 어댑터(PL2303, CH340)에 시리얼 번호가 없어 COM 번호가 꽂는 USB 구멍을 따라감 | 포트를 고정하지 않고 매번 `*IDN?`으로 탐색. 측정 창 `장비` → `포트 찾기`, `led-check --scan` |
| 측정 중 `PermissionError` / 포트가 사라짐 | 노트북이 흔들려 CH340 어댑터가 순간적으로 빠짐 | 정리 단계를 서로 독립시키고, 2400 OFF는 포트를 다시 열어서라도 `:OUTP?`=0까지 재시도. 케이블 고정 |
| range 자동 결정이 늘 r7로 감 | auto range 전환 중 `PM:P?`가 3.4E+38을 돌려줘 과범위로 읽힘 | auto range를 끄고 r7에서 한 번 읽은 뒤 필요한 range를 계산. 측정 중 과범위면 소프트웨어로 한 단계씩 올림 |
| 10 kHz 덤프 첫 값이 0.098 근처 | `PM:DS:GET?` 응답 헤더의 Responsivity(0.098 A/W @ 275 nm) 값을 데이터로 읽음 | `End of Header`와 `End of Data` 사이만 파싱 |
| 원복 후 `PM:DIGITALFILTER?`가 100 | 처음엔 원격 설정 상한이 100이라고 잘못 판단. 값을 바꿔 써 보니 10000까지 정상 → 실제 원인은 `PM:FILTer`가 디지털 필터 크기를 100으로 초기화 | FILTer → ANALOGFILTER → DIGITALFILTER 순서로 설정/원복 |
| 원복 후 DS 간격이 틀어짐 | `PM:DS:INTerval?` 값을 ms로 보고 변환함 (실제로는 설정 정수) | 읽은 정수를 그대로 다시 씀 |
| 2400 설정이 가끔 일부만 적용됨 | RS-232에 흐름제어가 없어 연달아 보낸 명령이 사라짐 | 모든 설정을 `명령;*OPC?`로 보내 `1`을 받은 뒤 다음 명령. LED를 켜기 직전 `:SOUR:FUNC?`/`:SOUR:CURR:LEV?`로 재확인 |
| 정전압 5 V 점검에서 6.28 V, 3 mA로 읽힘 | 처음엔 명령 타이밍 문제로 판단했으나 `*OPC?` 동기화 후에도 재현 → 1 A range에서 작은 전류의 정확도/제한 문제로 추정 (확정 못 함) | 이 모듈은 정전압 구동 비권장. 정전류 + 전압 상한이 기본. 소스 range를 전류에 맞춰 선택 |
| uvc275 50 mA에서 광출력 ±46% (1.4–1.55 kHz) | 모듈 내부 스위칭 소자의 문턱(단자 V ≈ 6.26 V). 켠 지 40–60 s 뒤 발열로 V가 내려가면 이완 발진 | 100 mA 이상으로 구동, 경고 전압 6.30 V(프로파일 `vf_guard_V`). 새 LED는 `led-iv`(필터 없는 판독의 점 내 표준편차)로 발진 구간 확인 |
| 그 발진이 이상(anomaly)으로 안 잡힘 | 지속 발진은 '급변'이 아니라 배경 잡음으로 학습됨 | 상대 기준과 별도로 절대 기준(0.5%) 추가 |
| 접지 루프 걱정 | 노트북 + 두 장비 공통 접지 | 실측: 250 mA에서 60/180 Hz 성분이 신호의 0.004–0.006% → USB 절연기 불필요 |
| 끈 직후 0 A 근처 점이 피팅에 섞여 최종값이 틀어짐 | LED OFF와 표본 수집 정지 순서 | `led_on=False` → 판정 정지 → OFF 순서. 최종 상태도 정지 표시 전에 기록 |
| SETTLED가 너무 일찍 뜸 (515 s, 실제로는 그때 +1.35%) | 짧은 데이터로 피팅한 최종값이 높게 나옴 (600 s 데이터 +0.42%, 900 s +0.29%) | 30분 실측 데이터로 판정 규칙 6가지를 재생 비교 → 'SETTLED = 예측 최종값 ±tol/2' 채택 (기본 margin 0.5) |
| 프로파일 기준 곡선이 맞지 않음 | 기준으로 등록한 회차가 사실은 데워진 상태에서 시작한 회차 (시작 V 6.571 vs 냉간 6.612) | 시작 전압이 기준과 다르면(양방향) 기준 곡선을 쓰지 않고 자유 피팅. 기준 모델은 가장 차가운 시작 회차로 |
| 그래프 상태 패널 한글이 □□□ | 상태 문구에 한글, 그리고 matplotlib은 'sans-serif' 같은 일반 이름 하나만 주면 글자 단위 대체 글꼴을 쓰지 않음 | 그림 안 고정 문구는 영어, 글꼴 목록을 실제 이름(DejaVu Sans, 맑은 고딕 …)으로 지정 |
| 랩 PC에서 2400 `-101` (Invalid character) | 1936-R을 찾느라 모든 포트에 38400 baud로 `*IDN?`을 보냄 → 2400(9600)이 앞 COM 번호면 깨진 문자열이 입력 버퍼에 남아 다음 명령과 합쳐짐. 노트북은 2400이 뒤 번호라 안 드러남 | 지난번 찾은 포트를 먼저, 다른 장비가 있던 포트는 맨 뒤에 탐색. 2400을 열 때/설정 전 CR + `*CLS` |
| 채팅에서 설치 파일(.exe)을 받을 수 없음 | 다운로드 형식 제한 | zip으로 감싸서 전달 |

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
