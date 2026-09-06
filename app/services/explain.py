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
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import yaml

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
        placeholders[key] = label
        values[key] = actions_service.format_action_number(key, value)

    return facts, placeholders, values


# ---------------------------------------------------------------------------
# LLM 호출 + 검증
# ---------------------------------------------------------------------------


def _call_llm_explain(
    provider: Any, facts: dict[str, Any], placeholders: dict[str, str], template_id: str, schema: dict,
    *, deadline_seconds: Optional[float] = None,
) -> tuple[Optional[dict], Optional[str], int, list[str]]:
    """LLM 호출 1회를 시도한다. (data, model, latency_ms, problems)를 돌려준다.

    data가 None이면 반드시 템플릿으로 가야 한다(problems에 이유 코드가 있다).
    latency_ms는 실제로 provider.explain을 호출한 구간만 잰다(payload 검증 등
    호출 전 단계는 포함하지 않는다). `deadline_seconds`를 생략하면 provider.explain에
    그 인자를 아예 넘기지 않는다(기존 테스트 더블처럼 그 키워드를 모르는 provider와도
    호환되도록). 넘길 때는 SPEC 2.9의 대화 화면 설명(`explain_chat_compare`)처럼 provider
    기본값보다 짧은 체인 상한을 강제하고 싶을 때만 지정한다.
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
        result = provider.explain(payload, template_id, EXPLAIN_SYSTEM, **kwargs)
    except LLMUnavailable:
        latency_ms = int((time.monotonic() - started) * 1000)
        return None, None, latency_ms, ["llm_unavailable"]
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


def get_stored(kind: str, ref_id: str) -> Optional[ExplainResult]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT payload_json FROM explanations WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        ).fetchone()
        if row is None:
            return None
        return ExplainResult.model_validate(json.loads(row["payload_json"]))
    finally:
        conn.close()


def _save_explanation(result: ExplainResult) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO explanations(kind, ref_id, payload_json, created_at)
            VALUES (?,?,?,?)
            """,
            (
                result.kind,
                result.ref_id,
                json.dumps(result.model_dump(mode="json"), ensure_ascii=False),
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
    """공시 비교 결과 설명. `decisions.get`이 없거나 compare 결정이 아니면 None."""
    record = decisions_service.get(decision_id)
    if record is None or record.kind != "compare":
        return None

    if not refresh:
        stored = get_stored("compare", decision_id)
        if stored is not None:
            return stored.model_copy(update={"cached": True})

    result = CompareResult.model_validate(record.result)
    profile = session_service.get_profile()
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
    _save_explanation(explain_result)
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

    ref_id = f"{profile.id}:{action_id}@{hashing.fingerprint(card.numbers)[:8]}"

    if not refresh:
        stored = get_stored("action", ref_id)
        if stored is not None:
            return stored.model_copy(update={"cached": True})

    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    banned = get_banned_terms()
    facts, placeholders, values = action_slots(card, profile, capacity)
    allowed = set(placeholders.keys())

    data, model, latency_ms, problems = _call_llm_explain(
        provider, facts, placeholders, "action_card_v1", ACTION_SCHEMA,
    )

    all_problems: list[str] = list(problems)
    summary_text: Optional[str] = None
    item_reasons: dict[str, str] = {}

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
