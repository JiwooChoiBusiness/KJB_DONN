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


def fill(text: str, values: dict[str, str]) -> str:
    """text의 플레이스홀더를 values로 치환한다. 값이 없는 플레이스홀더가 남으면 KeyError."""

    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(name)
        return values[name]

    return PLACEHOLDER_RE.sub(_sub, text)


_JOSA_PAIRS: dict[str, tuple[str, str]] = {
    "은/는": ("은", "는"),
    "이/가": ("이", "가"),
    "을/를": ("을", "를"),
}


def josa(word: str, pair: str) -> str:
    """받침 유무로 조사 짝(pair)에서 하나를 고른다(템플릿용).

    pair: "은/는", "이/가", "을/를". 마지막 글자가 한글 음절이 아니면 받침 없는
    쪽(뒤쪽: 는/가/를)을 쓴다.
    """
    with_batchim, without_batchim = _JOSA_PAIRS[pair]
    if not word:
        return without_batchim
    offset = ord(word[-1]) - 0xAC00
    if 0 <= offset <= 11171:
        return with_batchim if offset % 28 != 0 else without_batchim
    return without_batchim
