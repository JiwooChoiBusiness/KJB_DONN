"""설명 문장 생성 서비스 (슬롯 필링, SPEC 2.8).

원칙(D4, 절대): LLM은 숫자를 보지도 쓰지도 않는다. 코드가 범주형 사실(facts)과
플레이스홀더 목록(placeholders)만 LLM에 보내고, LLM은 `{amount}` 같은 플레이스홀더가
든 문장을 돌려주며, 코드가 검증(`app.llm.slotfill.validate`)한 뒤 숫자를 채운다
(`app.llm.slotfill.fill`). 검증에 실패하거나 LLM이 불가하면 템플릿 문장을 쓴다.
문제가 하나라도 있으면 전체를 템플릿으로 간다(LLM 문장과 템플릿을 섞지 않는다).

첫 화면(`GET /api/home`)에서는 이 모듈을 호출하지 않는다(사용자가 비교를 실행했거나
"AI 설명 보기"를 눌렀을 때만, `app/api/routes.py`의 `/explain` 엔드포인트 참고).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

from app.core.capacity import compute_capacity
from app.core.schedule import build_schedule
from app.core import hashing
from app.data import db
from app.llm import guardrails, slotfill
from app.llm.provider import LLMUnavailable
from app.models import (
    ActionCard,
    Capacity,
    CompareContext,
    CompareItem,
    CompareResult,
    ExplainResult,
    PolicyParams,
    RateSemantics,
    SortKey,
    UserProfile,
    WhatIfResult,
)
from app.services import actions as actions_service
from app.services import decisions as decisions_service
from app.services import session as session_service
from app.services.insights import get_banned_terms

PROMPT_VERSION = "explain-v1"


def _load_llm_yaml_value(key: str, default: Any, path: str = "config/llm.yaml") -> Any:
    """config/llm.yaml에서 값 하나만 읽는다(app.llm.gemini의 로더와 별도로, explain.py는
    provider 인스턴스 없이도 대화 화면(SPEC 2.9) 설명 체인 상한을 알아야 한다)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    data = yaml.safe_load(text) or {}
    return data.get(key, default)


CHAT_EXPLAIN_DEADLINE_SECONDS: float = _load_llm_yaml_value("chat_explain_deadline_seconds", 8)

EXPLAIN_SYSTEM = (
    "당신은 한국어 개인 부채 코치 앱 DONN의 설명 작성기입니다. 입력 JSON의 facts만으로 문장을 씁니다. "
    "규칙 1: 숫자를 직접 쓰지 말고 금액, 금리, 기간, 개수는 반드시 placeholders에 있는 "
    "{이름} 형태의 플레이스홀더를 그대로 넣으세요. 목록에 없는 플레이스홀더를 새로 만들지 마세요. "
    "규칙 2: 상품명, 금융회사명, 브랜드명을 쓰지 말고 각 항목은 제공된 익명 라벨로만 부르세요. "
    "규칙 3: 추천, 가입하세요, 갈아타세요 같은 권유 표현을 쓰지 말고 비교, 확인, 살펴보기로 쓰세요. "
    "규칙 4: 입력에 없는 사실(우대조건, 한도, 심사 결과, 자격, 서류)을 지어내지 마세요. "
    "규칙 5: 해요체로 짧고 명확하게 쓰세요. summary는 2~3문장이고, reasons가 있다면 항목마다 "
    "1문장씩 items 순서와 같은 개수로 돌려주세요. "
    "규칙 6: em dash나 특수 기호, 마크다운 서식(별표, 백틱, 목록 기호)을 쓰지 마세요. "
    "규칙 7: 출력은 주어진 JSON 스키마를 그대로 따르세요."
)

COMPARE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "reasons": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary", "reasons"],
}

ACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"summary": {"type": "STRING"}},
    "required": ["summary"],
}

# 행동 카드 설명(action_card_v1) 전용 시스템 프롬프트(2026-09-06 PMO 지적: 실제 화면에서
# "현재 -1,450,608원이나 95% 상태를 고려해..." 같이 라벨 없는 플레이스홀더 나열이
# 나왔다). EXPLAIN_SYSTEM의 공통 규칙에 3문장 구조와 라벨 동반 요구, 숫자 없는 좋은
# 문장 예시(R2 추가 상환, R4 비상금, R3 대환 비교)를 더한다.
ACTION_EXPLAIN_SYSTEM = EXPLAIN_SYSTEM + (
    "\n규칙 8(구조): summary는 반드시 세 문장입니다. 문장 1은 확인된 사실을 라벨과 값으로 "
    "말합니다(예: 이번 달 남는 돈이 {net_monthly}이고 상환 비율이 {debt_service_ratio}라서). "
    "문장 2는 그 사실이 왜 중요한지 gist를 풀어서 설명합니다. 문장 3은 지금 할 일 한 가지를 "
    "카드 제목의 행동을 풀어써서 말합니다(상품명·회사명 없이). "
    "규칙 9(라벨 동반): placeholders 목록의 각 항목은 \"키: 라벨\" 형식입니다. 문장에서 그 "
    "플레이스홀더를 쓸 때는 반드시 라벨에 해당하는 말과 함께 쓰세요(예: 라벨이 \"이번 달 "
    "남는 돈\"이면 \"이번 달 남는 돈이 {net_monthly}\"처럼 쓰고, 라벨 없이 \"{net_monthly}이나\" "
    "처럼 값만 나열하지 마세요). "
    "좋은 예시 세 개(숫자 없이 플레이스홀더를 그대로 쓴 문장, 실제로는 이렇게 라벨과 함께 씁니다):\n"
    "1) 이번 달 남는 돈이 {net_monthly}이라서 금리 {target_rate} 대출에 추가로 갚으면 "
    "{months_saved} 빨리 끝나고 이자 {interest_saved}을 아낄 수 있어요. 여유 자금이 생기면 "
    "이 대출부터 갚는 것을 확인해 보세요.\n"
    "2) 지금 비상금이 목표보다 {gap}만큼 모자라서 갑자기 돈이 필요할 때 대응하기 어려워요. "
    "매달 조금씩이라도 비상금부터 채우는 계획을 준비해 보세요.\n"
    "3) 지금 대출 금리가 {current_rate}로 낮지 않은 편이라서 다른 조건과 비교해볼 필요가 "
    "있어요. 공시된 다른 상품의 금리와 조건을 비교해 보세요."
)

