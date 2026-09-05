"""DONN 제도 안내 KB 키워드 검색 모듈.

`kb/*.md` 문서의 YAML 프런트매터와 본문을 읽어 deterministic 키워드 검색을 제공한다.
임베딩이나 LLM 호출은 쓰지 않는다. 한글 음절 바이그램 기반 TF-IDF 점수에 키워드 일치와
제목 일치 가중치를 더해 점수를 매기고, 동점이면 slug 오름차순으로 정렬한다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_KB_DIR = "kb"

# 답변으로 채택할 최소 점수. 키워드 정확 일치(8점) 하나만 있어도 넘고,
# 본문의 우연한 바이그램 한두 개 겹침으로는 넘지 못하도록 잡은 값이다.
ANSWER_THRESHOLD = 3.0

DISCLAIMER = "제도 설명은 참고용이며 최신 내용은 관련 기관 안내를 확인하세요."

REQUIRED_SECTIONS = (
    "개요",
    "대상과 요건",
    "절차",
    "비용과 유의점",
    "관련 기관과 창구",
    "자주 묻는 질문",
)


@dataclass
class KbDoc:
    """kb/*.md 문서 하나를 파싱한 결과."""

    slug: str
    title: str
    category: str
    keywords: list[str]
    sources: list[dict[str, Any]]
    verified_at: str
    needs_verification: bool
    body: str
    sections: dict[str, str] = field(default_factory=dict)


@dataclass
class KbHit:
    """검색 결과 한 건."""

    slug: str
    title: str
    score: float
    snippet: str
    needs_verification: bool
    sources: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# 문서 로딩
# ---------------------------------------------------------------------------

_docs_cache: Optional[list[KbDoc]] = None
_docs_cache_dir: Optional[str] = None


def _resolve_kb_dir(kb_dir: str) -> Path:
    """kb_dir을 실제 디렉터리 경로로 해석한다.

    상대경로가 현재 작업 디렉터리 기준으로 없으면, 이 파일(app/kb/search.py) 기준
    프로젝트 루트(../../<kb_dir>)도 시도한다. 어느 쪽도 없으면 CWD 기준 경로를 그대로
    반환해(존재하지 않는 디렉터리) load_docs가 빈 리스트를 돌려주게 한다.
    """
    p = Path(kb_dir)
    if p.is_absolute():
        return p
    candidates = [Path.cwd() / p, Path(__file__).resolve().parent.parent.parent / p]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """`---\n<yaml>\n---\n<본문>` 형식을 (frontmatter dict, 본문) 으로 분리한다."""
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("문서가 '---' 프런트매터로 시작하지 않습니다")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("프런트매터를 닫는 '---'를 찾지 못했습니다")
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    data = yaml.safe_load(fm_text) or {}
    if not isinstance(data, dict):
        raise ValueError("프런트매터가 매핑(dict) 형식이 아닙니다")
    return data, body.lstrip("\n")


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


def _parse_sections(body: str) -> dict[str, str]:
    """`## 제목` 단위로 본문을 섹션 dict(제목 -> 내용)로 나눈다."""
    sections: dict[str, str] = {}
    current: Optional[str] = None
    buf: list[str] = []
    for line in body.split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _load_doc_file(path: Path) -> KbDoc:
    text = path.read_text(encoding="utf-8")
    data, body = _parse_frontmatter(text)
    sections = _parse_sections(body)

    slug = str(data.get("slug") or path.stem)
    title = str(data.get("title") or slug)
    category = str(data.get("category") or "")
    keywords = [str(k) for k in (data.get("keywords") or [])]
    sources = list(data.get("sources") or [])
    verified_at = data.get("verified_at")
    verified_at = "" if verified_at is None else str(verified_at)
    needs_verification = bool(data.get("needs_verification", False))

    return KbDoc(
        slug=slug,
        title=title,
        category=category,
        keywords=keywords,
        sources=sources,
        verified_at=verified_at,
        needs_verification=needs_verification,
        body=body,
        sections=sections,
    )


def load_docs(kb_dir: str = DEFAULT_KB_DIR) -> list[KbDoc]:
    """kb_dir 안의 모든 `*.md` 문서를 읽어 KbDoc 리스트로 반환한다.

    모듈 레벨에 캐시하며, 같은 kb_dir로 다시 호출하면 캐시를 그대로 돌려준다.
    강제로 다시 읽으려면 `reload()`를 쓴다. 밑줄로 시작하는 파일(임시/작업용)은 건너뛴다.
    """
    global _docs_cache, _docs_cache_dir
    if _docs_cache is not None and _docs_cache_dir == kb_dir:
        return _docs_cache

    directory = _resolve_kb_dir(kb_dir)
    docs: list[KbDoc] = []
    if directory.exists():
        for path in sorted(directory.glob("*.md")):
            # README.md는 사람이 읽는 안내 파일이라 프런트매터가 없다. 밑줄로 시작하는
            # 파일(임시/작업용)과 함께 문서 목록에서 제외한다.
            if path.name.startswith("_") or path.name.lower() == "readme.md":
                continue
            docs.append(_load_doc_file(path))
    docs.sort(key=lambda d: d.slug)

    _docs_cache = docs
    _docs_cache_dir = kb_dir
    return docs


def reload(kb_dir: str = DEFAULT_KB_DIR) -> list[KbDoc]:
    """캐시를 비우고 kb_dir에서 문서를 다시 읽는다."""
    global _docs_cache, _docs_cache_dir
    _docs_cache = None
    _docs_cache_dir = None
    return load_docs(kb_dir)


# ---------------------------------------------------------------------------
# 검색: 한글 문자 바이그램 TF-IDF + 키워드/제목 가중치
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _bigrams(text: str) -> list[str]:
    """공백으로 나눈 토큰 내부에서만 문자(음절) 바이그램을 만든다.

    토큰 경계를 넘어 바이그램을 만들지 않아 "...권 요건"처럼 서로 다른 단어의
    끝/시작 글자가 우연히 섞이는 것을 막는다. 한 글자짜리 토큰은 그대로 하나의
    자질(unigram)로 쓴다.
    """
    if not text:
        return []
    grams: list[str] = []
    for tok in _WHITESPACE_RE.split(text.strip()):
        if not tok:
            continue
        if len(tok) == 1:
            grams.append(tok)
            continue
        for i in range(len(tok) - 1):
            grams.append(tok[i : i + 2])
    return grams


def _normalize(text: str) -> str:
    """공백을 제거해 부분 문자열 포함 여부 비교에 쓴다."""
    return _WHITESPACE_RE.sub("", text or "")


def _build_index(docs: list[KbDoc]) -> tuple[dict[str, Counter], dict[str, float]]:
    """문서별 바이그램 빈도(TF)와 전체 문서집합 기준 역문서빈도(IDF)를 계산한다."""
    doc_grams: dict[str, Counter] = {}
    df: Counter = Counter()
    for d in docs:
        counter = Counter(_bigrams(d.body))
        doc_grams[d.slug] = counter
        for g in counter.keys():  # dict 순서는 삽입 순서라 결정적이다
            df[g] += 1

    n = max(len(docs), 1)
    idf = {g: math.log((n + 1) / (dfg + 1)) + 1.0 for g, dfg in df.items()}
    return doc_grams, idf


def _keyword_score(query_norm: str, query_grams: set[str], keywords: list[str]) -> float:
    """키워드가 질의에 (완전히) 포함되면 높은 고정 가중치, 부분 겹침은 비례 가중치를 준다.

    방향은 "키워드가 질의 안에 있는가"(kw_norm in query_norm)만 본다. 반대 방향
    (query_norm in kw_norm, 질의가 키워드의 부분 문자열인가)까지 인정하면 "금"처럼
    아주 짧은 질의가 "예금", "국민연금" 같은 무관한 키워드에도 전부 걸려 부당하게 높은
    점수를 받는다(2026-09-06 리뷰: `answer("금")`이 답을 만들어내던 원인).
    """
    score = 0.0
    for kw in keywords:
        kw_norm = _normalize(kw)
        if not kw_norm:
            continue
        if kw_norm in query_norm:
            score += 8.0
            continue
        kw_grams = set(_bigrams(kw))
        if kw_grams:
            overlap = len(kw_grams & query_grams)
            if overlap:
                score += 4.0 * (overlap / len(kw_grams))
    return score


def _title_score(query_norm: str, query_grams: set[str], title: str) -> float:
    """제목 일치는 가장 높은 가중치를 준다."""
    title_norm = _normalize(title)
    if not title_norm:
        return 0.0
    if title_norm in query_norm or query_norm in title_norm:
        return 15.0
    title_grams = set(_bigrams(title))
    if not title_grams:
        return 0.0
    overlap = len(title_grams & query_grams)
    return 10.0 * (overlap / len(title_grams))


def _best_snippet(query_grams: set[str], doc: KbDoc, max_len: int = 400) -> str:
    """질의 바이그램과 가장 많이 겹치는 섹션을 골라 최대 max_len자로 자른다."""
    best_heading: Optional[str] = None
    best_overlap = -1
    for heading, text in doc.sections.items():
        overlap = len(set(_bigrams(text)) & query_grams)
        if overlap > best_overlap:
            best_overlap = overlap
            best_heading = heading

    if best_heading is not None and best_overlap > 0:
        text = doc.sections[best_heading]
    else:
        text = doc.sections.get("개요", doc.body)

    text = text.strip()
    if len(text) > max_len:
        # max_len 안에서 마지막 문장 경계(., ?, !) 뒤에서 자른다. 단어나 문장 중간에서
        # 뚝 끊기면 어색하므로, 경계가 있으면 그 문장까지만 남기고 "..."를 붙이지
        # 않는다(2026-09-06 리뷰). 경계를 못 찾으면(문장부호가 전혀 없는 예외적인 텍스트)
        # 기존처럼 글자 수로 자르고 "..."를 붙인다.
        cut = text[:max_len]
        boundary = max(cut.rfind("."), cut.rfind("?"), cut.rfind("!"))
        if boundary >= 0:
            text = cut[: boundary + 1].strip()
        else:
            text = cut.rstrip() + "..."
    return text


def search(query: str, k: int = 3, docs: Optional[list[KbDoc]] = None) -> list[KbHit]:
    """질의에 대해 상위 k개 문서를 deterministic하게 점수 매겨 반환한다.

    점수 = (한글 문자 바이그램 TF-IDF 본문 점수) + (키워드 일치 가중치) + (제목 일치 가중치).
    동점이면 slug 오름차순으로 정렬한다. docs를 넘기면 그 목록만 대상으로 검색하고,
    생략하면 `load_docs()`로 얻은 캐시된 전체 문서를 대상으로 한다.

    질의가 2자 미만이면(예: "금") 어떤 문서와도 의미 있게 구분되지 않으므로 빈 결과를
    돌려준다. `ANSWER_THRESHOLD`를 여기서도 적용해, 점수가 낮아 사실상 무관한 문서까지
    "상위 k개"라는 이유만으로 끼워 넣지 않는다(2026-09-06 리뷰: 관계없는 영어 질의에도
    항상 문서 k개가 반환되던 문제).
    """
    if docs is None:
        docs = load_docs()
    if not docs or not query or len(query.strip()) < 2:
        return []

    doc_grams, idf = _build_index(docs)

    query_gram_list = _bigrams(query)
    if not query_gram_list:
        return []
    query_grams = set(query_gram_list)
    query_counter = Counter(query_gram_list)
    query_norm = _normalize(query)
    q_len_sqrt = math.sqrt(len(query_gram_list))

    scored: list[tuple[float, str, KbDoc]] = []
    for doc in docs:
        counter = doc_grams.get(doc.slug, Counter())
        body_score = 0.0
        for g, qc in query_counter.items():
            tf = counter.get(g, 0)
            if tf:
                body_score += qc * tf * idf.get(g, 1.0)
        body_score /= q_len_sqrt

        total = (
            body_score
            + _keyword_score(query_norm, query_grams, doc.keywords)
            + _title_score(query_norm, query_grams, doc.title)
        )
        scored.append((total, doc.slug, doc))

    scored.sort(key=lambda t: (-t[0], t[1]))
    qualified = [row for row in scored if row[0] > ANSWER_THRESHOLD]

    k = max(k, 0)
    hits: list[KbHit] = []
    for score, _slug, doc in qualified[:k]:
        hits.append(
            KbHit(
                slug=doc.slug,
                title=doc.title,
                score=round(score, 4),
                snippet=_best_snippet(query_grams, doc),
                needs_verification=doc.needs_verification,
                sources=doc.sources,
            )
        )
    return hits


def answer(query: str) -> Optional[dict[str, Any]]:
    """최상위 검색 결과가 임계값을 넘으면 답변 dict를, 아니면 None을 반환한다.

    반환 dict의 disclaimer는 항상 고정 문구다: DISCLAIMER 상수 참고.
    """
    hits = search(query, k=1)
    if not hits or hits[0].score <= ANSWER_THRESHOLD:
        return None
    top = hits[0]
    return {
        "slug": top.slug,
        "title": top.title,
        "snippet": top.snippet,
        "sources": top.sources,
        "needs_verification": top.needs_verification,
        "disclaimer": DISCLAIMER,
    }
