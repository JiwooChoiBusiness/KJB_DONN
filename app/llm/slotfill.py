"""슬롯 필링 문자열 유틸 (SPEC 2.8). app.data/app.models에 의존하지 않는 순수 문자열 처리.

원칙(D4, 절대): LLM은 숫자를 보지도 쓰지도 않는다. 코드가 범주형 사실(facts)과
플레이스홀더 이름 목록만 LLM에 보내고, LLM은 `{amount}` 같은 플레이스홀더가 든
문장을 돌려주며, 코드가 검증한 뒤(`validate`) 숫자를 채운다(`fill`). 검증에
실패하거나 LLM이 불가하면 호출자(`app/services/explain.py`)가 템플릿으로 간다.
"""
from __future__ import annotations

import re
from typing import Any

# 플레이스홀더 이름은 소문자와 밑줄만(숫자 없음). 순위는 항목 라벨 글자를 따라
# _a, _b, _c 접미사를 쓴다(rate_a = 첫 번째 항목 금리).
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z_]*)\}")

FORBIDDEN_PHRASES = (
    "추천", "가입하세요", "가입을 권", "갈아타세요", "권합니다", "권해드", "권장",
    "보장", "무조건", "최고의", "최선의", "가장 좋은", "가장 유리",
)

_DIGIT_RE = re.compile(r"[0-9]")
_LEADING_BULLET_RE = re.compile(r"(?m)^[ \t]*[-•]+[ \t]*")
_DASH_RE_MAP = {"—": " ", "–": " "}  # em dash, en dash -> 공백
_WS_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"[.!?]+")