# ---------------------------------------------------------------------------
# 설명 문장 품질 검증 (2026-09-06 PMO 지적, compare·action·chat_compare 모두 적용)
# ---------------------------------------------------------------------------

# 플레이스홀더별 "이 근처에 있어야 자연스러운" 맥락어. 각 플레이스홀더 앞뒤 20자
# (공백 제거 후) 안에 이 중 하나도 없으면 라벨 없이 값만 나열한 것으로 본다
# (`unlabeled_placeholder`). label_a/b/c는 이미 라벨 그 자체라 검사 대상이 아니다.
# 표에 없는 키는 검사를 생략한다.
PLACEHOLDER_CONTEXT: dict[str, tuple[str, ...]] = {
    "net_monthly": ("남는돈", "여력", "여유"),
    "debt_service_ratio": ("상환비율", "비율"),
    "interest_saved": ("이자", "절감", "아낄"),
    "months_saved": ("개월", "단축", "빨리"),
    "new_months": ("개월", "기간"),
    "extra_monthly": ("추가상환", "추가", "더갚"),
    "target_rate": ("금리",),
    "current_rate": ("금리",),
    "rate": ("금리",),
    "emergency_fund": ("비상금", "비상자금", "부족", "목표"),
    "target_emergency_fund": ("비상금", "비상자금", "부족", "목표"),
    "gap": ("비상금", "비상자금", "부족", "목표"),
    "balance": ("잔액",),
    "payoff_amount": ("정리", "금액"),
    "monthly_interest_saving": ("이자", "절감"),
    "prepay_fee": ("수수료",),
    "breakeven_months": ("개월", "회수", "손익"),
    "saving_rate": ("저축률", "저축"),
    "min_saving_rate": ("저축률", "저축"),
    "coverage_ratio": ("충당률", "충당"),
    "min_coverage_ratio": ("충당률", "충당"),
    "guaranteed_income": ("확정소득", "소득"),
    "essential_expense": ("지출", "생활비"),
    "monthly_income_x2": ("월소득", "두배"),
    "remaining_months": ("개월", "남은", "거치"),
    "grace_months": ("개월", "남은", "거치"),
    "cost": ("비용", "무료"),
    "amount": ("금액", "원을", "빌리"),
    "term_months": ("기간", "동안", "개월"),
    "max_rate": ("상한", "이하", "금리"),
    "candidates_total": ("상품", "중", "후보"),
    "shown_count": ("상위", "골랐", "개를"),
    "rate_a": ("금리",), "rate_b": ("금리",), "rate_c": ("금리",),
    "monthly_a": ("납입", "월"), "monthly_b": ("납입", "월"), "monthly_c": ("납입", "월"),
    "total_a": ("총이자", "이자"), "total_b": ("총이자", "이자"), "total_c": ("총이자", "이자"),
    "vs_a": ("절감", "줄", "더들", "차이", "아낄"),
    "vs_b": ("절감", "줄", "더들", "차이", "아낄"),
    "vs_c": ("절감", "줄", "더들", "차이", "아낄"),
    # SPEC 2.13 whatif_v1
    "lump_sum_amount": ("일시상환", "한번에", "목돈", "상환"),
    "new_rate": ("금리",),
    "total_cost_delta": ("총이자", "이자", "차이"),
    "monthly_delta": ("납입", "월", "차이"),
    "retirement_age_after": ("은퇴", "나이", "세"),
    "shortfall_delta": ("부족액", "부족", "노후"),
}

# 각 플레이스홀더 앞/뒤로 살펴볼 문자 수(공백 제거 후). 한국어 어순상 설명어가 값
# 앞이 아니라 뒤에 오는 경우도 흔해(예: "{months_saved} 빨리 끝나고") 앞뒤 모두 본다.
_CONTEXT_WINDOW_CHARS = 20

_REASON_CONNECTORS = ("라서", "때문", "므로", "이라", "니까", "덕분", "탓", "이어서", "아서", "어서")
_ACTION_VERBS = (
    "확인", "상담", "살펴", "문의", "비교", "저축", "상환", "갚", "채우", "준비",
    "신청", "검토", "점검", "조정", "줄이",
)
_VAGUE_PHRASES = ("상태를 고려해", "관련 내용을", "관련 사항을", "고려해 보세요", "참고해 보세요")

_WS_ONLY_RE = re.compile(r"\s+")


def _unlabeled_placeholder_names(text: str) -> list[str]:
    """text 안에서 맥락어 없이(라벨 없이) 값만 나열된 플레이스홀더 이름 목록.

    PLACEHOLDER_CONTEXT에 없는 키는 검사하지 않는다(표에 없는 키는 검사 생략).
    """
    names: list[str] = []
    for match in slotfill.PLACEHOLDER_RE.finditer(text):
        name = match.group(1)
        context_words = PLACEHOLDER_CONTEXT.get(name)
        if not context_words:
            continue
        before = _WS_ONLY_RE.sub("", text[: match.start()])[-_CONTEXT_WINDOW_CHARS:]
        after = _WS_ONLY_RE.sub("", text[match.end():])[:_CONTEXT_WINDOW_CHARS]
        if not any(word in before or word in after for word in context_words):
            names.append(name)
    return names


def _extra_quality_problems(text: str, *, location: str) -> list[str]:
    """slotfill.validate 이후에 추가로 거는 품질 검사(2026-09-06 PMO 지적 반영).

    (a) unlabeled_placeholder: 플레이스홀더 근처에 맥락어가 하나도 없음.
    (b) not_informative: summary에 이유 연결어와 행동 동사가 둘 다 있어야 한다(둘 중
        하나라도 없으면 실패). 항목 이유(reason_a/b/c)는 원래 문장이 짧고 사실
        나열형이라 이 검사는 summary에만 건다.
    (c) vague_phrase: "상태를 고려해" 같은 빈말이 있으면 실패(위치 무관).
    """
    problems: list[str] = []
    if _unlabeled_placeholder_names(text):
        problems.append("unlabeled_placeholder")
    if location == "summary":
        has_connector = any(c in text for c in _REASON_CONNECTORS)
        has_verb = any(v in text for v in _ACTION_VERBS)
        if not (has_connector and has_verb):
            problems.append("not_informative")
    if any(p in text for p in _VAGUE_PHRASES):
        problems.append("vague_phrase")
    return problems

