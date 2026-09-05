"""kb/*.md 제도 안내 문서와 app/kb/search.py 검색 모듈 테스트.

15개 문서(P7 생애주기 층에서 추가한 연금·세제 문서 3편 포함)가 정해진 프런트매터·섹션
형식을 지키는지, 금지된 민간 금융회사명이나 em dash가 없는지, 결정적 키워드 검색이
기대한 문서를 상위로 올리는지, 관계없는 질문에는 답을 만들지 않는지를 확인한다.
임베딩이나 LLM 호출은 쓰지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.kb.search import DISCLAIMER, REQUIRED_SECTIONS, KbDoc, answer, load_docs, search
from app.llm.guardrails import check_text

KB_DIR = Path(__file__).resolve().parent.parent / "kb"

EXPECTED_SLUGS = {
    "rate-cut-request",
    "ccrs-debt-adjustment",
    "court-rehabilitation",
    "dsr-and-stress-dsr",
    "prepayment-fee",
    "refinance-infrastructure",
    "policy-microfinance",
    "deposit-insurance",
    "credit-score-basics",
    "illegal-lending-response",
    "student-loan-repayment",
    "consumer-rights",
    # P7 생애주기 층: 연금·세제 안내 3편
    "national-pension-estimate",
    "retirement-pension-db-dc-irp",
    "pension-savings-isa-tax",
}

ALLOWED_CATEGORIES = {
    "rights",
    "debt-adjustment",
    "court",
    "regulation",
    "fees",
    "refinance",
    "policy-products",
    "protection",
    "credit",
    "student",
    "consumer",
    "pension",
    "tax",
}

# 국내 주요 민간 은행·카드·저축은행·캐피탈·보험사 이름(20개 이상). kb 문서 어디에도
# 나오면 안 된다(공공기관과 햇살론 등 정책 상품명만 허용).
BANNED_PRIVATE_NAMES = [
    "국민은행",
    "신한은행",
    "우리은행",
    "하나은행",
    "농협은행",
    "기업은행",
    "SC제일은행",
    "카카오뱅크",
    "케이뱅크",
    "토스뱅크",
    "신한카드",
    "삼성카드",
    "현대카드",
    "롯데카드",
    "국민카드",
    "우리카드",
    "하나카드",
    "BC카드",
    "SBI저축은행",
    "OK저축은행",
    "웰컴저축은행",
    "페퍼저축은행",
    "현대캐피탈",
    "롯데캐피탈",
    "삼성생명",
    "한화생명",
    "교보생명",
    "삼성화재",
    "DB손해보험",
    "메리츠화재",
]

# 상호금융권 개별 기관명(SEV5 #3). "상호금융권"이라는 업권 일반 명칭은 허용하되, 특정
# 기관 실명은 금지한다(kb/deposit-insurance.md, kb/prepayment-fee.md가 과거 이 이름들을
# 직접 언급해 M0 실명 비노출 원칙을 어겼다). products 테이블에 이 기관들의 상품이 실려
# 있으면 채팅 응답 금칙어 검사(app.services.insights.get_banned_terms)에도 걸리므로,
# 아래 목록은 "KB 문서 자체"에 실명이 남아있는지를 직접 검사하는 보강 장치다.
BANNED_MUTUAL_FINANCE_NAMES = [
    "새마을금고",
    "신협중앙회",
    "신협",
    "농협중앙회",
    "농협",
    "수협중앙회",
    "수협",
]


def _kb_files() -> list[Path]:
    """15개 제도 안내 문서 파일만 반환한다(kb/README.md는 사람이 읽는 안내 파일이라 제외).

    README.md는 이 디렉터리의 형식과 규칙을 '설명'하는 문서라서, 예를 들어 em dash를
    쓰지 말라는 규칙을 적으면서 em dash 글자 자체를 인용해야 한다(CLAUDE.md가 같은
    규칙을 설명할 때도 마찬가지다). 그래서 콘텐츠 규칙 테스트는 실제 15개 문서만 본다.
    """
    return sorted(p for p in KB_DIR.glob("*.md") if p.name.lower() != "readme.md")


@pytest.fixture(scope="module")
def docs() -> list[KbDoc]:
    loaded = load_docs()
    assert loaded, "kb 문서를 하나도 읽지 못했습니다(kb_dir 경로 확인 필요)"
    return loaded


# ---------------------------------------------------------------------------
# 문서 형식
# ---------------------------------------------------------------------------


def test_exactly_fifteen_docs_with_expected_slugs(docs: list[KbDoc]) -> None:
    assert len(docs) == 15
    assert {d.slug for d in docs} == EXPECTED_SLUGS


def test_required_frontmatter_fields_present(docs: list[KbDoc]) -> None:
    for d in docs:
        assert d.slug, "slug 누락"
        assert d.title, f"{d.slug}: title 누락"
        assert d.category in ALLOWED_CATEGORIES, f"{d.slug}: category '{d.category}' 미허용"
        assert isinstance(d.keywords, list) and len(d.keywords) > 0, f"{d.slug}: keywords 누락"
        assert isinstance(d.sources, list) and len(d.sources) > 0, f"{d.slug}: sources 누락"
        for src in d.sources:
            assert isinstance(src, dict), f"{d.slug}: sources 항목은 매핑이어야 함"
            assert src.get("title"), f"{d.slug}: source title 누락"
            assert src.get("url", "").startswith(("http://", "https://")), (
                f"{d.slug}: source url이 http(s)로 시작해야 함: {src.get('url')!r}"
            )
            assert src.get("accessed"), f"{d.slug}: source accessed 누락"
        assert d.verified_at, f"{d.slug}: verified_at 누락"
        assert isinstance(d.needs_verification, bool), f"{d.slug}: needs_verification은 bool이어야 함"


def test_all_six_section_headings_present(docs: list[KbDoc]) -> None:
    for d in docs:
        assert set(d.sections.keys()) == set(REQUIRED_SECTIONS), (
            f"{d.slug}: 섹션이 기대와 다릅니다 -> {sorted(d.sections.keys())}"
        )


def test_line_count_between_60_and_120() -> None:
    files = _kb_files()
    assert len(files) == 15
    for path in files:
        n_lines = path.read_text(encoding="utf-8").count("\n") + 1
        assert 60 <= n_lines <= 120, f"{path.name}: {n_lines}줄 (60~120 범위를 벗어남)"


def test_faq_has_three_questions(docs: list[KbDoc]) -> None:
    for d in docs:
        faq = d.sections.get("자주 묻는 질문", "")
        q_count = faq.count("Q:")
        a_count = faq.count("A:")
        assert q_count == 3, f"{d.slug}: FAQ 질문이 3개가 아님({q_count}개)"
        assert a_count == 3, f"{d.slug}: FAQ 답변이 3개가 아님({a_count}개)"


# ---------------------------------------------------------------------------
# 콘텐츠 규칙: 금지어, em dash
# ---------------------------------------------------------------------------


def test_no_banned_private_financial_company_names() -> None:
    assert len(BANNED_PRIVATE_NAMES) >= 20, "금지어 목록은 최소 20개 이상이어야 함"
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        for name in BANNED_PRIVATE_NAMES:
            assert name not in text, f"{path.name}: 금지된 민간 금융회사명 '{name}' 발견"


def test_no_individual_mutual_finance_institution_names() -> None:
    """SEV5 #3: 상호금융권을 가리킬 때도 개별 기관 실명(새마을금고/신협/농협/수협 등)이
    아니라 "상호금융권" 같은 업권 일반 명칭만 써야 한다."""
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        for name in BANNED_MUTUAL_FINANCE_NAMES:
            assert name not in text, f"{path.name}: 금지된 상호금융권 기관 실명 '{name}' 발견"


def test_no_em_dash_in_any_doc() -> None:
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        assert "—" not in text, f"{path.name}: em dash(—) 발견"


def test_no_recommend_word_in_any_doc() -> None:
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        assert "추천" not in text, f"{path.name}: '추천' 단어 발견(안내/비교/확인으로 대체해야 함)"


def test_all_docs_chat_reply_passes_guardrail_check() -> None:
    """SEV5 #3: app/api/routes.py가 KB 응답에 대해서만 금칙어 검사를 건너뛰던 우회를
    없앴으므로, 15개 문서 전부 실제 채팅 응답 형식(제목+스니펫+확인필요 문구+면책)으로
    조립했을 때도 금칙어 검사(민간 금융회사명 + 상호금융권 개별 기관명)를 통과해야 한다."""
    banned = BANNED_PRIVATE_NAMES + BANNED_MUTUAL_FINANCE_NAMES
    docs = load_docs()
    assert len(docs) == 15
    for doc in docs:
        hit = answer(doc.title)
        assert hit is not None and hit["slug"] == doc.slug, f"{doc.slug}: 제목으로 질의했는데 자기 자신이 최상위가 아님"
        reply_text = f"{hit['title']} 안내입니다. {hit['snippet']}"
        if hit.get("needs_verification"):
            reply_text += " 일부 수치는 확인이 필요한 항목입니다."
        reply_text += f" {hit['disclaimer']}"
        hits_found = check_text(reply_text, banned)
        assert hits_found == [], f"{doc.slug}: 응답 문장이 금칙어 검사에 걸림 -> {hits_found}"


def test_no_loan_comparison_platform_steering_in_any_doc() -> None:
    """SEV3 #11: 대출비교 플랫폼(판매대리·중개업자)으로 안내하는 문구를 KB에서 없앤다.
    금융결제원, 금감원 파인, 금융회사 앱/창구 안내는 그대로 유지한다."""
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        assert "대출비교 플랫폼" not in text, f"{path.name}: '대출비교 플랫폼' 안내 문구 발견"


def test_needs_verification_true_when_doc_contains_confirm_needed_marker() -> None:
    """SEV3 #20: 본문에 "(확인 필요)"가 있는 문서는 프런트매터 needs_verification도
    true여야 한다(문구와 배지가 어긋나면 안 된다)."""
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        data, body = text.split("---", 2)[1:]  # 프런트매터 vs 본문 (첫 '---'는 빈 문자열)
        if "(확인 필요)" in body:
            fm = yaml.safe_load(data) or {}
            assert fm.get("needs_verification") is True, (
                f"{path.name}: 본문에 '(확인 필요)'가 있는데 needs_verification이 true가 아님"
            )


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------


def test_search_rate_cut_request_top_hit(docs: list[KbDoc]) -> None:
    hits = search("금리인하요구권 요건", docs=docs)
    assert hits and hits[0].slug == "rate-cut-request"


def test_search_debt_adjustment_top_hit(docs: list[KbDoc]) -> None:
    hits = search("채무조정 신청", docs=docs)
    assert hits and hits[0].slug == "ccrs-debt-adjustment"


def test_search_prepayment_fee_top_hit(docs: list[KbDoc]) -> None:
    hits = search("중도상환수수료", docs=docs)
    assert hits and hits[0].slug == "prepayment-fee"


def test_search_national_pension_estimate_top_hit(docs: list[KbDoc]) -> None:
    hits = search("국민연금 예상연금 조회", docs=docs)
    assert hits and hits[0].slug == "national-pension-estimate"


def test_search_retirement_pension_top_hit(docs: list[KbDoc]) -> None:
    hits = search("퇴직연금 DB DC IRP 차이", docs=docs)
    assert hits and hits[0].slug == "retirement-pension-db-dc-irp"


def test_search_pension_savings_isa_tax_top_hit(docs: list[KbDoc]) -> None:
    hits = search("연금저축 ISA 세액공제", docs=docs)
    assert hits and hits[0].slug == "pension-savings-isa-tax"


def test_search_handles_conversational_phrasing(docs: list[KbDoc]) -> None:
    """실제 채팅 문구(구어체, 조사 포함)에서도 같은 문서가 1위여야 한다."""
    assert search("금리인하요구권 요건이 뭐야", docs=docs)[0].slug == "rate-cut-request"
    assert search("채무조정 어떻게 신청해", docs=docs)[0].slug == "ccrs-debt-adjustment"
    assert search("중도상환수수료 언제까지 내야 해", docs=docs)[0].slug == "prepayment-fee"


def test_search_results_are_deterministic(docs: list[KbDoc]) -> None:
    query = "채무조정 신청"
    first = [(h.slug, h.score, h.snippet) for h in search(query, docs=docs)]
    second = [(h.slug, h.score, h.snippet) for h in search(query, docs=docs)]
    assert first == second


def test_search_ties_break_on_slug(docs: list[KbDoc]) -> None:
    """질의와 전혀 안 겹치는 문서들은 점수가 0으로 같으므로 slug 오름차순이어야 한다."""
    hits = search("zzz not korean at all", docs=docs, k=len(docs))
    zero_score_slugs = [h.slug for h in hits if h.score == 0.0]
    assert zero_score_slugs == sorted(zero_score_slugs)


def test_search_k_limits_results(docs: list[KbDoc]) -> None:
    assert search("채무조정", docs=docs, k=0) == []
    assert len(search("채무조정", docs=docs, k=1)) == 1
    assert len(search("채무조정", docs=docs, k=3)) == 3


def test_search_empty_query_returns_empty(docs: list[KbDoc]) -> None:
    assert search("", docs=docs) == []
    assert search("   ", docs=docs) == []


def test_search_single_character_query_returns_empty(docs: list[KbDoc]) -> None:
    """SEV3 #19: 2자 미만 질의는 어떤 문서와도 의미 있게 구분되지 않으므로 빈 결과."""
    assert search("금", docs=docs) == []
    assert search("가", docs=docs) == []


