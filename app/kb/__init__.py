"""DONN 제도 안내 KB 패키지.

`kb/*.md` 문서를 읽어 deterministic 키워드 검색을 제공한다. 임베딩이나 LLM 호출은
쓰지 않으며, 채팅의 faq 의도에서 이 모듈의 `answer()`를 호출해 답을 구성한다.
"""
from .search import (
    DEFAULT_KB_DIR,
    DISCLAIMER,
    REQUIRED_SECTIONS,
    KbDoc,
    KbHit,
    answer,
    load_docs,
    reload,
    search,
)

__all__ = [
    "KbDoc",
    "KbHit",
    "load_docs",
    "reload",
    "search",
    "answer",
    "DISCLAIMER",
    "DEFAULT_KB_DIR",
    "REQUIRED_SECTIONS",
]