# R0~R10 규칙 요지(숫자 없는 한 문장). app/core/rules.py의 각 규칙 조건을 그대로 요약한다.
# R7은 존재하지 않는 규칙 번호다(rules.py에 R0,R1,R2,R3,R4,R5,R6,R8,R9,R10만 있다).
RULE_GIST: dict[str, str] = {
    "R0": "연체 신호가 있거나 이번 달 남는 돈이 마이너스이거나 상환 비율이 매우 높으면 새 대출보다 공적 상담을 먼저 안내한다.",
    "R1": "신용대출 계열 대출의 금리가 일정 수준 이상이고 소득 증가나 고용 변동 신호가 있으면 금리인하요구권 신청을 안내한다.",
    "R2": "이번 달 남는 돈을 금리가 가장 높은 대출에 추가 상환하면 기간이 줄고 이자가 절감된다.",
    "R3": "잔여 기간이 충분히 남고 금리가 일정 수준 이상이며 수수료 회수 기간이 잔여 기간보다 짧으면 다른 상품 금리를 비교해볼 수 있다.",
    "R4": "비상금이 생활비 대비 목표 개월 수보다 적으면 비상금부터 채우는 것을 안내한다.",
    "R5": "만기나 거치 종료가 얼마 남지 않은 대출은 정리 금액과 이번 달 여력을 확인하도록 안내한다.",
    "R6": "금리가 높고 잔액이 크지 않은 소액 대출은 다른 대출보다 먼저 줄이도록 안내한다.",
    "R8": "저축률이 생애 단계 기준보다 낮으면 자동이체 저축 계획을 세우도록 안내한다.",
    "R9": "원리금상환비율이 생애 단계 기준을 넘으면 상환 구조를 점검하고 다른 상품 금리를 비교해볼 수 있다.",
    "R10": "나이가 일정 기준 이상이고 노후 확정소득이 필수지출을 충분히 충당하지 못하면 노후자금 시뮬레이션을 안내한다.",
}

_CATEGORY_LABELS_KR: dict[str, str] = {
    "deposit": "예금", "saving": "적금", "mortgage": "주택담보대출",
    "jeonse": "전세자금대출", "credit": "신용대출", "policy": "정책서민금융",
}

_ESTIMATED_FIELD_LABELS_KR: dict[str, str] = {
    "amount": "금액", "term_months": "기간", "category": "카테고리",
    "credit_band": "신용 구간", "repay_method": "상환방식", "target_loan_id": "대상 대출",
}

# insights.py의 _LOAN_TYPE_LABELS_KR와 같은 내용(별도 모듈 간 private 심볼 의존을
# 피하기 위해 그대로 옮겨 둔다).
_LOAN_TYPE_LABELS_KR: dict[str, str] = {
    "credit": "신용대출",
    "mortgage": "주택담보대출",
    "jeonse": "전세자금대출",
    "student": "학자금대출",
    "card_loan": "카드론",
    "overdraft": "마이너스통장",
    "policy": "정책상품대출",
    "other": "기타 대출",
}

_CAPACITY_BAND_LABELS_KR: dict[str, str] = {
    "comfortable": "여유",
    "ok": "보통",
    "tight": "빠듯",
    "negative": "부족",
}

_ORDINAL_KR = {1: "첫 번째", 2: "두 번째", 3: "세 번째"}
_RANK_LETTER = {1: "a", 2: "b", 3: "c"}
_REASON_LOCATIONS = ["reason_a", "reason_b", "reason_c"]

_DIGIT_RE = re.compile(r"[0-9]")


def _sort_basis_label(sort_key: SortKey) -> str:
    if sort_key == SortKey.MONTHLY_PAYMENT:
        return "월 납입액이 적은 순"
    if sort_key == SortKey.RATE:
        return "금리가 낮은 순"
    return "총이자가 적은 순"


def _rate_kind_label(item: CompareItem) -> str:
    if item.rate_semantics == RateSemantics.DISCLOSED_AVG_RATE:
        return "신용점수 구간별 전월 평균금리"
    if item.rate_kind == "avg":
        return "평균금리"
    if item.rate_kind == "base":
        return "기준금리"
    if item.rate_kind == "min":
        return "최저금리"
    return "공시 금리"


def _vs_current_phrase(vs: Optional[int]) -> str:
    if vs is None:
        return "비교 기준 없음"
    if vs < 0:
        return "현재보다 총이자 절감"
    if vs > 0:
        return "현재보다 총이자 증가"
    return "현재 대출과 같음"


# ---------------------------------------------------------------------------
# 슬롯 조립 (facts/placeholders/values)
# ---------------------------------------------------------------------------


