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
import re
import time
from typing import Any, Optional

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
from app.models import ChatResource, PolicyParams, ProductCategory, UserProfile

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


def products_resource(category: ProductCategory) -> ChatResource:
    """공시 상품 스냅샷 리소스 1건. `products.query(category)`의 건수와 공시 기준월을 쓴다."""
    items = products.query(category)
    disclosure_month = next((it.disclosure_month for it in items if it.disclosure_month), "")
    category_label = _CATEGORY_LABELS_KR.get(category.value, category.value)
    detail = f"{disclosure_month} 공시, {category_label}" if disclosure_month else f"{category_label} 공시"
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

KB_ANSWER_SYSTEM = (
    "당신은 한국어 개인 부채 코치 앱 DONN의 제도 안내 요약 작성기입니다. 제공된 문단만 "
    "근거로 요약 1문장과 핵심 3개(각 1문장, 문단당 1개)를 JSON으로 돌려주세요. 문단에 없는 "
    "숫자나 사실을 지어내지 마세요. 특정 금융회사나 상품 이름, 가입을 권유하는 표현을 쓰지 "
    "마세요. 해요체로 짧고 명확하게 쓰세요."
)

KB_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "points": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["summary", "points"],
}

_NUMBER_TOKEN_RE = re.compile(r"\d[\d,.]*")
_LEADING_BULLET_RE = re.compile(r"^[\s\-•]+")
_WS_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"[.!?]")


def _number_tokens(text: str) -> set[str]:
    return set(_NUMBER_TOKEN_RE.findall(text or ""))


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


def _render_kb_markdown(summary: str, points: list[str], needs_verification: bool) -> str:
    lines = [summary, "", "**핵심**"]
    lines.extend(f"- {p}" for p in points)
    if needs_verification:
        lines.append("")
        lines.append("일부 수치는 확인이 필요한 항목이에요.")
    return "\n".join(lines)


def _rule_based_kb_answer(doc: Any, sections: list[str]) -> tuple[str, list[str]]:
    if not sections:
        return doc.title, []
    summary = _first_sentence(doc.sections.get(sections[0], ""))
    points = []
    for name in sections:
        sentence = _first_sentence(doc.sections.get(name, ""))
        if sentence:
            points.append(f"**{name}**: {sentence}")
    return summary, points


def format_kb_answer(
    doc: Any, hit: Any, provider: Any, banned: list[str], *, deadline_seconds: Optional[float] = None,
) -> tuple[str, bool, Optional[str], int, list[str]]:
    """KB 문서 1건으로 마크다운 답변을 만든다.

    반환: (markdown_text, llm_used, model, latency_ms, problems). LLM 경로가 하나라도
    검증에 실패하면 규칙 경로(섹션 제목 + 첫 문장)로 전부 되돌아간다(부분 혼합 없음).
    """
    sections = kb_reference_sections(doc)
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
            result = provider.explain(slots, "kb_answer_v1", KB_ANSWER_SYSTEM, **kwargs)
            latency_ms = int((time.monotonic() - started) * 1000)
        except LLMUnavailable:
            latency_ms = int((time.monotonic() - started) * 1000)
            problems.append("llm_unavailable")
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
                candidate_problems = list(_kb_text_problems(raw_summary, banned, 120))
                if len(raw_points) != len(sections):
                    candidate_problems.append("points_count_mismatch")
                for p in raw_points:
                    candidate_problems.extend(f"point:{c}" for c in _kb_text_problems(p, banned, 100))
                output_numbers = set(_number_tokens(raw_summary))
                for p in raw_points:
                    output_numbers |= _number_tokens(p)
                if not output_numbers.issubset(reference_numbers):
                    candidate_problems.append("ungrounded_number")

                if candidate_problems:
                    problems.extend(candidate_problems)
                else:
                    summary, points = raw_summary, raw_points
                    llm_used, model = True, result.model
    else:
        problems.append("llm_unavailable")

    markdown = _render_kb_markdown(summary, points, doc.needs_verification)
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


def _official_kb_link_fallback(limit: int = 5) -> list[ChatResource]:
    seen: set[str] = set()
    out: list[ChatResource] = []
    for doc in kb_search.load_docs():
        for src in doc.sources:
            url = (src or {}).get("url") or ""
            if not url or url in seen or not any(domain in url for domain in _OFFICIAL_DOMAIN_WHITELIST):
                continue
            seen.add(url)
            out.append(ChatResource(kind="external", title=(src or {}).get("title") or url, ref=url, url=url))
            if len(out) >= limit:
                return out
    return out


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

    try:
        result = search_fn(question_masked, EXTERNAL_ANSWER_SYSTEM, deadline_seconds=deadline_seconds)
    except LLMUnavailable:
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, ["llm_unavailable"], None
    except Exception as exc:  # noqa: BLE001 - 네트워크/파싱 등 예상 밖 오류도 안전하게 폴백
        return EXTERNAL_NO_GROUNDING_TEXT, _official_kb_link_fallback(), False, None, [f"error:{type(exc).__name__}"], None

    data = result.data if isinstance(result.data, dict) else {}
    sources = data.get("sources") or []
    search_html = data.get("search_entry_point_html") or None
    resources = external_resources(sources)
    problems: list[str] = []

    text = slotfill.sanitize(result.text or "")
    if guardrails.check_text(text, banned) or any(p in text for p in slotfill.FORBIDDEN_PHRASES):
        text = EXTERNAL_BANNED_FALLBACK_TEXT
        problems.append("banned_term_removed")
    elif text:
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

DIRECT_ANSWER_SYSTEM = (
    "당신은 한국어 개인 부채 코치 앱 DONN의 안내 도우미입니다. DONN이 할 수 있는 일(부채 "
    "상환표 계산, 시나리오 비교, 공시 상품 비교, 행동 제안, 제도 안내, 소비 패턴 분석, "
    "생애 흐름과 노후자금 계산)을 바탕으로 인사나 일반적인 질문에 2문장 이내로 답하세요. "
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
    provider: Any, banned: list[str], *, deadline_seconds: Optional[float] = None,
) -> tuple[str, bool, Optional[str], int, list[str]]:
    """(text, llm_used, model, latency_ms, problems). 프로필 수치·발화 원문을 LLM에 보내지
    않는다(자료가 필요 없는 질문이므로 빈 slots만 보낸다)."""
    if not hasattr(provider, "explain") or not provider.available():
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, 0, ["llm_unavailable"]

    kwargs: dict[str, Any] = {"schema": DIRECT_ANSWER_SCHEMA}
    if deadline_seconds is not None:
        kwargs["deadline_seconds"] = deadline_seconds

    started = time.monotonic()
    try:
        result = provider.explain({}, "direct_answer_v1", DIRECT_ANSWER_SYSTEM, **kwargs)
    except LLMUnavailable:
        latency_ms = int((time.monotonic() - started) * 1000)
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, latency_ms, ["llm_unavailable"]
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
    problems = slotfill.validate(sanitized, set(), banned, max_chars=200, max_sentences=2)
    if problems:
        return DIRECT_ANSWER_FALLBACK_TEXT, False, None, latency_ms, problems
    return sanitized, True, result.model, latency_ms, []
