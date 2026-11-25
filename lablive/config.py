"""경로/사용자 설정. 실행 위치(cwd)와 무관하게 항상 같은 곳에 profiles/ runs/ logs/ 를 둔다.

데이터 폴더(home) 우선순위:
  set_home() (CLI --home, --sim) > 사용자 설정 파일의 "home" (GUI 경로 창 / install.bat) > 환경변수 LABLIVE_HOME
  > ~/lablive_data
  <home>/profiles/<name>.json    광원 프로파일
  <home>/runs/<시각>_...          측정 회차  (사용자 설정 "runs"로 따로 지정 가능 — 예: 공유 드라이브)
  <home>/logs/                    오류 기록
사용자 설정 파일: ~/.lablive.json  {"home": ..., "runs": ..., "colors": {...}}
기존 C:\\code\\control 의 profiles/ runs/ 를 계속 쓰려면 home = C:\\code\\control
(프로파일 안 경로는 home 기준 상대경로라 폴더째 옮겨도 동작)
"""
import json
import os

_home = None
USER_FILE = os.path.join(os.path.expanduser("~"), ".lablive.json")


def default_home():
    return os.path.join(os.path.expanduser("~"), "lablive_data")


def load_user():
    try:
        with open(USER_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_user(**fields):
    """사용자 설정 일부 갱신 (None이면 그 키 삭제)."""
    d = load_user()
    for k, v in fields.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    tmp = USER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, USER_FILE)
    return d


def user_home():
    """--home/--sim을 무시한 '평소' 데이터 폴더 (경로 창에 보여 줄 값)."""
    h = load_user().get("home") or os.environ.get("LABLIVE_HOME") or default_home()
    return os.path.abspath(os.path.expanduser(h))


def first_run(legacy=r"C:\code\control"):
    """처음 실행(설정 파일에 home 없음, LABLIVE_HOME 없음)이고 예전 스크립트 폴더에 profiles/가 있으면 그걸 home으로.
    반환: 정해진 home 또는 None."""
    if load_user().get("home") or os.environ.get("LABLIVE_HOME"):
        return None
    h = legacy if os.path.isdir(os.path.join(legacy, "profiles")) else default_home()
    try:
        save_user(home=h)
    except OSError:
        return None
    return h


def set_home(path):
    global _home
    _home = os.path.abspath(os.path.expanduser(path)) if path else None


def home_source():
    if _home:
        return "명령 옵션(--home/--sim)"
    if load_user().get("home"):
        return f"사용자 설정 ({USER_FILE})"
    if os.environ.get("LABLIVE_HOME"):
        return "환경변수 LABLIVE_HOME"
    return "기본값"


def home():
    h = _home or load_user().get("home") or os.environ.get("LABLIVE_HOME") or default_home()
    return os.path.abspath(os.path.expanduser(h))


def _sub(name):
    p = os.path.join(home(), name)
    os.makedirs(p, exist_ok=True)
    return p


def profiles_dir():
    return _sub("profiles")


def runs_dir():
    """사용자 설정 "runs"가 있으면 그 폴더 (단, --home/--sim으로 home을 바꾼 실행에서는 <home>/runs)."""
    r = None if _home else load_user().get("runs")
    if r:
        r = os.path.abspath(os.path.expanduser(r))
        os.makedirs(r, exist_ok=True)
        return r
    return _sub("runs")


def logs_dir():
    return _sub("logs")