def compare_slots(result: CompareResult, profile: Optional[UserProfile]) -> tuple[dict, dict, dict]:
    """비교 결과 상위 3개 항목만으로 (facts, placeholders, values)를 만든다.

    facts는 전부 숫자 없는 범주형 문자열이다(LLM에 그대로 전달). `CompareItem`에는
    `lender_group`/`rate_type` 필드가 없으므로(anon_label 문자열에 그룹명이 이미
    포함되어 있다) SPEC 2.8 원문의 items 필드 중 그 둘은 만들지 않는다.
    """
    ctx = result.context
    top_items = result.items[:3]

    target_loan = None
    if ctx.target_loan_id and profile is not None:
        target_loan = next((l for l in profile.loans if l.id == ctx.target_loan_id), None)

    facts: dict[str, Any] = {
        "category": _CATEGORY_LABELS_KR.get(ctx.category.value, ctx.category.value),
        "sort_basis": _sort_basis_label(ctx.sort_key),
        "has_current_loan": "예" if target_loan is not None else "아니오",
        "estimated_fields": [_ESTIMATED_FIELD_LABELS_KR.get(f, f) for f in ctx.estimated_fields],
        "items": [],
    }
    if target_loan is not None:
        facts["current_loan_type"] = _LOAN_TYPE_LABELS_KR.get(target_loan.loan_type.value, "기타 대출")

    placeholders: dict[str, str] = {
        "amount": "비교에 사용한 금액",
        "term_months": "비교에 사용한 기간",
        "candidates_total": "비교 대상 전체 상품 개수",
        "shown_count": "실제로 보여준 상위 상품 개수",
    }
    values: dict[str, str] = {
        "amount": f"{ctx.amount:,}원",
        "term_months": f"{ctx.term_months}개월",
        "candidates_total": f"{result.candidates_total}개",
        "shown_count": f"{len(top_items)}개",
    }

    for idx, item in enumerate(top_items, start=1):
        letter = _RANK_LETTER[idx]
        ordinal = _ORDINAL_KR[idx]
        facts["items"].append({
            "label": item.anon_label,
            "rate_kind": _rate_kind_label(item),
            "vs_current": _vs_current_phrase(item.vs_current_total_interest),
            "notes": list(item.notes),
        })

        placeholders[f"label_{letter}"] = f"{ordinal} 상품의 익명 라벨"
        values[f"label_{letter}"] = item.anon_label

        placeholders[f"rate_{letter}"] = f"{ordinal} 상품의 금리"
        values[f"rate_{letter}"] = f"{item.rate:.4g}%"

        if item.monthly_payment is not None:
            placeholders[f"monthly_{letter}"] = f"{ordinal} 상품의 월 납입액"
            values[f"monthly_{letter}"] = f"{item.monthly_payment:,}원"

        if item.total_interest is not None:
            placeholders[f"total_{letter}"] = f"{ordinal} 상품의 총이자"
            values[f"total_{letter}"] = f"{item.total_interest:,}원"

        if item.vs_current_total_interest is not None:
            placeholders[f"vs_{letter}"] = f"{ordinal} 상품과 현재 대출의 총이자 차이"
            values[f"vs_{letter}"] = f"{abs(item.vs_current_total_interest):,}원"

    return facts, placeholders, values


def _action_number_label(key: str) -> Optional[str]:
    """actions.py의 라벨 맵에서 key에 대응하는 한국어 설명을 찾는다(없으면 None)."""
    return (
        actions_service._RATIO_PCT_LABELS.get(key)
        or actions_service._PCT_LABELS.get(key)
        or actions_service._MONTHS_LABELS.get(key)
        or actions_service._WON_LABELS.get(key)
    )


def action_slots(card: ActionCard, profile: UserProfile, capacity: Capacity) -> tuple[dict, dict, dict]:
    """행동 카드 1건으로 (facts, placeholders, values)를 만든다.

    SPEC 2.8 원문은 facts에 `rule_id`("R0" 등) 원문을 나열하지만, 그 문자열 자체에
    숫자가 있어 D4(`assert_no_digits`)를 항상 위반한다(모든 R0~R10 규칙 코드에 최소
    한 자리 숫자가 있다). `gist`(`RULE_GIST[rule_id]`)가 이미 규칙의 의미를 숫자 없는
    문장으로 전달하므로 rule_id는 내부 조회에만 쓰고 LLM 페이로드에는 넣지 않는다.
    같은 이유로 `card.numbers`의 라벨 자체에 숫자가 섞인 키가 있다면(현재
    `app/services/actions.py`의 라벨 맵에는 없다) placeholders/values에서 제외한다.
    """
    facts: dict[str, Any] = {
        "title": card.title,
        "gist": RULE_GIST.get(card.rule_id, ""),
        "capacity_band": _CAPACITY_BAND_LABELS_KR.get(capacity.band.value, capacity.band.value),
        "safe_mode": "예" if card.safe_mode else "아니오",
    }
    if card.related_loan_ids:
        loan = next((l for l in profile.loans if l.id == card.related_loan_ids[0]), None)
        if loan is not None:
            facts["loan_type"] = _LOAN_TYPE_LABELS_KR.get(loan.loan_type.value, "기타 대출")

    placeholders: dict[str, str] = {}
    values: dict[str, str] = {}
    for key, value in card.numbers.items():
        if value is None:
            continue
        label = _action_number_label(key)
        if label is None or _DIGIT_RE.search(label):
            continue  # 라벨을 모르거나 라벨 자체에 숫자가 있으면 LLM에 노출하지 않는다(D4)
        # 2026-09-06 PMO 지적: LLM이 라벨 없이 플레이스홀더만 나열하는 문제(예: "현재
        # {net_monthly}이나 {debt_service_ratio} 상태를 고려해")가 있었다. 플레이스홀더
        # 설명 자체에 "반드시 라벨과 함께 쓸 것"을 못박아 각 항목마다 반복 상기시킨다.
        placeholders[key] = f"{label}(반드시 라벨과 함께 쓸 것)"
        values[key] = actions_service.format_action_number(key, value)

    return facts, placeholders, values


# ---------------------------------------------------------------------------
# LLM 호출 + 검증
# ---------------------------------------------------------------------------