def assert_no_digits(payload: Any) -> None:
    """payload(dict/list/str 중첩 구조) 안 어디든 숫자 문자(0~9)가 있으면 ValueError.

    LLM으로 보내기 직전에 반드시 호출한다(D4 하드 불변식). 실패하면 호출하지 않고
    템플릿으로 간다(호출부 책임).
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert_no_digits(key)
            assert_no_digits(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            assert_no_digits(item)
    elif isinstance(payload, str):
        if _DIGIT_RE.search(payload):
            raise ValueError(f"payload에 숫자가 포함되어 있습니다: {payload!r}")
    elif payload is None or isinstance(payload, bool):
        return
    elif isinstance(payload, (int, float)):
        # 범주형 사실에는 숫자 값 자체가 있으면 안 된다(문자열 라벨만 허용).
        raise ValueError(f"payload에 숫자 값이 포함되어 있습니다: {payload!r}")


def sanitize(text: str) -> str:
    """앞뒤 공백 정리, 연속 공백 축약, em/en dash를 공백으로, 마크다운 기호 제거."""
    if not text:
        return ""
    out = text
    for dash, repl in _DASH_RE_MAP.items():
        out = out.replace(dash, repl)
    out = "\n".join(_LEADING_BULLET_RE.sub("", line) for line in out.split("\n"))
    out = out.replace("`", "").replace("*", "")
    out = _WS_RE.sub(" ", out)
    return out.strip()


def validate(
    text: str, allowed: set[str], banned: list[str], *, max_chars: int, max_sentences: int
) -> list[str]:
    """text(이미 sanitize된 문장)의 문제 코드 목록을 돌려준다(빈 리스트면 통과).

    코드: empty, unknown_placeholder:<name>, digit_outside_placeholder,
    banned_term:<term>, forbidden_phrase:<phrase>, too_long, too_many_sentences.
    """
    if not text or not text.strip():
        return ["empty"]

    problems: list[str] = []

    found = set(PLACEHOLDER_RE.findall(text))
    for name in sorted(found - allowed):
        problems.append(f"unknown_placeholder:{name}")

    without_placeholders = PLACEHOLDER_RE.sub("", text)
    if _DIGIT_RE.search(without_placeholders):
        problems.append("digit_outside_placeholder")

    for term in banned:
        if term and term in text:
            problems.append(f"banned_term:{term}")

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            problems.append(f"forbidden_phrase:{phrase}")

    if len(text) > max_chars:
        problems.append("too_long")

    sentence_count = len([s for s in _SENTENCE_END_RE.split(text) if s.strip()])
    if sentence_count > max_sentences:
        problems.append("too_many_sentences")

    return problems


# 값 뒤에 다시 붙으면 중복이 되는 단위(긴 것부터 검사한다).
_UNIT_SUFFIXES = ("개월", "개", "원", "%", "세", "년")


def fill(text: str, values: dict[str, str]) -> str:
    """text의 플레이스홀더를 values로 치환한다. 값이 없는 플레이스홀더가 남으면 KeyError.

    치환 직후 조사 보정을 한다: LLM/템플릿이 쓴 문장은 실제 값을 모른 채 플레이스홀더
    바로 뒤에 조사(은/는, 이/가, 을/를, 과/와, 으로/로, 이라/라, 이에요/예요)를 붙였을 수
    있어, 값의 마지막 글자 받침에 맞지 않을 수 있다(예: "{rate_a}은" + "5.47%" -> 그대로
    두면 "5.47%은"). `_fix_josa_after`가 플레이스홀더 바로 뒤 토큰이 그 조사 후보 중
    하나면 값에 맞는 형태로 고쳐 쓴다.
    """

    out: list[str] = []
    pos = 0
    for match in PLACEHOLDER_RE.finditer(text):
        name = match.group(1)
        if name not in values:
            raise KeyError(name)
        value = values[name]
        out.append(text[pos:match.start()])
        out.append(value)
        pos = match.end()
        # 단위 중복 제거: LLM이 "{candidates_total}개"처럼 값에 이미 붙은 단위를 한 번 더 쓰면
        # "75개개"가 된다(2026-09-06 화면 실측). 값의 끝 단위와 같은 단위가 바로 이어지면 건너뛴다.
        for unit in _UNIT_SUFFIXES:
            if value.endswith(unit) and text.startswith(unit, pos):
                pos += len(unit)
                break
        fixed, consumed = _fix_josa_after(text[pos:], value)
        if consumed:
            out.append(fixed)
            pos += consumed
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# 조사 보정 (플레이스홀더를 값으로 채운 뒤, 값의 마지막 글자 받침에 맞춰 고친다)
# ---------------------------------------------------------------------------

# (받침 있음 형태, 받침 없음 형태). "으로/로"는 받침이 있어도 그 받침이 ㄹ이면
# "으로"가 아니라 "로"를 쓰는 특수 규칙이 있어 josa()가 pair 이름으로 따로 처리한다.
_JOSA_PAIRS: dict[str, tuple[str, str]] = {
    "은/는": ("은", "는"),
    "이/가": ("이", "가"),
    "을/를": ("을", "를"),
    "과/와": ("과", "와"),
    "으로/로": ("으로", "로"),
    "이라/라": ("이라", "라"),
    "이에요/예요": ("이에요", "예요"),
}

_RIEUL_BATCHIM_INDEX = 8  # 종성 순서(0=받침 없음)에서 순수 "ㄹ" 받침의 인덱스


def josa(word: str, pair: str) -> str:
    """받침 유무로 조사 짝(pair)에서 하나를 고른다(템플릿용, `fill()`의 조사 보정도 재사용).

    pair: "은/는", "이/가", "을/를", "과/와", "으로/로", "이라/라", "이에요/예요". 마지막
    글자가 한글 음절이 아니면(예: "%", 영문, 숫자로 끝나는 값) 받침 없는 쪽을 쓴다.
    "으로/로"는 받침이 있어도 그 받침이 ㄹ이면 "으로"가 아니라 "로"를 쓴다(예: "말로",
    "서울로").
    """
    with_batchim, without_batchim = _JOSA_PAIRS[pair]
    if not word:
        return without_batchim
    offset = ord(word[-1]) - 0xAC00
    if not (0 <= offset <= 11171):
        return without_batchim
    batchim = offset % 28
    if batchim == 0:
        return without_batchim
    if pair == "으로/로" and batchim == _RIEUL_BATCHIM_INDEX:
        return without_batchim
    return with_batchim


# fill()이 플레이스홀더 바로 뒤 원문에서 조사 후보를 찾을 때 쓰는 토큰 목록. 각 그룹의
# 받침 있음/없음 형태를 모두 등록해 템플릿이 어느 쪽을 먼저 썼든 찾아낸다. 긴 토큰(2~3자)을
# 짧은 토큰(1자)보다 먼저 검사해야 "이에요"가 "이"로 잘못 잘리지 않는다.
_JOSA_TOKENS: list[tuple[str, str]] = sorted(
    ((form, pair) for pair, forms in _JOSA_PAIRS.items() for form in forms),
    key=lambda item: -len(item[0]),
)


def _fix_josa_after(remainder: str, value: str) -> tuple[str, int]:
    """remainder(플레이스홀더 바로 뒤 원문)가 조사 토큰으로 시작하면 (고친 토큰, 그
    토큰의 길이)를 돌려준다. 아니면 ("", 0)."""
    for token, pair in _JOSA_TOKENS:
        if remainder.startswith(token):
            return josa(value, pair), len(token)
    return "", 0
