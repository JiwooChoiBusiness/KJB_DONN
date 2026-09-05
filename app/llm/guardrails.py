"""가드레일: 금칙어 검사, PII 마스킹, 규칙 기반 발화 파싱 (SPEC 2.4).

이 모듈은 순수 문자열 처리만 하며 app.data/app.models에 의존하지 않는다(가볍고
테스트하기 쉬운 상태 유지). `banned` 목록은 상품 회사명/상품명으로 호출부
(app/services/insights.py)가 조합해서 넘긴다.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 금칙어 검사
# ---------------------------------------------------------------------------


def check_text(text: str, banned: list[str]) -> list[str]:
    """text 안에 포함된 banned 항목 목록을 돌려준다(없으면 빈 리스트).

    대소문자 구분 없는 한국어 문자열 포함 여부만 본다(부분 일치). 빈 문자열
    금칙어는 모든 문장에 일치해버리므로 무시한다.
    """
    if not text or not banned:
        return []
    return [term for term in banned if term and term in text]


# ---------------------------------------------------------------------------
# PII 마스킹
# ---------------------------------------------------------------------------

# 순서가 중요하다: 더 구체적인 패턴(주민등록번호/전화번호/이메일)을 먼저 치환해
# 숫자 자리를 없앤 뒤, 남은 긴 숫자 뭉치를 계좌번호류로 마스킹한다. 그렇지 않으면
# 계좌번호 패턴이 전화번호/주민등록번호까지 먼저 삼켜 각각의 라벨을 못 붙인다.
#
# 전화번호 패턴은 구분자(-, 공백)를 필수로 요구한다. 구분자를 선택 사항으로 두면
# "110123456789" 같은 구분자 없는 긴 숫자 뭉치 안에서 "0"으로 시작하는 부분
# 문자열만 전화번호로 오매칭되어 앞부분 숫자("11")가 마스킹되지 않고 그대로
# 남는 사고가 실제로 발생한다(수동 테스트로 확인). 구분자 없는 휴대폰 번호
# (예: "01012345678")는 아래 계좌번호류 패턴에 걸려 "[계좌번호]"로만 마스킹되며,
# 라벨은 정확하지 않아도 수치 자체는 그대로 마스킹된다(D4의 목적은 라벨이 아니라
# 마스킹이므로 안전).
_RRN_RE = re.compile(r"\d{6}[-\s]?[1-4]\d{6}")
_PHONE_RE = re.compile(r"01[016789][-\s]\d{3,4}[-\s]\d{4}|0\d{1,2}[-\s]\d{3,4}[-\s]\d{4}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 계좌번호류: 구분자를 포함해 10자리 이상 이어지는 숫자 뭉치.
# 일반적인 원화 금액 표기(1억 미만, 예: 999,999,999원=9자리)는 9자리 이하라
# 이 패턴에 걸리지 않는다(확인 필요: 10억 이상 금액을 채팅에 그대로 숫자로
# 입력하는 극단적 사례는 계좌번호로 오인될 수 있음 - PoC 범위에서는 허용).
_ACCOUNT_RE = re.compile(r"\d[\d\-\s]{9,20}\d")


def mask_pii(text: str) -> str:
    """전화번호, 주민등록번호, 계좌번호류 숫자, 이메일을 마스킹해 돌려준다.

    Gemini로 보내는 모든 사용자 발화는 반드시 이 함수를 거쳐야 한다(SPEC D4).
    """
    if not text:
        return text
    masked = _RRN_RE.sub("[주민등록번호]", text)
    masked = _PHONE_RE.sub("[전화번호]", masked)
    masked = _EMAIL_RE.sub("[이메일]", masked)
    masked = _ACCOUNT_RE.sub("[계좌번호]", masked)
    return masked


# ---------------------------------------------------------------------------
# 규칙 기반 발화 파싱
# ---------------------------------------------------------------------------

# 의도 키워드. 더 구체적인 의도를 먼저 검사하고 compare는 마지막에 둔다(비교는
# 슬롯만 있어도 기본값으로 떨어지는 catch-all이라 다른 의도와 겹칠 위험이 가장
# 작다).
_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("schedule", ("상환표", "상환 스케줄", "스케줄", "납입 계획", "납입계획")),
    ("scenario", ("시나리오", "전망")),
    ("spending", ("소비", "지출", "거래내역", "가계부")),
    ("liquidity", ("비상금", "비상자금")),
    ("saving", ("저축률", "저축", "자동이체")),
    ("retirement", ("노후", "연금", "은퇴")),
    ("action", ("무엇부터", "뭐부터", "할 일", "행동 제안", "행동")),
    ("compare", ("비교", "공시", "대환", "갈아타")),
]

# P7 생애주기 층(SPEC 2.7): retirement(노후·연금·은퇴), saving(저축률·자동이체),
# liquidity(비상금·비상자금)는 app/api/routes.py가 재무비율·노후자금 격차 수치를 채워 답한다.
_VALID_INTENTS = {"compare", "schedule", "scenario", "action", "faq", "spending", "retirement", "saving", "liquidity"}

# 카테고리 키워드. "신용대출"이 "신용"보다 먼저 오도록(더 구체적인 것을 먼저)
# 순서를 유지한다.
_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("주택담보", "mortgage"),
    ("주담대", "mortgage"),
    ("전세", "jeonse"),
    ("신용대출", "credit"),
    ("신용", "credit"),
    ("적금", "saving"),
    ("예금", "deposit"),
    ("정책", "policy"),
]

_SORT_KEYWORDS: list[tuple[str, str]] = [
    ("금리순", "rate"),
    ("금리가 낮은", "rate"),
    ("금리 낮은", "rate"),
    ("월납입", "monthly_payment"),
    ("월 납입", "monthly_payment"),
    ("총이자", "total_cost"),
    ("총비용", "total_cost"),
]

_RATE_CAP_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:이하|이내|미만|아래)")
_TERM_MONTHS_RE = re.compile(r"(\d+)\s*개월")
_TERM_YEARS_RE = re.compile(r"(\d+)\s*년")
_EOK_RE = re.compile(r"(\d+(?:\.\d+)?)\s*억")
_CHEONMAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*천만\s*원?")
_MANWON_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*만\s*원?")
_RAW_WON_RE = re.compile(r"(\d[\d,]*)\s*원")
# 조사(는/은/을/를)를 비탐욕 캡처 뒤에서 별도로 흡수해 "우리은행은 빼고" 같은
# 입력에서도 조사가 회사명에 섞여 들어가지 않게 한다.
_EXCLUDE_RE = re.compile(r"([가-힣A-Za-z0-9]{1,12}?)(?:는|은|을|를)?\s*빼고")
_EXCLUDE_RE2 = re.compile(r"([가-힣A-Za-z0-9]{1,12}?)(?:는|은|을|를)?\s*제외")


def _detect_intent(text: str) -> Optional[str]:
    for intent, keywords in _INTENT_KEYWORDS:
        if any(k in text for k in keywords):
            return intent
    return None


def _detect_category(text: str) -> Optional[str]:
    for keyword, value in _CATEGORY_KEYWORDS:
        if keyword in text:
            return value
    return None


def _detect_sort_key(text: str) -> Optional[str]:
    for keyword, value in _SORT_KEYWORDS:
        if keyword in text:
            return value
    return None


def _detect_amount(text: str) -> Optional[int]:
    """"3천만원", "5,000만 원", "1억", "30000000원" 등을 원 단위 정수로 변환한다.

    억/천만/만 단위는 서로 다른 위치에 나타나도 합산한다("1억 5천만원" -> 1.5억).
    어떤 단위 표현도 없으면 순수 "숫자+원" 표기를 마지막으로 시도한다.
    """
    total = 0.0
    found = False

    m = _EOK_RE.search(text)
    if m:
        total += float(m.group(1)) * 100_000_000
        found = True

    m = _CHEONMAN_RE.search(text)
    if m:
        total += float(m.group(1)) * 10_000_000
        found = True

    m = _MANWON_RE.search(text)
    if m:
        total += float(m.group(1).replace(",", "")) * 10_000
        found = True

    if not found:
        m = _RAW_WON_RE.search(text)
        if m:
            digits = m.group(1).replace(",", "")
            if digits:
                total = float(digits)
                found = True

    return int(total) if found else None


def _detect_term_months(text: str) -> Optional[int]:
    m = _TERM_MONTHS_RE.search(text)
    if m:
        return int(m.group(1))
    m = _TERM_YEARS_RE.search(text)
    if m:
        return int(m.group(1)) * 12
    return None


def _detect_max_rate(text: str) -> Optional[float]:
    m = _RATE_CAP_RE.search(text)
    return float(m.group(1)) if m else None


def _detect_exclude_companies(text: str) -> list[str]:
    names: list[str] = []
    for pattern in (_EXCLUDE_RE, _EXCLUDE_RE2):
        m = pattern.search(text)
        if m:
            name = m.group(1).strip()
            if name and name not in names:
                names.append(name)
    return names


def parse_message(text: str) -> dict[str, Any]:
    """규칙 기반으로 의도와 비교 슬롯을 추출한다(Gemini 미사용/실패 시 폴백).

    반환 키는 `app.llm.gemini`의 extract 스키마와 동일하게 맞춘다:
    intent(필수), category, amount, term_months, max_rate, exclude_companies, sort_key.
    감지하지 못한 슬롯 키는 아예 넣지 않는다(값 있는 키만 포함).
    """
    text = text or ""

    category = _detect_category(text)
    amount = _detect_amount(text)
    term_months = _detect_term_months(text)
    max_rate = _detect_max_rate(text)
    exclude_companies = _detect_exclude_companies(text)
    sort_key = _detect_sort_key(text)

    intent = _detect_intent(text)
    if intent is None:
        has_slots = any([category, amount, term_months, max_rate, exclude_companies, sort_key])
        intent = "compare" if has_slots else "faq"
    if intent not in _VALID_INTENTS:
        intent = "faq"

    result: dict[str, Any] = {"intent": intent}
    if category is not None:
        result["category"] = category
    if amount is not None:
        result["amount"] = amount
    if term_months is not None:
        result["term_months"] = term_months
    if max_rate is not None:
        result["max_rate"] = max_rate
    if exclude_companies:
        result["exclude_companies"] = exclude_companies
    if sort_key is not None:
        result["sort_key"] = sort_key
    return result