def _call_llm_explain(
    provider: Any, facts: dict[str, Any], placeholders: dict[str, str], template_id: str, schema: dict,
    *, system: str = EXPLAIN_SYSTEM, deadline_seconds: Optional[float] = None,
) -> tuple[Optional[dict], Optional[str], int, list[str]]:
    """LLM 호출 1회를 시도한다. (data, model, latency_ms, problems)를 돌려준다.

    data가 None이면 반드시 템플릿으로 가야 한다(problems에 이유 코드가 있다).
    latency_ms는 실제로 provider.explain을 호출한 구간만 잰다(payload 검증 등
    호출 전 단계는 포함하지 않는다). `deadline_seconds`를 생략하면 provider.explain에
    그 인자를 아예 넘기지 않는다(기존 테스트 더블처럼 그 키워드를 모르는 provider와도
    호환되도록). 넘길 때는 SPEC 2.9의 대화 화면 설명(`explain_chat_compare`)처럼 provider
    기본값보다 짧은 체인 상한을 강제하고 싶을 때만 지정한다. `system`을 생략하면 공용
    EXPLAIN_SYSTEM을 쓰고, action_card_v1처럼 구조화된 지시가 필요하면 호출부가
    ACTION_EXPLAIN_SYSTEM 등을 넘긴다.
    """
    if not hasattr(provider, "explain") or not provider.available():
        return None, None, 0, ["llm_unavailable"]

    payload = {"facts": facts, "placeholders": placeholders}
    try:
        slotfill.assert_no_digits(payload)
    except ValueError:
        return None, None, 0, ["payload_has_digits"]

    kwargs: dict[str, Any] = {"schema": schema}
    if deadline_seconds is not None:
        kwargs["deadline_seconds"] = deadline_seconds

    started = time.monotonic()
    try:
        result = provider.explain(payload, template_id, system, **kwargs)
    except LLMUnavailable:
        latency_ms = int((time.monotonic() - started) * 1000)
        return None, None, latency_ms, ["llm_unavailable"]
    except Exception:  # noqa: BLE001 - SEV4 2026-09-06 리뷰: 예상 밖 예외도 템플릿
        # 폴백으로 떨어지게 하고, 원문은 로그로만 남긴다(사용자 화면에는 노출하지 않는다).
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.exception("explain LLM 호출 실패, 템플릿으로 대체")
        return None, None, latency_ms, ["llm_error"]
    latency_ms = int((time.monotonic() - started) * 1000)

    data = result.data if isinstance(result.data, dict) else None
    if data is None and result.text:
        try:
            parsed = json.loads(result.text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            data = parsed
    if data is None:
        return None, result.model, latency_ms, ["no_json"]
    return data, result.model, latency_ms, []


def _process_text(
    raw: Any, allowed: set[str], banned: list[str], values: dict[str, str],
    *, max_chars: int, max_sentences: int, location: str,
) -> tuple[Optional[str], list[str]]:
    """sanitize -> validate -> fill 파이프라인 1건. 문제가 있으면 (None, [위치가 접두된 코드들])."""
    if not isinstance(raw, str):
        return None, [f"{location}:empty"]
    sanitized = slotfill.sanitize(raw)
    problems = slotfill.validate(sanitized, allowed, banned, max_chars=max_chars, max_sentences=max_sentences)
    problems = problems + _extra_quality_problems(sanitized, location=location)
    if problems:
        return None, [f"{location}:{p}" for p in problems]
    try:
        filled = slotfill.fill(sanitized, values)
    except KeyError as exc:
        return None, [f"{location}:missing_value:{exc}"]
    return filled, []


# ---------------------------------------------------------------------------
# 템플릿 폴백
# ---------------------------------------------------------------------------


def _vs_sentence(vs: Optional[int], vs_amount: Optional[str]) -> str:
    if vs is None or vs_amount is None:
        return ""
    if vs < 0:
        return f" 현재 대출보다 총이자를 {vs_amount} 줄일 수 있는 조건이에요."
    if vs > 0:
        return f" 현재 대출보다 총이자가 {vs_amount} 더 들어요."
    return ""


def _template_compare(
    top_items: list[CompareItem], facts: dict[str, Any], values: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """LLM 실패/검증 실패 시 쓰는 결정론적 템플릿(compare_summary_v1). SPEC 2.8 문장 그대로
    (은/는은 `slotfill.josa`로 고른다)."""
    if not top_items:
        return "지금 조건으로는 비교할 수 있는 상품이 없어요.", {}

    category = facts["category"]
    sort_basis = facts["sort_basis"]
    label_a = values["label_a"]
    summary = (
        f"{category} 공시 상품 {values['candidates_total']} 중 {sort_basis}으로 "
        f"상위 {values['shown_count']}를 골랐어요. "
        f"{label_a}{slotfill.josa(label_a, '은/는')} 금리 {values['rate_a']}, "
        f"월 납입 {values.get('monthly_a', '확인 필요')}, "
        f"총이자 {values.get('total_a', '확인 필요')}로 첫 번째예요."
    )
    summary += _vs_sentence(top_items[0].vs_current_total_interest, values.get("vs_a"))
    if facts.get("estimated_fields"):
        summary += " 금액과 기간은 프로필에서 추정한 값이라 조건 확인에서 바꿀 수 있어요."

    item_reasons: dict[str, str] = {}
    for idx, item in enumerate(top_items, start=1):
        letter = _RANK_LETTER[idx]
        ordinal = _ORDINAL_KR[idx]
        sentence = (
            f"{sort_basis} 기준 {ordinal}이에요. "
            f"금리 {values.get(f'rate_{letter}', '확인 필요')}({_rate_kind_label(item)}), "
            f"월 납입 {values.get(f'monthly_{letter}', '확인 필요')}, "
            f"총이자 {values.get(f'total_{letter}', '확인 필요')}."
        )
        sentence += _vs_sentence(item.vs_current_total_interest, values.get(f"vs_{letter}"))
        item_reasons[str(idx)] = sentence

    return summary, item_reasons


def _template_action(card: ActionCard) -> tuple[str, dict[str, str]]:
    """LLM 실패/검증 실패 시 쓰는 템플릿(action_card_v1). card.summary를 그대로 쓴다."""
    return card.summary, {}


def _guard_template(summary: str, item_reasons: dict[str, str], banned: list[str]) -> tuple[str, dict[str, str]]:
    """템플릿 결과도 최종적으로 guardrails.check_text를 통과해야 한다(SPEC 2.8)."""
    if guardrails.check_text(summary, banned):
        return "설명을 표시할 수 없어 계산 결과만 제공합니다.", {}
    return summary, item_reasons


# ---------------------------------------------------------------------------
# 저장/조회
# ---------------------------------------------------------------------------


def _get_stored_row(kind: str, ref_id: str) -> Optional[dict[str, Any]]:
    """저장된 payload_json을 원본 dict 그대로 돌려준다(비공개 키 포함, 예: `_profile_id`).

    `get_stored`는 이 dict를 `ExplainResult.model_validate`로 감싸 모델에 없는 키를
    조용히 버린다. `explain_compare`의 캐시 유효성 검사(SEV3 #6)는 그 버려지는 키가
    필요해 이 내부 함수를 직접 쓴다.
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM explanations WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])
    finally:
        conn.close()


def get_stored(kind: str, ref_id: str) -> Optional[ExplainResult]:
    raw = _get_stored_row(kind, ref_id)
    if raw is None:
        return None
    return ExplainResult.model_validate(raw)


def _save_explanation(result: ExplainResult, *, extra: Optional[dict[str, Any]] = None) -> None:
    """`extra`가 있으면 저장되는 JSON에 `ExplainResult` 필드 외의 비공개 키로 함께 넣는다
    (SPEC/SEV3 #6: `CompareContext`의 `_current_total_interest`와 같은 방식. `ExplainResult`
    모델 자체에는 필드를 추가하지 않는다 - 결정 기록·재현은 이 테이블과 무관하다)."""
    conn = db.get_conn()
    try:
        payload = result.model_dump(mode="json")
        if extra:
            payload.update(extra)
        conn.execute(
            """
            INSERT OR REPLACE INTO explanations(kind, ref_id, payload_json, created_at)
            VALUES (?,?,?,?)
            """,
            (
                result.kind,
                result.ref_id,
                json.dumps(payload, ensure_ascii=False),
                result.created_at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------


def explain_compare(decision_id: str, provider: Any, *, refresh: bool = False) -> Optional[ExplainResult]:
    """공시 비교 결과 설명. `decisions.get`이 없거나 compare 결정이 아니면 None.

    저장된 설명의 payload_json에는 생성 당시 세션 프로필 id를 비공개 키 `_profile_id`로
    함께 저장한다(SEV3 2026-09-06 리뷰). `decisions` 테이블에는 profile_id 컬럼이 없어
    같은 decision_id를 다른 페르소나로 전환한 뒤에도 조회할 수 있는데, 캐시를 그대로
    재사용하면 이전 페르소나 기준으로 만든 문장(현재 대출 유무 등 facts)이 그대로
    나온다. 현재 프로필과 다르면 캐시를 쓰지 않고 새로 만든다.
    """
    record = decisions_service.get(decision_id)
    if record is None or record.kind != "compare":
        return None

    profile = session_service.get_profile()
    current_profile_id = profile.id if profile is not None else None

    if not refresh:
        stored_raw = _get_stored_row("compare", decision_id)
        if stored_raw is not None and stored_raw.get("_profile_id") == current_profile_id:
            return ExplainResult.model_validate(stored_raw).model_copy(update={"cached": True})

    result = CompareResult.model_validate(record.result)
    banned = get_banned_terms()
    facts, placeholders, values = compare_slots(result, profile)
    top_items = result.items[:3]
    allowed = set(placeholders.keys())

    data, model, latency_ms, problems = _call_llm_explain(
        provider, facts, placeholders, "compare_summary_v1", COMPARE_SCHEMA,
    )

    all_problems: list[str] = list(problems)
    summary_text: Optional[str] = None
    item_reasons: dict[str, str] = {}

    if data is not None:
        raw_reasons = data.get("reasons")
        if not isinstance(raw_reasons, list):
            raw_reasons = []
        summary_text, summary_problems = _process_text(
            data.get("summary"), allowed, banned, values, max_chars=300, max_sentences=3, location="summary",
        )
        all_problems.extend(summary_problems)

        if len(raw_reasons) < len(top_items):
            all_problems.append("reasons_count_mismatch")

        reasons_filled: list[Optional[str]] = []
        for idx in range(len(top_items)):
            raw = raw_reasons[idx] if idx < len(raw_reasons) else None
            filled, reason_problems = _process_text(
                raw, allowed, banned, values, max_chars=140, max_sentences=1, location=_REASON_LOCATIONS[idx],
            )
            all_problems.extend(reason_problems)
            reasons_filled.append(filled)

        if not all_problems:
            item_reasons = {str(idx + 1): reasons_filled[idx] for idx in range(len(top_items))}

    if summary_text is not None and not all_problems:
        source, llm_used, final_model = "llm", True, model
    else:
        source, llm_used, final_model = "template", False, None
        summary_text, item_reasons = _template_compare(top_items, facts, values)
        summary_text, item_reasons = _guard_template(summary_text, item_reasons, banned)

    explain_result = ExplainResult(
        kind="compare",
        ref_id=decision_id,
        summary=summary_text,
        item_reasons=item_reasons,
        source=source,
        llm_used=llm_used,
        model=final_model,
        latency_ms=latency_ms,
        template_id="compare_summary_v1",
        prompt_version=PROMPT_VERSION,
        problems=all_problems,
        cached=False,
        created_at=datetime.now(),
    )
    _save_explanation(explain_result, extra={"_profile_id": current_profile_id})
    return explain_result


def explain_action(
    action_id: str,
    provider: Any,
    profile: UserProfile,
    params: PolicyParams,
    *,
    today: date,
    refresh: bool = False,
) -> Optional[ExplainResult]:
    """행동 카드 설명. `evaluate_rules` 원시 카드에서 action_id로 찾는다(없으면 None)."""
    cards = actions_service._raw_actions(profile, params, today=today)
    card = next((c for c in cards if c.id == action_id), None)
    if card is None:
        return None

    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)

    # ref_id 지문에 numbers 외에 capacity.band/safe_mode/related_loan_ids도 포함한다(SEV3
    # 2026-09-06 리뷰: 변동지출이 바뀌어 여력 구간(band)만 달라져도 카드 문구(facts의
    # capacity_band)는 달라지는데, numbers만으로 지문을 만들면 numbers가 우연히 같을 때
    # 오래된 설명이 새 여력 구간에서도 그대로 캐시될 수 있다).
    cache_fingerprint_input = {
        "numbers": card.numbers,
        "capacity_band": capacity.band.value,
        "safe_mode": card.safe_mode,
        "related_loan_ids": list(card.related_loan_ids),
    }
    ref_id = f"{profile.id}:{action_id}@{hashing.fingerprint(cache_fingerprint_input)[:8]}"

    if not refresh:
        stored = get_stored("action", ref_id)
        if stored is not None:
            return stored.model_copy(update={"cached": True})

    banned = get_banned_terms()
    facts, placeholders, values = action_slots(card, profile, capacity)
    allowed = set(placeholders.keys())

    # 2026-09-06 PMO 지적("말도 안 되는 답변"): 안전 모드(R0) 카드는 위기 상황 문장이라
    # 결정론 원칙(SPEC 0.1)을 지켜 LLM을 아예 호출하지 않고 항상 카드 템플릿을 쓴다.
    # provider.explain은 이 분기에서 한 번도 불리지 않는다.
    if card.safe_mode:
        summary_text, item_reasons = _template_action(card)
        summary_text, item_reasons = _guard_template(summary_text, item_reasons, banned)
        explain_result = ExplainResult(
            kind="action",
            ref_id=ref_id,
            summary=summary_text,
            item_reasons=item_reasons,
            source="template",
            llm_used=False,
            model=None,
            latency_ms=0,
            template_id="action_card_v1",
            prompt_version=PROMPT_VERSION,
            problems=["safe_mode_template"],
            cached=False,
            created_at=datetime.now(),
        )
        _save_explanation(explain_result)
        return explain_result

    data, model, latency_ms, problems = _call_llm_explain(
        provider, facts, placeholders, "action_card_v1", ACTION_SCHEMA, system=ACTION_EXPLAIN_SYSTEM,
    )

    all_problems: list[str] = list(problems)
    summary_text = None
    item_reasons = {}

    if data is not None:
        summary_text, summary_problems = _process_text(
            data.get("summary"), allowed, banned, values, max_chars=300, max_sentences=3, location="summary",
        )
        all_problems.extend(summary_problems)

    if summary_text is not None and not all_problems:
        source, llm_used, final_model = "llm", True, model
    else:
        source, llm_used, final_model = "template", False, None
        summary_text, item_reasons = _template_action(card)
        summary_text, item_reasons = _guard_template(summary_text, item_reasons, banned)

    explain_result = ExplainResult(
        kind="action",
        ref_id=ref_id,
        summary=summary_text,
        item_reasons=item_reasons,
        source=source,
        llm_used=llm_used,
        model=final_model,
        latency_ms=latency_ms,
        template_id="action_card_v1",
        prompt_version=PROMPT_VERSION,
        problems=all_problems,
        cached=False,
        created_at=datetime.now(),
    )
    _save_explanation(explain_result)
    return explain_result


# ---------------------------------------------------------------------------
# 대화 화면 compare 안내 문장 (SPEC 2.9, 2.8 재사용)
# ---------------------------------------------------------------------------

CHAT_COMPARE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"summary": {"type": "STRING"}},
    "required": ["summary"],
}


def _chat_compare_template(category_label: str, followup_changed: list[str], values: dict[str, str]) -> str:
    """LLM 실패/검증 실패 시 쓰는 템플릿(chat_compare_prep_v1). 지금까지 routes.py에 그대로
    있던 안내 문장과 같은 내용이다. "아래 버튼으로..." 유도 문장은 여기서 붙이지 않는다
    (app/api/routes.py가 LLM 문장이든 템플릿 문장이든 동일하게 뒤에 덧붙인다)."""
    if followup_changed:
        labels = ", ".join(followup_changed)
        return (
            f"이전 조건에서 {labels}만 바꿔 다시 준비했어요. {category_label}, 금액 {values['amount']}, "
            f"기간 {values['term_months']} 기준입니다."
        )
    return (
        f"{category_label} 비교 조건을 준비했어요. 금액 {values['amount']}, 기간 {values['term_months']} "
        "기준입니다."
    )


def explain_chat_compare(ctx: CompareContext, followup_changed: list[str], provider: Any) -> ExplainResult:
    """대화 화면(SPEC 2.9)의 compare 의도 안내 문장. `followup_changed`는 이미 한국어로
    번역된 라벨 목록이다(호출부 `app/api/routes.py`가 `_FOLLOWUP_LABELS`로 변환해 넘긴다).

    저장하지 않는다(`explanations` 테이블 미사용, `cached`는 항상 False. 대화 로그가
    이미 이 응답 문장을 기록한다). 체인 상한은 `config/llm.yaml`의
    `chat_explain_deadline_seconds`(기본 8초)로 `explain_total_deadline_seconds`보다 짧게
    강제한다(대화 화면은 "생각 과정"을 보여주며 기다리므로 더 빨리 포기하고 템플릿으로
    가는 편이 낫다).
    """
    category_label = _CATEGORY_LABELS_KR.get(ctx.category.value, ctx.category.value)
    facts: dict[str, Any] = {
        "category": category_label,
        "estimated_fields": [_ESTIMATED_FIELD_LABELS_KR.get(f, f) for f in ctx.estimated_fields],
        "changed_fields": list(followup_changed),
        "has_max_rate": "예" if ctx.max_rate is not None else "아니오",
        "has_exclude_companies": "예" if ctx.exclude_companies else "아니오",
    }
    placeholders: dict[str, str] = {"amount": "비교 금액", "term_months": "비교 기간"}
    values: dict[str, str] = {
        "amount": f"{ctx.amount:,}원",
        "term_months": f"{ctx.term_months}개월",
    }
    if ctx.max_rate is not None:
        placeholders["max_rate"] = "금리 상한"
        values["max_rate"] = f"{ctx.max_rate:.4g}%"

    banned = get_banned_terms()
    allowed = set(placeholders.keys())

    data, model, latency_ms, problems = _call_llm_explain(
        provider, facts, placeholders, "chat_compare_prep_v1", CHAT_COMPARE_SCHEMA,
        deadline_seconds=CHAT_EXPLAIN_DEADLINE_SECONDS,
    )

    all_problems: list[str] = list(problems)
    summary_text: Optional[str] = None

    if data is not None:
        summary_text, summary_problems = _process_text(
            data.get("summary"), allowed, banned, values, max_chars=220, max_sentences=2, location="summary",
        )
        all_problems.extend(summary_problems)

    if summary_text is not None and not all_problems:
        source, llm_used, final_model = "llm", True, model
    else:
        source, llm_used, final_model = "template", False, None
        summary_text = _chat_compare_template(category_label, followup_changed, values)
        summary_text, _ = _guard_template(summary_text, {}, banned)

    return ExplainResult(
        kind="compare",
        ref_id=f"chat:{ctx.category.value}:{ctx.amount}:{ctx.term_months}",
        summary=summary_text,
        item_reasons={},
        source=source,
        llm_used=llm_used,
        model=final_model,
        latency_ms=latency_ms,
        template_id="chat_compare_prep_v1",
        prompt_version=PROMPT_VERSION,
        problems=all_problems,
        cached=False,
        created_at=datetime.now(),
    )


# ---------------------------------------------------------------------------
# 계산형 자유 질의(what-if) 결론 문장 (SPEC 2.13, 2.8 재사용)
# ---------------------------------------------------------------------------

WHATIF_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"summary": {"type": "STRING"}},
    "required": ["summary"],
}

_WHATIF_TOOL_LABELS_KR: dict[str, str] = {
    "extra_payment": "추가 상환",
    "lump_sum": "일시 상환",
    "refinance": "대환",
    "retirement_age": "은퇴 시점 변경",
}

_WHATIF_DIGIT_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def _loan_type_label_no_digits(label: Optional[str]) -> str:
    """`WhatIfResult.target_loan_label`(예: "카드론(잔액 2,400,000원, 17.5%)")에서 괄호 안
    숫자 부분을 떼어 낸 숫자 없는 대출 종류 라벨만 남긴다(D4: facts는 숫자를 보내면 안 된다)."""
    if not label:
        return "대출"
    return _WHATIF_DIGIT_PAREN_RE.sub("", label).strip() or "대출"


def whatif_slots(result: WhatIfResult) -> tuple[dict, dict, dict]:
    """what-if 결과 1건으로 (facts, placeholders, values)를 만든다. facts=도구·대출 종류·
    방향(숫자 없음), placeholders/values=금액·개월·이자(도구별로 있는 것만)."""
    facts: dict[str, Any] = {
        "tool": _WHATIF_TOOL_LABELS_KR.get(result.tool, result.tool),
        "loan_type": _loan_type_label_no_digits(result.target_loan_label),
    }
    placeholders: dict[str, str] = {}
    values: dict[str, str] = {}

    def add(key: str, raw_value: Any, label: str, formatter) -> None:
        if raw_value is None:
            return
        placeholders[key] = label
        values[key] = formatter(raw_value)

    won = lambda v: f"{v:,}원"
    months = lambda v: f"{v:,}개월"

    if result.tool == "extra_payment":
        facts["direction"] = "상환 기간 단축"
        add("extra_monthly", result.inputs.get("extra_monthly"), "매월 추가 상환액", won)
        add("months_saved", result.deltas.get("months_saved"), "단축 개월", months)
        add("interest_saved", result.deltas.get("interest_saved"), "절감 이자", won)
    elif result.tool == "lump_sum":
        facts["direction"] = "상환 기간 단축"
        add("lump_sum_amount", result.inputs.get("amount"), "일시 상환액", won)
        add("months_saved", result.deltas.get("months_saved"), "단축 개월", months)
        add("interest_saved", result.deltas.get("interest_saved"), "절감 이자", won)
    elif result.tool == "refinance":
        delta = result.deltas.get("total_cost_delta") or 0
        facts["direction"] = "총이자 절감" if delta < 0 else ("총이자 증가" if delta > 0 else "총이자 변화 없음")
        add("new_rate", result.inputs.get("new_rate"), "새 금리", lambda v: f"{v:.4g}%")
        add("total_cost_delta", abs(delta) if delta else 0, "총이자 차이", won)
    else:  # retirement_age
        delta = result.deltas.get("shortfall_delta") or 0
        facts["direction"] = "부족액 감소" if delta < 0 else ("부족액 증가" if delta > 0 else "부족액 변화 없음")
        add("retirement_age_after", result.after.get("retirement_age"), "새 은퇴 나이", lambda v: f"{v}세")
        add("shortfall_delta", abs(delta), "노후 부족액 차이", won)

    return facts, placeholders, values


def _template_whatif_conclusion(result: WhatIfResult) -> str:
    """LLM 실패/검증 실패 시 쓰는 결론 문장(template_id whatif_v1)."""
    label = result.target_loan_label or "대상 대출"
    if result.tool == "extra_payment":
        return (
            f"매달 {result.inputs.get('extra_monthly', 0):,}원을 {label}에 추가로 갚으면 "
            f"{result.deltas.get('months_saved', 0):,}개월 빨리 끝나고 이자 "
            f"{result.deltas.get('interest_saved', 0):,}원을 아껴요."
        )
    if result.tool == "lump_sum":
        return (
            f"{result.inputs.get('amount', 0):,}원을 {label}에 한 번에 갚으면 "
            f"{result.deltas.get('months_saved', 0):,}개월 빨리 끝나고 이자 "
            f"{result.deltas.get('interest_saved', 0):,}원을 아껴요."
        )
    if result.tool == "refinance":
        delta = result.deltas.get("total_cost_delta", 0) or 0
        direction = "줄어요" if delta < 0 else ("늘어요" if delta > 0 else "그대로예요")
        return (
            f"{label}을 금리 {result.inputs.get('new_rate', 0):.4g}%로 갈아타면 총이자가 "
            f"{abs(delta):,}원 {direction}."
        )
    # retirement_age
    delta = result.deltas.get("shortfall_delta", 0) or 0
    direction = "줄어요" if delta < 0 else ("늘어요" if delta > 0 else "그대로예요")
    return (
        f"은퇴를 {result.after.get('retirement_age', 0)}세로 하면 노후 부족액이 "
        f"{abs(delta):,}원 {direction}."
    )


def explain_chat_whatif(
    result: WhatIfResult, provider: Any,
) -> tuple[str, bool, Optional[str], int, list[str]]:
    """SPEC 2.13: what-if 결론 1문장을 LLM 슬롯 필링으로 만든다. 실패·불가 시 템플릿.

    반환: (text, llm_used, model, latency_ms, problems). `ExplainResult`는 kind가
    "compare"|"action"으로 고정돼 있어(app/models.py) whatif 전용 kind를 담을 수 없으므로,
    `explain_chat_compare`와 달리 저장하지 않고 이 얕은 튜플만 돌려준다(대화 로그가 이미
    응답 문장을 기록한다).
    """
    facts, placeholders, values = whatif_slots(result)
    banned = get_banned_terms()
    allowed = set(placeholders.keys())

    data, model, latency_ms, problems = _call_llm_explain(
        provider, facts, placeholders, "whatif_v1", WHATIF_SCHEMA,
        deadline_seconds=CHAT_EXPLAIN_DEADLINE_SECONDS,
    )

    all_problems: list[str] = list(problems)
    summary_text: Optional[str] = None

    if data is not None:
        summary_text, summary_problems = _process_text(
            data.get("summary"), allowed, banned, values, max_chars=160, max_sentences=1, location="summary",
        )
        all_problems.extend(summary_problems)

    if summary_text is not None and not all_problems:
        return summary_text, True, model, latency_ms, all_problems

    template_text = _template_whatif_conclusion(result)
    template_text, _ = _guard_template(template_text, {}, banned)
    return template_text, False, None, latency_ms, all_problems