def test_search_unrelated_english_sentence_returns_empty(docs: list[KbDoc]) -> None:
    """SEV3 #19: ANSWER_THRESHOLD를 search() 안에서도 적용해, 전혀 무관한 질의에는
    "상위 k개"를 채우기 위해 무관한 문서를 끼워 넣지 않는다."""
    assert search("hello world how are you", docs=docs) == []


def test_snippet_cuts_at_last_sentence_boundary_before_400_chars() -> None:
    """SEV3 #20: 스니펫이 400자 언저리에서 단어/문장 중간이 아니라 마지막 문장 부호
    (.·?·!) 뒤에서 잘려야 한다."""
    long_section = ("이것은 테스트 문장입니다. " * 40).strip()
    assert len(long_section) > 400
    doc = KbDoc(
        slug="snippet-test", title="스니펫 테스트 문서", category="test",
        keywords=["스니펫테스트키워드"],
        sources=[{"title": "t", "url": "https://example.com", "accessed": "2026-09-06"}],
        verified_at="2026-09-06", needs_verification=False,
        body=f"## 개요\n\n{long_section}\n",
        sections={"개요": long_section},
    )
    hits = search("스니펫테스트키워드", docs=[doc], k=1)
    assert hits
    snippet = hits[0].snippet
    assert len(snippet) <= 400
    assert snippet.endswith("."), f"문장 경계가 아닌 곳에서 잘림: {snippet!r}"
    assert not snippet.endswith("...")


# ---------------------------------------------------------------------------
# answer()
# ---------------------------------------------------------------------------


def test_answer_none_for_unrelated_query() -> None:
    assert answer("전혀 관계없는 문장 xyz") is None


def test_answer_none_for_single_character_query() -> None:
    """SEV3 #19: "금"처럼 짧은 질의가 "예금"·"국민연금" 등 무관한 키워드에 부분
    문자열로 걸려 답을 만들어내던 문제(질의가 키워드의 부분 문자열인 반대 방향 매칭)."""
    assert answer("금") is None


def test_answer_shape_for_relevant_query() -> None:
    result = answer("중도상환수수료 언제까지 내야 해")
    assert result is not None
    assert result["slug"] == "prepayment-fee"
    assert result["disclaimer"] == DISCLAIMER
    assert result["disclaimer"] == "제도 설명은 참고용이며 최신 내용은 관련 기관 안내를 확인하세요."
    assert isinstance(result["sources"], list) and result["sources"]
    assert isinstance(result["needs_verification"], bool)
    assert isinstance(result["snippet"], str) and result["snippet"]
    assert set(result.keys()) == {
        "slug",
        "title",
        "snippet",
        "sources",
        "needs_verification",
        "disclaimer",
    }
