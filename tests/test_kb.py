"""kb/*.md 제도 안내 문서와 app/kb/search.py 검색 모듈 테스트.

12개 문서가 정해진 프런트매터·섹션 형식을 지키는지, 금지된 민간 금융회사명이나
em dash가 없는지, 결정적 키워드 검색이 기대한 문서를 상위로 올리는지, 관계없는
질문에는 답을 만들지 않는지를 확인한다. 임베딩이나 LLM 호출은 쓰지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.kb.search import DISCLAIMER, REQUIRED_SECTIONS, KbDoc, answer, load_docs, search

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


def _kb_files() -> list[Path]:
    """12개 제도 안내 문서 파일만 반환한다(kb/README.md는 사람이 읽는 안내 파일이라 제외).

    README.md는 이 디렉터리의 형식과 규칙을 '설명'하는 문서라서, 예를 들어 em dash를
    쓰지 말라는 규칙을 적으면서 em dash 글자 자체를 인용해야 한다(CLAUDE.md가 같은
    규칙을 설명할 때도 마찬가지다). 그래서 콘텐츠 규칙 테스트는 실제 12개 문서만 본다.
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


def test_exactly_twelve_docs_with_expected_slugs(docs: list[KbDoc]) -> None:
    assert len(docs) == 12
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
    assert len(files) == 12
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


def test_no_em_dash_in_any_doc() -> None:
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        assert "—" not in text, f"{path.name}: em dash(—) 발견"


def test_no_recommend_word_in_any_doc() -> None:
    for path in _kb_files():
        text = path.read_text(encoding="utf-8")
        assert "추천" not in text, f"{path.name}: '추천' 단어 발견(안내/비교/확인으로 대체해야 함)"


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


# ---------------------------------------------------------------------------
# answer()
# ---------------------------------------------------------------------------


def test_answer_none_for_unrelated_query() -> None:
    assert answer("전혀 관계없는 문장 xyz") is None


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
