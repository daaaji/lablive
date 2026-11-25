# lablive 설치 파일

Windows 설치 파일만 두는 브랜치 (소스는 [`main`](https://github.com/daaaji/lablive/tree/main)). 64비트 Windows 10/11, Python 불필요.

| 파일 | 버전 | 날짜 |
|---|---|---|
| [lablive_Setup_0.9.0b7.exe](lablive_Setup_0.9.0b7.exe) | 0.9.0b7 (beta) | 2026-11-25 |

- 파일을 열고 오른쪽 위 다운로드(Download raw file) → 실행 → 기존 설치 위에 그대로 덮어쓰기
- 서명 없는 설치 파일이라 SmartScreen 경고가 뜨면 `추가 정보` → `실행`
- 무결성: `SHA256SUMS` (PowerShell: `Get-FileHash lablive_Setup_0.9.0b7.exe`)
- 변경 내용: `main` 브랜치의 CHANGELOG.md
- b6·b7 설치 파일은 b5 실행 파일의 lablive 모듈을 교체·추가해 만듦 (`main`의 packaging/repyz.py) — 실행 파일 속성 버전 표시는 0.9.0.5
- 이전 설치 파일(0.9.0b6-r2 등)은 이 브랜치의 커밋 기록에 남아 있음
