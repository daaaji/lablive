"""그림 글꼴: DejaVu Sans(µ, τ, ± 포함) + 한글 글꼴을 글자 단위 fallback으로 (matplotlib ≥ 3.6).
한글 글꼴이 없는 PC에서도 깨지지 않게 그림 안 문구는 원칙적으로 영어, 이건 사용자 메모(--note) 등 대비."""
_done = None

KR = ("Malgun Gothic", "맑은 고딕", "NanumGothic", "NanumBarunGothic", "AppleGothic", "Apple SD Gothic Neo",
      "Noto Sans CJK KR", "Noto Sans KR", "Gulim", "Dotum",
      "Noto Sans CJK JP", "Noto Sans CJK TC", "Noto Sans CJK SC")   # CJK 글꼴도 한글 포함


MONO = ["DejaVu Sans Mono"]     # setup() 뒤에 한글 글꼴이 붙음 — 고정폭 텍스트는 family=MONO로


def setup():
    """반환: 찾은 한글 글꼴 이름 (없으면 None).
    주의: 'sans-serif' 같은 일반 이름 하나만 주면 matplotlib은 글꼴 하나로만 찾아 fallback이 안 됨
    → font.family 자체를 실제 글꼴 이름 목록으로."""
    global _done
    if _done is not None:
        return _done or None
    import matplotlib
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    kr = next((c for c in KR if c in have), None)
    extra = [kr] if kr else []
    matplotlib.rcParams["font.family"] = ["DejaVu Sans"] + extra
    MONO[:] = ["DejaVu Sans Mono"] + extra
    matplotlib.rcParams["axes.unicode_minus"] = False
    _done = kr or ""
    return kr
