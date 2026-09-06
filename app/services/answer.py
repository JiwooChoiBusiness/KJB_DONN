"""답변 경로(direct/internal/external)와 리소스 패널, KB 답변 형식, 외부 검색 (SPEC 2.11).

원칙(D4는 그대로 유지): 개인신용정보 파생 수치(G3, 프로필·대출·비교 결과 등)는 이 모듈이
LLM에 보내지 않는다. `format_kb_answer`가 참조하는 kb/*.md 문단은 공개 제도 문서라
D4 대상이 아니므로(SPEC 2.11 본문), 여기서는 숫자를 그대로 프롬프트에 넣되 출력에 나온
모든 숫자가 참조 문단 안에 실제로 있는지 검사해서 지어낸 숫자를 막는다.

이 모듈은 `app/api/routes.py`가 조립하는 채팅 파이프라인의 "무엇을 근거로 답했는가"를
`ChatResource`로 정리하고, KB 문서를 읽기 좋은 마크다운으로 요약하며, 외부(Google 검색
그라운딩) 답변을 만든다. 실제 파이프라인 순서(경로 결정, 노드 트레이스 구성)는
`app/api/routes.py`가 맡는다.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from app.data import products
# 주의: `from app.kb import search as kb_search`는 쓰지 않는다. app/kb/__init__.py가
# `from .search import (..., search, ...)`로 함수 `search`를 재노출하면서 패키지 자신의
# `search`라는 이름 자체가 서브모듈이 아니라 그 함수로 재바인딩되기 때문이다(파이썬의
# 흔한 함정: 서브모듈 임포트가 먼저 속성을 만들어도, `__init__.py`의 나중 대입이 덮어쓴다).
# `app/api/routes.py`와 같은 방식으로 패키지 전체를 임포트해 재노출된 이름들
# (load_docs/search/MIN_SCORE/answer)을 속성으로 쓴다.
from app import kb as kb_search
from app.llm import guardrails, slotfill
from app.llm.provider import LLMUnavailable
from app.models import (
    ChatResource,
    PolicyParams,
    ProductCategory,
    SpendingFeatures,
    SpendingLinkedAction,
    SpendingSummary,
    UserProfile,
    WhatIfResult,
)
# insights.py가 이미 만들어 둔 공공·업권 일반어 판별 어휘를 재사용한다(SEV4 2026-09-06
# 리뷰: external 요약의 "OO은행"류 패턴 검사가 같은 기준으로 오탐을 피하게 한다).
from app.services.insights import _PUBLIC_TOKENS, _SECTOR_WORDS
# app/core/spending.py의 생애 이벤트 라벨 맵을 그대로 재사용한다(SPEC 2.12: 대화창 파일
# 첨부 답변도 같은 문구를 쓴다 - app/api/routes.py가 answer.py를 이미 import하는 것과
# 같은 방식으로 app/core 쪽의 준-비공개 상수를 직접 참조한다).
from app.core.spending import _LIFE_EVENT_LABELS
# SPEC 2.16(답변 길이): 길이 규칙 표와 문장 수 세기는 app/services/explain.py에 한 곳에만
# 둔다(explain.py는 answer.py를 import하지 않아 순환 임포트가 생기지 않는다).
from app.services.explain import KB_POINT_MAX_CHARS, KB_SECTION_LIMIT, LENGTH_RULES, count_sentences

_CATEGORY_LABELS_KR: dict[str, str] = {
    "deposit": "예금", "saving": "적금", "mortgage": "주택담보대출",
    "jeonse": "전세자금대출", "credit": "신용대출", "policy": "정책상품",
}

_POLICY_KEY_LABELS_KR: dict[str, str] = {
    "dsr_limit_bank_pct": "은행권 DSR 한도",
    "dsr_limit_nonbank_pct": "비은행권 DSR 한도",
    "emergency_fund_months": "비상금 목표 개월",
    "rate_cut_request_min_rate": "금리인하요구권 기준 금리",
    "refi_rate_gap_min_pct": "대환 비교 기준 금리차",
    "prepay_fee_period_months": "중도상환수수료 부과 기간",
    "national_pension_start_age": "국민연금 수급개시연령",
    "interest_income_tax_pct": "이자소득세율",
}


# ---------------------------------------------------------------------------
# 리소스 빌더 (SPEC 2.11 2절)
# ---------------------------------------------------------------------------


def profile_resources(profile: Optional[UserProfile]) -> list[ChatResource]:
    """프로필·대출 자료를 썼음을 나타내는 리소스. 대출은 한 건으로 묶는다."""
    if profile is None:
        return []
    out = [ChatResource(kind="profile", title="내 정보", ref=profile.id, detail=profile.display_name)]
    if profile.loans:
        total_balance = sum(loan.balance for loan in profile.loans)
        out.append(ChatResource(
            kind="loan",
            title=f"내 대출 {len(profile.loans)}건",
            ref=profile.id,
            detail=f"총잔액 {total_balance:,}원",
        ))
    return out


def calc_resource(kind_label: str, ref: str, detail: str) -> ChatResource:
    """상환표·시나리오·비교 결과·행동 카드 등 계산 엔진 산출물 리소스 1건."""
    return ChatResource(kind="calc", title=kind_label, ref=ref, detail=detail)


def kb_resources(doc_or_hit: Any, sections: list[str]) -> list[ChatResource]:
    """KB 문서(`KbDoc`) 또는 검색 결과(`KbHit`) 어느 쪽을 넘겨도 동작한다(둘 다 slug/title/
    sources/verified_at/needs_verification을 갖는다). `sections`는 답변이 실제로 참조한
    섹션 이름 목록이며, ref는 `slug#첫 섹션`, detail은 섹션 이름을 이어붙인 문자열이다."""
    slug = getattr(doc_or_hit, "slug", "") or ""
    title = getattr(doc_or_hit, "title", "") or slug
    sources = getattr(doc_or_hit, "sources", None) or []
    verified_at = getattr(doc_or_hit, "verified_at", None)
    needs_verification = bool(getattr(doc_or_hit, "needs_verification", False))

    primary_section = sections[0] if sections else ""
    ref = f"{slug}#{primary_section}" if primary_section else slug
    first_url = (sources[0] or {}).get("url") if sources else None

    return [ChatResource(
        kind="kb",
        title=title,
        ref=ref,
        detail=", ".join(sections),
        url=first_url,
        verified_at=str(verified_at) if verified_at else None,
        needs_verification=needs_verification,
    )]


def format_disclosure_month(raw: str) -> str:
    """공시 기준월 원시 값("202608" 같은 YYYYMM)을 "2026년 8월"로 바꾼다. 형식이 다르면
    그대로 돌려준다. `app/api/routes.py`의 답변 파이프라인도 이 함수를 그대로 써서
    포맷 로직을 한 곳(answer.py)으로 통일한다(2026-09-06 보강)."""
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[:4]}년 {int(raw[4:6])}월"
    return raw


def products_resource(category: ProductCategory) -> ChatResource:
    """공시 상품 스냅샷 리소스 1건. `products.query(category)`의 건수와 공시 기준월을 쓴다."""
    items = products.query(category)
    disclosure_month = next((it.disclosure_month for it in items if it.disclosure_month), "")
    category_label = _CATEGORY_LABELS_KR.get(category.value, category.value)
    month_label = format_disclosure_month(disclosure_month) if disclosure_month else ""
    detail = f"{month_label} 공시, {category_label}" if month_label else f"{category_label} 공시"
    snapshot_ids = sorted({it.snapshot_id for it in items})
    return ChatResource(
        kind="products",
        title=f"공시 상품 {len(items)}건",
        ref=",".join(snapshot_ids),
        detail=detail,
    )


def policy_resources(params: PolicyParams, keys: list[str]) -> list[ChatResource]:
    """규제·내부 기준값 리소스. `config/policy_params.yaml`에서 읽은 값만 쓴다(원칙 6)."""
    out: list[ChatResource] = []
    for key in keys:
        p = params.params.get(key)
        if p is None:
            continue
        title = _POLICY_KEY_LABELS_KR.get(key, p.note or key)
        detail = f"{p.value}{p.unit}" if p.unit else str(p.value)
        out.append(ChatResource(
            kind="policy",
            title=title,
            ref=key,
            detail=detail,
            url=p.source_url or None,
            verified_at=str(p.verified_at) if p.verified_at else None,
            needs_verification=p.needs_verification,
        ))
    return out


_WS_RE_KEYWORD = re.compile(r"\s+")


def find_institutional_keyword_doc(text: str, *, min_keyword_len: int = 4) -> Optional[Any]:
    """발화에 KB 문서의 keyword가 그대로(공백 제거 후 부분 문자열로) 들어있으면 그 문서를
    돌려준다(SPEC 2.11 "여러 노드": compare/action/schedule/scenario/retirement/saving/
    liquidity 의도에 제도 키워드가 뚜렷할 때만 보조 kb 노드를 붙이기 위한 고정밀 신호).

    `app.kb.search.search()`의 바이그램 TF-IDF 점수는 여러 문서 본문에 공통으로 나오는
    짧은 단어("상환" 등)가 우연히 많이 겹치는 질문에서도 (faq 라우팅용 MIN_SCORE보다도)
    훨씬 높게 나올 수 있어(예: "상환표 보여줘"가 학자금 문서와 점수 40대로 겹친다) 이
    "부가 참조" 용도로 쓰기에는 너무 시끄럽다. 대신 키워드 자체가 발화에 말 그대로
    들어있는지만 본다(짧은 키워드는 오탐이 잦아 `min_keyword_len` 미만은 무시한다).
    여러 문서가 걸리면 더 긴 키워드가 매칭된 문서, 동률이면 slug 오름차순을 고른다.
    """
    query_norm = _WS_RE_KEYWORD.sub("", text or "")
    if not query_norm:
        return None
    best: Optional[tuple[int, str, Any]] = None  # (-len(keyword), slug, doc)
    for doc in kb_search.load_docs():
        for kw in doc.keywords:
            kw_norm = _WS_RE_KEYWORD.sub("", kw or "")
            if len(kw_norm) >= min_keyword_len and kw_norm in query_norm:
                candidate = (-len(kw_norm), doc.slug, doc)
                if best is None or candidate < best:
                    best = candidate
    return best[2] if best else None


def external_resources(sources: list[dict[str, Any]]) -> list[ChatResource]:
    """Google 검색 그라운딩 출처(`{"uri","title"}`) 목록을 리소스로 바꾼다."""
    out: list[ChatResource] = []
    seen: set[str] = set()
    for s in sources or []:
        uri = (s or {}).get("uri") or (s or {}).get("url") or ""
        if not uri or uri in seen:
            continue
        seen.add(uri)
        out.append(ChatResource(kind="external", title=(s or {}).get("title") or uri, ref=uri, url=uri))
    return out


# ---------------------------------------------------------------------------
# KB 답변 형식 (SPEC 2.11 3절)
# ---------------------------------------------------------------------------

def _kb_answer_system(detail: str) -> str:
    """제도 안내 요약(kb_answer_v1) 시스템 프롬프트. 요약 문장 수·핵심 개수 상한만
    detail(SPEC 2.16, "full"|"brief")별로 다르다."""
    rule = LENGTH_RULES[detail]["kb_summary"]
    point_limit = KB_SECTION_LIMIT[detail]
    return (
        "당신은 한국어 개인 부채 코치 앱 DONN의 제도 안내 요약 작성기입니다. 제공된 문단만 "
        f"근거로 요약({rule['min_sentences']}~{rule['max_sentences']}문장)과 핵심 최대 "
        f"{point_limit}개(각 1문장, 문단당 1개)를 JSON으로 돌려주세요. 문단에 없는 숫자나 "
        "사실을 지어내지 마세요. 특정 금융회사나 상품 이름, 가입을 권유하는 표현을 쓰지 "
        "마세요. 숫자를 한글 수사로 바꿔 쓰지 말고 아라비아 숫자 그대로 쓰세요. 문장마다 "
        "서로 다른 정보를 쓰고 같은 말을 반복하지 마세요. 해요체로 짧고 명확하게 쓰세요."
    )


KB_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "points": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary", "points"],
}

# SEV3 2026-09-06 리뷰: 숫자를 "바로 뒤 단위"까지 포함해 토큰화하고 쉼표는 정규화한다
# (참조 "2000만원"과 출력 "2,000만원"을 같은 토큰으로 본다). 단위가 없는 맨 숫자도
# 여전히 토큰이 된다(선택 그룹).
_NUMBER_TOKEN_RE = re.compile(r"\d[\d,.]*\s*(?:%|원|만원|개월|년|일|배|세)?")
_LEADING_BULLET_RE = re.compile(r"^[\s\-•]+")
_WS_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"[.!?]")

# 숫자를 한글 수사로 바꿔 써서 숫자 기반 그라운딩 검사를 피해가는 것을 막는다(예:
# "이천만원"은 _NUMBER_TOKEN_RE로 잡히지 않지만 실제로는 지어낸 숫자일 수 있다).
_HANGUL_NUMERAL_RE = re.compile(r"[일이삼사오육칠팔구십백천만억]{2,}\s*(?:원|퍼센트|개월|년)")


def _normalize_number_token(raw: str) -> str:
    """쉼표와 공백을 지운다(표기 차이만 다른 같은 숫자를 같은 토큰으로 만든다)."""
    return re.sub(r"[,\s]", "", raw)


def _number_tokens(text: str) -> set[str]:
    return {_normalize_number_token(m.group(0)) for m in _NUMBER_TOKEN_RE.finditer(text or "") if m.group(0).strip()}


def _has_hangul_numeral(text: str) -> bool:
    return bool(_HANGUL_NUMERAL_RE.search(text or ""))


def _first_sentence(text: str, max_len: int = 140) -> str:
    if not text:
        return ""
    cleaned = _LEADING_BULLET_RE.sub("", text.strip())
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return ""
    m = _SENTENCE_END_RE.search(cleaned)
    sentence = cleaned[: m.end()] if m else cleaned
    if len(sentence) > max_len:
        sentence = sentence[:max_len].rstrip() + "..."
    return sentence


def _split_sentences(cleaned: str) -> list[str]:
    """이미 공백이 정리된 문자열을 문장 종결부호 뒤에서 나눈다(구분자는 각 조각 끝에 남긴다)."""
    sentences: list[str] = []
    start = 0
    for m in _SENTENCE_END_RE.finditer(cleaned):
        piece = cleaned[start : m.end()].strip()
        if piece:
            sentences.append(piece)
        start = m.end()
    tail = cleaned[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def _first_clean_sentence(text: str, max_len: int = 140) -> str:
    """섹션 본문에서 권유 표현(`slotfill.FORBIDDEN_PHRASES`)이 없는 첫 문장을 고른다.

    (SEV3 2026-09-06 리뷰: kb/*.md 본문 중 "청약철회권: 보장성 상품(보험 등)..." 같은
    문장은 "보장"이 부분 문자열로 걸리지만 실제로는 권유 표현이 아니다. 이런 문장을
    통째로 버리는 대신 같은 섹션의 다음 문장으로 건너뛴다. 모든 문장이 걸리면 빈 문자열
    (호출부가 그 섹션은 건너뛴다).)
    """
    if not text:
        return ""
    cleaned = _LEADING_BULLET_RE.sub("", text.strip())
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return ""
    for sentence in _split_sentences(cleaned):
        if any(phrase in sentence for phrase in slotfill.FORBIDDEN_PHRASES):
            continue
        if len(sentence) > max_len:
            sentence = sentence[:max_len].rstrip() + "..."
        return sentence
    return ""


def kb_reference_sections(doc: Any, limit: int = 3) -> list[str]:
    """답변 근거로 쓸 섹션 이름(문서 순서, 내용이 있는 섹션만) 최대 `limit`개."""
    return [name for name, text in doc.sections.items() if (text or "").strip()][:limit]


def _kb_text_problems(text: str, banned: list[str], max_chars: int) -> list[str]:
    if not text or not text.strip():
        return ["empty"]
    problems: list[str] = []
    for term in banned:
        if term and term in text:
            problems.append(f"banned_term:{term}")
    for phrase in slotfill.FORBIDDEN_PHRASES:
        if phrase in text:
            problems.append(f"forbidden_phrase:{phrase}")
    if len(text) > max_chars:
        problems.append("too_long")
    return problems


def _render_kb_markdown(
    summary: str, points: list[str], needs_verification: bool, usage_line: Optional[str] = None,
) -> str:
    lines = [summary, "", "**핵심**"]
    lines.extend(f"- {p}" for p in points)
    if needs_verification:
        lines.append("")
        lines.append("일부 수치는 확인이 필요한 항목이에요.")
    if usage_line:
        lines.append("")
        lines.append(usage_line)
    return "\n".join(lines)


def _kb_usage_line(doc: Any) -> str:
    """SPEC 2.16: "이렇게 활용하세요" 한 줄(코드가 항상 붙인다, LLM/규칙 경로 공통).

    "절차" 섹션의 권유 표현 없는 첫 문장을 쓰고, 그 섹션이 없거나 빈 문장이면 고정
    안내로 대신한다(kb/*.md 15개 문서는 전부 "절차" 섹션을 갖고 있다).
    """
    procedure_sentence = _first_clean_sentence((doc.sections or {}).get("절차", ""))
    if not procedure_sentence:
        procedure_sentence = "자세히 보기에서 절차를 확인해 보세요."
    return f"이렇게 활용하세요: {procedure_sentence}"


def _rule_based_kb_answer(doc: Any, sections: list[str]) -> tuple[str, list[str]]:
    if not sections:
        return doc.title, []
    summary = _first_clean_sentence(doc.sections.get(sections[0], ""))
    if not summary:
        summary = doc.title
    points = []
    for name in sections:
        sentence = _first_clean_sentence(doc.sections.get(name, ""))
        if sentence:
            points.append(f"**{name}**: {sentence}")
    return summary, points


def format_kb_answer(
    doc: Any, hit: Any, provider: Any, banned: list[str], *, deadline_seconds: Optional[float] = None,
    detail: str = "full",
) -> tuple[str, bool, Optional[str], int, list[str]]:
    """KB 문서 1건으로 마크다운 답변을 만든다.

    반환: (markdown_text, llm_used, model, latency_ms, problems). LLM 경로가 하나라도
    검증에 실패하면 규칙 경로(섹션 제목 + 첫 문장)로 전부 되돌아간다(부분 혼합 없음).
    `detail`(SPEC 2.16, "full"|"brief")은 섹션·핵심 개수 상한(`KB_SECTION_LIMIT`)과 요약
    길이 규칙(`LENGTH_RULES`)에 반영된다. 마지막 줄("이렇게 활용하세요")은 경로와 무관하게
    코드가 항상 붙인다(검증 대상이 아니다).
    """
    section_limit = KB_SECTION_LIMIT[detail]
    summary_rule = LENGTH_RULES[detail]["kb_summary"]
    point_max_chars = KB_POINT_MAX_CHARS[detail]
    sections = kb_reference_sections(doc, limit=section_limit)
    rule_summary, rule_points = _rule_based_kb_answer(doc, sections)

    llm_used = False
    model: Optional[str] = None
    latency_ms = 0
    problems: list[str] = []
    summary, points = rule_summary, rule_points

    can_call = hasattr(provider, "explain") and provider.available() and bool(sections)
    if can_call:
        reference_text = "\n\n".join(f"[{name}]\n{doc.sections.get(name, '')}" for name in sections)
        reference_numbers = _number_tokens(reference_text)
        slots = {"title": doc.title, "sections": reference_text}
        kwargs: dict[str, Any] = {"schema": KB_ANSWER_SCHEMA}
        if deadline_seconds is not None:
            kwargs["deadline_seconds"] = deadline_seconds

        started = time.monotonic()
        try:
            result = provider.explain(slots, "kb_answer_v1", _kb_answer_system(detail), **kwargs)
            latency_ms = int((time.monotonic() - started) * 1000)
        except LLMUnavailable:
            latency_ms = int((time.monotonic() - started) * 1000)
            problems.append("llm_unavailable")
            result = None
        except Exception:  # noqa: BLE001 - SEV4 2026-09-06 리뷰: 예상 밖 예외도 규칙
            # 렌더링으로 떨어지게 하고, 원문은 로그로만 남긴다.
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.exception("KB 답변 LLM 호출 실패, 규칙 렌더링으로 대체")
            problems.append("llm_error")
            result = None

        if result is not None:
            data = result.data if isinstance(result.data, dict) else None
            if data is None and result.text:
                try:
                    parsed = json.loads(result.text)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    data = parsed
            if data is None:
                problems.append("no_json")
            else:
                raw_summary = slotfill.sanitize(str(data.get("summary") or ""))
                raw_points_field = data.get("points")
                raw_points = (
                    [slotfill.sanitize(str(p)) for p in raw_points_field]
                    if isinstance(raw_points_field, list) else []
                )
                candidate_problems = list(_kb_text_problems(raw_summary, banned, summary_rule["max_chars"]))
                if raw_summary and count_sentences(raw_summary) < summary_rule["min_sentences"]:
                    candidate_problems.append("too_short")
                if len(raw_points) != len(sections):
                    candidate_problems.append("points_count_mismatch")
                for p in raw_points:
                    candidate_problems.extend(
                        f"point:{c}" for c in _kb_text_problems(p, banned, point_max_chars)
                    )
                output_numbers = set(_number_tokens(raw_summary))
                for p in raw_points:
                    output_numbers |= _number_tokens(p)
                if not output_numbers.issubset(reference_numbers):
                    candidate_problems.append("ungrounded_number")
                if _has_hangul_numeral(raw_summary) or any(_has_hangul_numeral(p) for p in raw_points):
                    # SEV3 2026-09-06 리뷰: 숫자를 한글 수사로 바꿔 쓰면 _number_tokens가
                    # 숫자를 못 잡아 ungrounded_number 검사를 그대로 피해간다. 별도로 잡는다.
                    candidate_problems.append("hangul_numeral")

                if candidate_problems:
                    problems.extend(candidate_problems)
                else:
                    summary, points = raw_summary, raw_points
                    llm_used, model = True, result.model
    else:
        problems.append("llm_unavailable")

    markdown = _render_kb_markdown(summary, points, doc.needs_verification, _kb_usage_line(doc))
    return markdown, llm_used, model, latency_ms, problems


# ---------------------------------------------------------------------------
# 외부 검색 (SPEC 2.11 4절)
# ---------------------------------------------------------------------------

EXTERNAL_ANSWER_SYSTEM = (
    "당신은 한국어 금융 정보 도우미입니다. 한국 금융 제도와 금리 등 일반 정보를 3문장 "
    "이내로 답하세요. 특정 금융회사나 상품 이름을 말하지 말고 가입을 권유하지 마세요. "
    "개인 맞춤 조언을 하지 말고, 출처가 불확실하면 그렇다고 말하세요."
)

EXTERNAL_GROUNDED_SUFFIX = "(외부 검색 요약이라 최신 여부와 정확성은 링크에서 확인해 주세요.)"
EXTERNAL_BANNED_FALLBACK_TEXT = "요약 대신 아래 출처 링크를 확인해 주세요."
EXTERNAL_NO_GROUNDING_TEXT = "내부 자료에서 답을 찾지 못해 공식 안내 링크를 모았어요."

# 그라운딩이 불가능할 때 kb/*.md의 sources 중 이 도메인만 "공식 안내" 링크로 쓴다.
_OFFICIAL_DOMAIN_WHITELIST = (
    "fss.or.kr", "fsc.go.kr", "kinfa.or.kr", "ccrs.or.kr", "nps.or.kr", "hf.go.kr",
    "easylaw.go.kr", "law.go.kr", "nts.go.kr", "bok.or.kr", "kdic.or.kr", "kcredit.or.kr",
)


def _is_official_domain(url: str) -> bool:
    """`urlparse(url).hostname`이 허용 도메인 자체이거나 그 하위 도메인으로 끝나는지 본다.

    (SEV2 2026-09-06 리뷰: 기존 `domain in url` 부분 문자열 검사는
    "https://evil.example/fss.or.kr"이나 "https://fss.or.kr.evil.example" 같은 URL도
    통과시킬 수 있었다. hostname만 잘라 비교하면 이런 스푸핑을 막는다.)
    """
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not hostname:
        return False
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in _OFFICIAL_DOMAIN_WHITELIST)


def _official_kb_link_fallback(limit: int = 5) -> list[ChatResource]:
    seen: set[str] = set()
    out: list[ChatResource] = []
    for doc in kb_search.load_docs():
        for src in doc.sources:
            url = (src or {}).get("url") or ""
            if not url or url in seen or not _is_official_domain(url):
                continue
            seen.add(url)
            out.append(ChatResource(kind="external", title=(src or {}).get("title") or url, ref=url, url=url))
            if len(out) >= limit:
                return out
    return out


# SEV4 2026-09-06 리뷰: 검색 질의로 나가기 전에 숫자·금액 토큰을 지운다(D4/G3: 개인
# 신용정보 파생 수치가 외부로 나가지 않게 한다). 단위가 없는 맨 숫자도 지운다.
_QUERY_NUMBER_RE = re.compile(r"\d[\d,.]*\s*(?:원|만원|천만원|억|%|개월|년|세|배)?")

# "OO은행"류 패턴에서 앞 토큰이 공공·업권 일반어면 회사명이 아니다(SEV4 2026-09-06 리뷰).
# "한국"은 중앙은행 "한국은행"(공적 기관, 기준금리를 정하는 곳이라 외부 검색 요약에
# 자연스럽게 자주 등장한다)을 오탐하지 않기 위해 포함한다. 민간 은행("한국씨티은행" 등)은
# 정규식이 접두 토큰을 "씨티"까지 최소 확장해 잡아내므로 여기 포함해도 안전하다.
_GENERIC_SECTOR_PREFIX_WORDS = {
    "시중", "인터넷", "저축", "지방", "국책", "특수", "일반", "제1금융권", "제2금융권", "한국",
}
_COMPANY_NAME_PATTERN_RE = re.compile(r"([가-힣A-Za-z]{2,}?)(은행|저축은행|캐피탈|카드|생명|화재|증권|금고|보험)")


def _strip_numeric_tokens(text: str) -> str:
    """검색 질의에서 숫자·금액 토큰을 제거하고 공백을 정리한다."""
    stripped = _QUERY_NUMBER_RE.sub(" ", text or "")
    return _WS_RE.sub(" ", stripped).strip()


def _has_ungrounded_company_name(text: str) -> bool:
    """"OO은행"류 패턴이 있고 앞 토큰이 공공·업권 일반어가 아니면 True.

    (SEV4 2026-09-06 리뷰: banned 목록은 현재 적재된 상품의 회사명만 담고 있어, 외부
    검색 요약이 그 목록에 없는 다른 실존 금융회사명을 그대로 인용해도 걸러지지 않았다.)
    """
    for m in _COMPANY_NAME_PATTERN_RE.finditer(text or ""):
        prefix = m.group(1)
        if prefix in _SECTOR_WORDS or prefix in _GENERIC_SECTOR_PREFIX_WORDS:
            continue
        if any(tok in prefix for tok in _PUBLIC_TOKENS):
            continue
        return True
    return False


def external_answer(
    question_masked: str, provider: Any, banned: list[str], *, deadline_seconds: Optional[float] = None,
) -> tuple[str, list[ChatResource], bool, Optional[str], list[str], Optional[str]]:
    """Google 검색 그라운딩으로 답한다. 반환: (text, resources, llm_used, model, problems,
    search_entry_point_html). 그라운딩이 불가능하면 kb/*.md 공식 링크로 폴백한다."""
    search_fn = getattr(provider, "search_answer", None)
    enabled = getattr(provider, "external_search_enabled", True)
    available = bool(getattr(provider, "available", lambda: False)())

    if search_fn is None or not enabled or not available:
        reason = "search_unavailable" if search_fn is None or not available else "external_search_disabled"
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, [reason], None

    # SEV4 2026-09-06 리뷰: 개인 수치(G3)가 검색 질의에 그대로 나가지 않도록 숫자·금액
    # 토큰을 제거한 질의만 외부로 보낸다. 제거 후에도 숫자가 남거나(단위 없이 이어진
    # 숫자 등) 질의가 너무 짧아지면(문맥이 사실상 사라진 것) 그라운딩 자체를 건너뛰고
    # 공식 링크로 폴백한다.
    safe_query = _strip_numeric_tokens(question_masked)
    if any(ch.isdigit() for ch in safe_query):
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, ["query_has_digits"], None
    if len(safe_query) < 5:
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, ["query_too_short"], None

    try:
        result = search_fn(safe_query, EXTERNAL_ANSWER_SYSTEM, deadline_seconds=deadline_seconds)
    except LLMUnavailable:
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, ["llm_unavailable"], None
    except Exception:  # noqa: BLE001 - 네트워크/파싱 등 예상 밖 오류도 안전하게 폴백
        logger.exception("external 검색 LLM 호출 실패, 공식 링크로 대체")
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, ["llm_error"], None

    data = result.data if isinstance(result.data, dict) else {}
    sources = data.get("sources") or []
    search_html = data.get("search_entry_point_html") or None
    resources = external_resources(sources)
    problems: list[str] = []

    text = slotfill.sanitize(result.text or "")
    if guardrails.check_text(text, banned) or any(p in text for p in slotfill.FORBIDDEN_PHRASES):
        text = EXTERNAL_BANNED_FALLBACK_TEXT
        problems.append("banned_term_removed")
    elif _has_ungrounded_company_name(text):
        # SEV4 2026-09-06 리뷰: banned 목록에 없는 금융회사명 패턴이 잡히면 요약을 버리고
        # 출처 링크만 남긴다("추천"처럼 명백한 금칙어는 아니지만 M0 원칙(실명 비노출)
        # 위반이라 같은 방식으로 처리한다).
        text = EXTERNAL_BANNED_FALLBACK_TEXT
        problems.append("company_pattern")
    elif text:
        if any(ch.isdigit() for ch in text):
            # 그라운딩 요약에 숫자가 그대로 나오면 검증되지 않은 수치이므로 확인 필요
            # 문구를 붙인다(SEV4 2026-09-06 리뷰).
            text = f"{text} (수치는 확인 필요)"
        text = f"{text} {EXTERNAL_GROUNDED_SUFFIX}"
    else:
        text = EXTERNAL_BANNED_FALLBACK_TEXT
        problems.append("empty_summary")

    if not resources:
        resources = _official_kb_link_fallback()
        problems.append("no_grounding_sources")

    return text, resources, True, result.model, problems, search_html


# ---------------------------------------------------------------------------
# 직접 답변 (SPEC 2.11 1절)
# ---------------------------------------------------------------------------

def _direct_answer_system(detail: str) -> str:
    """직접 답변(direct_answer_v1) 시스템 프롬프트. 문장 수 상한만 detail(SPEC 2.16,
    "full"|"brief")별로 다르다."""
    rule = LENGTH_RULES[detail]["direct"]
    return (
        "당신은 한국어 개인 부채 코치 앱 DONN의 안내 도우미입니다. DONN이 할 수 있는 일(부채 "
        "상환표 계산, 시나리오 비교, 공시 상품 비교, 행동 제안, 제도 안내, 소비 패턴 분석, "
        "생애 흐름과 노후자금 계산)을 바탕으로 인사나 일반적인 질문에 답하세요. 문장마다 "
        "서로 다른 정보를 담고 같은 말을 반복하지 마세요. "
        f"문장 수는 {rule['min_sentences']}~{rule['max_sentences']}문장입니다. "
        "숫자, 상품명, 금융회사명, 가입을 권유하는 표현을 쓰지 말고 해요체로 답하세요."
    )


DIRECT_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"summary": {"type": "STRING"}},
    "required": ["summary"],
}

# 실패·불가 시 고정 안내 문장(기존 faq 폴백 안내문 재사용, SPEC 2.11 1절).
DIRECT_ANSWER_FALLBACK_TEXT = (
    "부채 상환표, 공시 비교, 시나리오, 행동 제안, 제도 안내 중 무엇이든 물어보세요. "
    "예: '신용대출 공시 비교해줘', '금리 4% 이하만', '금리인하요구권 요건이 뭐야'"
)


def build_direct_answer(
    provider: Any, banned: list[str], *, deadline_seconds: Optional[float] = None, detail: str = "full",
) -> tuple[str, bool, Optional[str], int, list[str]]:
    """(text, llm_used, model, latency_ms, problems). 프로필 수치·발화 원문을 LLM에 보내지
    않는다(자료가 필요 없는 질문이므로 빈 slots만 보낸다). `detail`(SPEC 2.16)은 길이
    규칙(`LENGTH_RULES`)과 시스템 프롬프트에 반영된다."""
    if not hasattr(provider, "explain") or not provider.available():
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, 0, ["llm_unavailable"]

    rule = LENGTH_RULES[detail]["direct"]
    kwargs: dict[str, Any] = {"schema": DIRECT_ANSWER_SCHEMA}
    if deadline_seconds is not None:
        kwargs["deadline_seconds"] = deadline_seconds

    started = time.monotonic()
    try:
        result = provider.explain({}, "direct_answer_v1", _direct_answer_system(detail), **kwargs)
    except LLMUnavailable:
        latency_ms = int((time.monotonic() - started) * 1000)
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, latency_ms, ["llm_unavailable"]
    except Exception:  # noqa: BLE001 - SEV4 2026-09-06 리뷰: 예상 밖 예외도 고정 안내
        # 문장으로 떨어지게 하고, 원문은 로그로만 남긴다.
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.exception("direct 답변 LLM 호출 실패, 고정 안내로 대체")
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, latency_ms, ["llm_error"]
    latency_ms = int((time.monotonic() - started) * 1000)

    data = result.data if isinstance(result.data, dict) else None
    if data is None and result.text:
        try:
            parsed = json.loads(result.text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            data = parsed
    summary = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(summary, str):
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, latency_ms, ["no_json"]

    sanitized = slotfill.sanitize(summary)
    # 허용 플레이스홀더 없음(빈 allowed set) + 숫자 있으면 실패 + 금칙어/권유표현/길이 검사.
    problems = slotfill.validate(
        sanitized, set(), banned, max_chars=rule["max_chars"], max_sentences=rule["max_sentences"],
    )
    if sanitized and count_sentences(sanitized) < rule["min_sentences"]:
        problems.append("too_short")
    if problems:
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, latency_ms, problems
    return sanitized, True, result.model, latency_ms, []


# ---------------------------------------------------------------------------
# 대화창 파일 첨부 답변 (SPEC 2.12) - LLM 미사용, 코드 템플릿
# ---------------------------------------------------------------------------


def format_spending_answer(
    summary: SpendingSummary, features: SpendingFeatures, profile: UserProfile, months: int,
    linked_actions: Optional[list[SpendingLinkedAction]] = None,
) -> str:
    """`POST /api/chat/attach` 응답 마크다운을 코드 템플릿으로 만든다(LLM 호출 없음).

    숫자는 전부 `app/core/spending.py`가 계산한 값이고 이 함수는 문장만 조립한다.
    가맹점 이름(마스킹된 것 포함)은 쓰지 않고 카테고리 라벨과 생애 이벤트 라벨만 쓴다
    (app/core/spending.py의 라벨 맵을 그대로 재사용).

    `linked_actions`(SPEC 2.15, 선택)가 있으면 `**이렇게 연결돼요**` 목록(최대 2개, 각 항목의
    `sentence`)을 덧붙인다.
    """
    avg = summary.avg_monthly_spend
    if profile.monthly_income > 0:
        pct = round(avg / profile.monthly_income * 100)
        headline = f"최근 {months}개월 월평균 지출은 {avg:,}원이고 월소득의 {pct}%예요."
    else:
        headline = f"최근 {months}개월 월평균 지출은 {avg:,}원이에요."

    points: list[str] = []

    top_categories = [c for c in summary.categories if c.amount > 0][:2]
    if top_categories:
        parts = ", ".join(f"{c.category.value} {round(c.share * 100)}%" for c in top_categories)
        points.append(f"지출이 가장 많은 카테고리는 {parts}예요.")

    if features.subscription_count > 0:
        points.append(
            f"정기 결제 {features.subscription_count}건의 월 합계는 {features.subscription_total:,}원이에요."
        )

    if summary.anomalies:
        top_anomaly = sorted(summary.anomalies, key=lambda a: -a.change_pct)[0]
        points.append(f"{top_anomaly.category.value} 지출이 전월보다 {round(top_anomaly.change_pct)}% 늘었어요.")

    if summary.life_events:
        top_event = sorted(summary.life_events, key=lambda e: -e.confidence)[0]
        label = _LIFE_EVENT_LABELS.get(top_event.kind, "최근 지출 패턴에 변화가 있었어요")
        points.append(f"{label}.")

    lines = [headline, "", "**핵심**"]
    lines.extend(f"- {p}" for p in points)
    if linked_actions:
        lines.append("")
        lines.append("**이렇게 연결돼요**")
        lines.extend(f"- {a.sentence}" for a in linked_actions[:2])
    lines.append("")
    lines.append("원본 거래내역은 저장하지 않고 요약만 남겨요.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 계산형 자유 질의(what-if) 답변 마크다운 (SPEC 2.13) - 코드 템플릿, 결론 문장만 교체 가능
# ---------------------------------------------------------------------------


def _whatif_calc_bullets(result: WhatIfResult) -> list[str]:
    """"계산 근거" 목록 3개(코드 템플릿, LLM 미사용). 도구별로 전후 수치를 나열한다."""
    if result.tool == "extra_payment":
        return [
            f"매월 추가 상환액: {result.inputs.get('extra_monthly', 0):,}원",
            (f"단축 개월: {result.deltas.get('months_saved', 0):,}개월 "
             f"({result.before.get('months', 0):,}개월 -> {result.after.get('months', 0):,}개월)"),
            (f"절감 이자: {result.deltas.get('interest_saved', 0):,}원 "
             f"(총이자 {result.before.get('total_interest', 0):,}원 -> {result.after.get('total_interest', 0):,}원)"),
        ]
    if result.tool == "lump_sum":
        fee = result.after.get("fee", 0) or 0
        amount_line = f"일시 상환액: {result.inputs.get('amount', 0):,}원"
        if fee > 0:
            amount_line += f"(중도상환수수료 {fee:,}원 별도)"
        return [
            amount_line,
            (f"단축 개월: {result.deltas.get('months_saved', 0):,}개월 "
             f"({result.before.get('months', 0):,}개월 -> {result.after.get('months', 0):,}개월)"),
            (f"절감 이자: {result.deltas.get('interest_saved', 0):,}원 "
             f"(총이자 {result.before.get('total_interest', 0):,}원 -> {result.after.get('total_interest', 0):,}원)"),
        ]
    if result.tool == "refinance":
        breakeven = result.deltas.get("breakeven_months")
        breakeven_text = f"{breakeven:,}개월" if breakeven is not None else "확인 필요"
        return [
            f"적용 금리: 연 {result.inputs.get('new_rate', 0):.4g}%로 변경",
            (f"월 납입액: {result.before.get('monthly', 0):,}원 -> {result.after.get('monthly', 0):,}원 "
             f"(차이 {result.deltas.get('monthly_delta', 0):,}원)"),
            (f"총이자: {result.before.get('total_interest', 0):,}원 -> {result.after.get('total_interest', 0):,}원 "
             f"(차이 {result.deltas.get('total_cost_delta', 0):,}원), 수수료 회수 {breakeven_text}"),
        ]
    # retirement_age
    return [
        f"은퇴 나이: {result.before.get('retirement_age', 0)}세 -> {result.after.get('retirement_age', 0)}세",
        (f"월 부족액: {result.before.get('monthly_gap', 0):,}원 -> {result.after.get('monthly_gap', 0):,}원"),
        (f"노후 부족액: {result.before.get('shortfall', 0):,}원 -> {result.after.get('shortfall', 0):,}원 "
         f"(추가 월 저축 {result.after.get('required_monthly_saving', 0):,}원 기준)"),
    ]


def format_whatif_answer(result: WhatIfResult, conclusion: str) -> str:
    """SPEC 2.13 답변 마크다운: 결론 1문장(LLM 슬롯 필링 또는 템플릿) + `**계산 근거**`
    목록 3개(코드 템플릿) + 가정 1줄. 계산 근거와 가정은 항상 코드가 만든다."""
    lines = [conclusion, "", "**계산 근거**"]
    lines.extend(f"- {b}" for b in _whatif_calc_bullets(result))
    if result.assumptions:
        lines.append("")
        lines.append(result.assumptions[0])
    return "\n".join(lines)
