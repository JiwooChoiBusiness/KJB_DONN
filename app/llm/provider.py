"""LLM 공급자 인터페이스 (SPEC 2.4).

`app/llm`의 다른 모듈(gemini.py)과 `app/api`가 이 계약에 맞춰 구현/사용한다.
PoC 공급자는 Gemini이지만, 실도입 시 다른 공급자로 교체할 수 있도록 어댑터
경계를 여기서 고정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass
class LLMResult:
    """LLM 호출 1회의 결과. `data`는 JSON 스키마 추출 결과(extract), 없으면 None.
    `text`는 원문 텍스트(explain은 항상 text만 채운다)."""

    data: Optional[dict[str, Any]]
    text: str
    model: str
    key_index: int
    latency_ms: int
    usage: dict[str, Any] = field(default_factory=dict)


class LLMUnavailable(Exception):
    """모델 x 키 조합을 모두 시도했지만 성공하지 못했을 때 발생한다.

    호출자(app/api/routes.py)는 이 예외를 잡아 규칙 기반(app.llm.guardrails.parse_message)
    또는 고정 템플릿 폴백을 써야 한다. 요청 자체가 잘못된 경우(HTTP 400)도 이 예외로
    표면화된다(즉시 실패, 재시도하지 않음).
    """


@runtime_checkable
class LLMProvider(Protocol):
    """SPEC 2.4의 공급자 계약. 구현체는 `app.llm.gemini.GeminiProvider`."""

    def available(self) -> bool:
        """키와 모델 체인이 최소 1개 이상 설정되어 있으면 True (네트워크 호출 없음)."""
        ...

    def extract(self, text: str, schema: dict, system: str) -> LLMResult:
        """JSON 스키마(OpenAPI 부분집합)에 맞춰 구조화된 슬롯을 추출한다.

        D4 규칙: `text`는 반드시 `guardrails.mask_pii`로 마스킹한 발화만 전달하고,
        잔액/소득/금리 등 프로필 수치는 절대 포함하지 않는다.
        """
        ...

    def explain(
        self, slots: dict, template_id: str, system: str, schema: Optional[dict] = None,
        deadline_seconds: Optional[float] = None,
    ) -> LLMResult:
        """범주형 슬롯만으로 설명 문장을 생성한다(수치는 이후 코드가 문장에 삽입한다).

        D4 규칙: `slots`(facts/placeholders)는 반드시 `app.llm.slotfill.assert_no_digits`를
        통과한(숫자 문자가 하나도 없는) 값만 전달한다. 개인신용정보에서 파생한 수치(G3)는
        이 경로로 절대 보내지 않는다. `schema`가 있으면 JSON 모드(responseMimeType/
        responseSchema)로 호출해 `LLMResult.data`를 채우고, 없으면 기존처럼 텍스트만
        돌려준다(`LLMResult.text`). `deadline_seconds`를 생략하면 구현체의 기본 설명 체인
        상한(예: `explain_total_deadline_seconds`)을 쓴다(SPEC 2.9: 대화 화면의 설명 생성은
        `chat_explain_deadline_seconds`로 더 짧게 줄인다).
        """
        ...

    def health(self) -> dict:
        """진단 정보(가용 여부, 모델 체인, 키 개수 등). 키 값 자체는 절대 포함하지 않는다."""
        ...
