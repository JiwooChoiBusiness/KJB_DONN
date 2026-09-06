"""질문에 맞는 문단을 문서 안에서 먼저 찾는다(SPEC 2.11 보강). 임베딩 없이 어절·n-gram 겹침으로 고른다.

"개인회생의 변제 기간은?"처럼 문서는 맞게 찾았지만 답이 문서 중간(절차 섹션의 글머리표)에 있을 때,
문서 앞부분 요약 대신 그 문장을 먼저 답하게 하기 위한 모듈이다. 순수 문자열 처리만 한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

# 질문 표현을 문서 표현으로 넓히는 동의어 묶음. 한 묶음의 단어가 질문에 있으면 묶음 전체를 검색어에 더한다.
SYNONYMS: list[list[str]] = [
    ["변제기간", "변제 기간", "갚는 기간", "상환 기간", "몇 년", "얼마나 오래", "변제 수행", "년까지", "년간", "얼마나 걸"],
    ["요건", "자격", "조건", "대상", "신청할 수 있", "누가"],
    ["절차", "방법", "어떻게", "순서", "진행"],
    ["비용", "수수료", "돈이 드", "얼마나 드"],
    ["기간", "얼마나 걸", "며칠", "몇 달", "개월"],
    ["면제", "탕감", "감면", "면책"],
    ["신청", "접수", "신청서"],
    ["불이익", "단점", "유의", "주의", "제한"],
    ["효과", "장점", "혜택", "이점"],
]

_STOPWORDS = {
    "은", "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "로", "과", "와", "도", "만", "뭐야", "뭐", "무엇",
    "무엇인가요", "알려줘", "알려", "해줘", "해", "줘", "인가요", "인가", "얼마", "좀", "요", "이야", "있어", "있나",
    "있나요", "돼", "되나", "될까", "할까", "해야", "해요", "궁금", "궁금해", "설명", "대해", "관해", "그럼", "그러면",
}
_NON_WORD_RE = re.compile(r"[^\w가-힣]+")
_JOSA_TAIL_RE = re.compile(r"(은|는|이|가|을|를|의|에서|에|으로|로|과|와|도|만|은요|는요)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LEADING_BULLET_RE = re.compile(r"^[-*•·]\s*")


@dataclass
class Chunk:
    section: str
    text: str
    order: int


def _normalize(text: str) -> str:
    return _NON_WORD_RE.sub("", text or "")


def _tokens(text: str) -> set[str]:
    """어절(조사 제거)과 2~3자 n-gram 집합. 한 글자 토큰은 버린다."""
    grams: set[str] = set()
    for word in _NON_WORD_RE.sub(" ", text or "").split():
        if word in _STOPWORDS:
            continue
        stem = _JOSA_TAIL_RE.sub("", word) if len(word) > 2 else word
        if len(stem) >= 2:
            grams.add(stem)
        for n in (2, 3):
            for i in range(len(stem) - n + 1):
                grams.add(stem[i:i + n])
    return grams


def expand_question(question: str) -> str:
    """질문에 동의어 묶음의 단어가 있으면 묶음 전체를 덧붙인 문자열을 돌려준다."""
    compact = _normalize(question)
    extra: list[str] = []
    for group in SYNONYMS:
        if any(_normalize(word) and _normalize(word) in compact for word in group):
            extra.extend(group)
    return (question or "") + " " + " ".join(extra)


def split_chunks(doc: Any) -> list[Chunk]:
    """섹션 본문을 글머리표 항목·문장 단위 청크로 나눈다. FAQ의 Q/A 줄은 하나로 묶는다."""
    chunks: list[Chunk] = []
    order = 0
    for section, body in (doc.sections or {}).items():
        items: list[str] = []
        pending_q: Optional[str] = None
        for raw in (body or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            is_bullet = bool(_LEADING_BULLET_RE.match(line))
            text = _LEADING_BULLET_RE.sub("", line).strip()
            if re.match(r"^(Q|질문)\s*[:：]", text):
                pending_q = text
                continue
            if re.match(r"^(A|답변?)\s*[:：]", text) and pending_q:
                items.append(f"{pending_q} {text}")
                pending_q = None
                continue
            if is_bullet:
                items.append(text)
            else:
                items.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip())
        if pending_q:
            items.append(pending_q)
        for text in items:
            chunks.append(Chunk(section=section, text=text, order=order))
            order += 1
    return chunks


def rank_chunks(doc: Any, question: str, k: int = 5) -> list[tuple[float, Chunk]]:
    """질문과 겹치는 정도로 청크를 매긴다. 문서 키워드(예: 개인회생)는 모든 청크에 있어 가중치를 낮춘다."""
    if not question or not question.strip():
        return []
    q_tokens = _tokens(expand_question(question))
    keyword_tokens: set[str] = set()
    for kw in list(getattr(doc, "keywords", []) or []) + [getattr(doc, "title", "") or ""]:
        keyword_tokens |= _tokens(kw)
    title_hits = {name: _tokens(name) & q_tokens for name in (doc.sections or {})}
    scored: list[tuple[float, Chunk]] = []
    for chunk in split_chunks(doc):
        overlap = _tokens(chunk.text) & q_tokens
        strong = overlap - keyword_tokens
        score = len(strong) + 0.2 * len(overlap & keyword_tokens)
        if title_hits.get(chunk.section) - keyword_tokens:
            score += 1.0
        if strong:
            scored.append((score, chunk))
    scored.sort(key=lambda sc: (-sc[0], sc[1].order))
    return scored[:k]


def best_answer_sentence(doc: Any, question: str, *, min_strong: int = 2) -> Optional[tuple[str, str]]:
    """질문에 직접 답하는 것으로 보이는 청크가 있으면 (섹션 제목, 문장)을 돌려준다."""
    ranked = rank_chunks(doc, question, k=1)
    if not ranked:
        return None
    score, chunk = ranked[0]
    keyword_tokens: set[str] = set()
    for kw in list(getattr(doc, "keywords", []) or []) + [getattr(doc, "title", "") or ""]:
        keyword_tokens |= _tokens(kw)
    q_tokens = _tokens(expand_question(question))
    strong = (_tokens(chunk.text) & q_tokens) - keyword_tokens
    title_hit = bool((_tokens(chunk.section) & q_tokens) - keyword_tokens)
    if len(strong) < min_strong and not (strong and title_hit):
        return None
    return chunk.section, chunk.text
