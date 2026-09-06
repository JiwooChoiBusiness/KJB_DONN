"""Gemini REST 어댑터 (SPEC 2.4, config/llm.yaml).

체인 규칙(PMO 지정): 모델이 바깥 루프, 키가 안쪽 루프. `GEMINI_MODEL_CHAIN`(.env,
없으면 config/llm.yaml의 model_chain)의 각 모델에 대해 `GEMINI_API_KEYS`(.env,
쉼표 구분)의 키를 순서대로 시도한다. 오류 처리:
- 429/500/503/타임아웃/네트워크 오류 -> 다음 키
- 404 -> 다음 모델(같은 모델의 남은 키는 시도하지 않는다)
- 400 -> 즉시 실패(요청 자체 문제이므로 다른 키/모델을 시도해도 결과가 같다)
- 401/403 -> 다음 키
- 429를 받은 키는 `key_cooldown_seconds`(기본 60초) 동안 건너뛴다(프로세스 메모리,
  인스턴스 생존 기간 동안 유지).
모든 모델 x 키 조합이 실패하면 `LLMUnavailable`을 던진다.

키 값은 어떤 경우에도 로그에 남기지 않는다(모델명/키 인덱스/지연/상태코드만 기록).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from dotenv import load_dotenv

from app.llm.provider import LLMResult, LLMUnavailable

load_dotenv()
logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/llm.yaml"


def _load_yaml_config(path: str = DEFAULT_CONFIG_PATH) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return yaml.safe_load(text) or {}


def _extract_text(raw: dict) -> str:
    """candidates[0].content.parts[0].text를 안전하게 꺼낸다(없으면 빈 문자열)."""
    try:
        candidates = raw.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        if not parts:
            return ""
        return parts[0].get("text", "") or ""
    except (AttributeError, IndexError, TypeError):
        return ""


class GeminiProvider:
    """Gemini generateContent REST 체인 어댑터. `app.llm.provider.LLMProvider` 구현체."""

    def __init__(self, keys: list[str], model_chain: list[str], cfg: Optional[dict] = None):
        self.keys = [k.strip() for k in keys if k and k.strip()]
        self.model_chain = [m.strip() for m in model_chain if m and m.strip()]
        cfg = cfg or {}
        self.endpoint = cfg.get("endpoint", "https://generativelanguage.googleapis.com/v1beta")
        self.retry_on_status = set(cfg.get("retry_on_status", [429, 500, 503]))
        self.skip_model_on_status = set(cfg.get("skip_model_on_status", [404]))
        self.fail_fast_on_status = set(cfg.get("fail_fast_on_status", [400, 401, 403]))
        self.key_cooldown_seconds = cfg.get("key_cooldown_seconds", 60)
        self.temperature = cfg.get("temperature", 0)
        self.timeout_seconds = cfg.get("timeout_seconds", 20)
        self.total_deadline_seconds = cfg.get("total_deadline_seconds", 12)
        # explain(설명 문장 슬롯 필링, SPEC 2.8) 전용 체인 상한. extract/chat 경로의
        # total_deadline_seconds와 분리해 화면이 "AI 설명 보기"를 기다리는 동안 더
        # 여유 있게 재시도할 수 있게 한다.
        self.explain_total_deadline_seconds = cfg.get("explain_total_deadline_seconds", 15)
        self.model_cooldown_seconds = cfg.get("model_cooldown_seconds", 120)  # 500/503이 반복된 모델은 잠시 건너뜀
        self.model_failure_threshold = cfg.get("model_failure_threshold", 2)  # 체인 전체 상한. 넘기면 규칙 파서 폴백
        # 429를 받은 (key_index) -> 쿨다운 해제 시각(time.monotonic() 기준). 프로세스
        # 메모리에만 있고 인스턴스가 살아있는 동안 유지된다(SPEC: "프로세스 메모리").
        self._cooldown_until: dict[int, float] = {}
        self._model_cooldown_until: dict[str, float] = {}

    @classmethod
    def from_env(cls, config_path: str = DEFAULT_CONFIG_PATH) -> "GeminiProvider":
        """`.env`의 GEMINI_API_KEYS/GEMINI_MODEL_CHAIN과 config/llm.yaml을 읽어 구성한다.

        GEMINI_MODEL_CHAIN이 비어 있으면 yaml의 model_chain으로 대체한다.
        """
        cfg = _load_yaml_config(config_path)
        keys_raw = os.environ.get("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        chain_raw = os.environ.get("GEMINI_MODEL_CHAIN", "")
        chain = [m.strip() for m in chain_raw.split(",") if m.strip()]
        if not chain:
            chain = list(cfg.get("model_chain", []))
        return cls(keys=keys, model_chain=chain, cfg=cfg)

    def available(self) -> bool:
        """키와 모델 체인이 모두 1개 이상 있으면 True. 네트워크 호출은 하지 않는다."""
        return bool(self.keys) and bool(self.model_chain)

    def health(self) -> dict:
        return {
            "available": self.available(),
            "model_chain": list(self.model_chain),
            "key_count": len(self.keys),
        }

    def _is_cooling_down(self, key_index: int) -> bool:
        until = self._cooldown_until.get(key_index)
        return until is not None and time.monotonic() < until

    def _set_cooldown(self, key_index: int) -> None:
        self._cooldown_until[key_index] = time.monotonic() + self.key_cooldown_seconds

    def _model_cooling_down(self, model: str) -> bool:
        until = self._model_cooldown_until.get(model)
        return bool(until and time.monotonic() < until)

    def _set_model_cooldown(self, model: str) -> None:
        self._model_cooldown_until[model] = time.monotonic() + self.model_cooldown_seconds
        logger.warning("gemini model cooldown model=%s seconds=%s", model, self.model_cooldown_seconds)

    def _post(self, model: str, key_index: int, body: dict, timeout: float) -> requests.Response:
        url = f"{self.endpoint}/models/{model}:generateContent"
        headers = {"x-goog-api-key": self.keys[key_index], "Content-Type": "application/json"}
        return requests.post(url, headers=headers, json=body, timeout=timeout)

    def _run_chain(self, body: dict, deadline_seconds: Optional[float] = None) -> tuple[dict, str, int, int]:
        """(응답 JSON, 모델명, 키 인덱스, 지연ms)를 반환한다. 모두 실패하면 LLMUnavailable.

        `deadline_seconds`를 생략하면 `self.total_deadline_seconds`(extract/chat 기본값)를
        쓴다. `explain`(SPEC 2.8)은 `self.explain_total_deadline_seconds`로 이 값을
        덮어써서 호출한다.
        """
        if not self.available():
            raise LLMUnavailable("Gemini 키 또는 모델 체인이 설정되지 않았습니다.")
        effective_deadline = self.total_deadline_seconds if deadline_seconds is None else deadline_seconds

        last_status: Optional[Any] = None
        chain_started = time.monotonic()
        for model in self.model_chain:
            if self._model_cooling_down(model):
                continue
            model_failures = 0
            for key_index in range(len(self.keys)):
                if self._is_cooling_down(key_index):
                    continue
                elapsed = time.monotonic() - chain_started
                if elapsed > effective_deadline:
                    raise LLMUnavailable(
                        f"체인 전체 시간 상한({effective_deadline}s) 초과(last_status={last_status})."
                    )
                # 요청별 타임아웃은 설정값(timeout_seconds)과 "남은 전체 예산"의 최솟값으로
                # 줄인다. 그렇지 않으면 느린 요청 하나가 timeout_seconds(예: 20s)까지 그대로
                # 붙잡고 있어, 체인 전체가 effective_deadline을 크게 넘겨버릴 수 있다
                # (2026-09-06 리뷰 지적). 이렇게 하면 초과분은 마지막 한 번의 "짧은" 요청
                # 정도로 제한된다.
                deadline_remaining = effective_deadline - elapsed
                request_timeout = max(min(self.timeout_seconds, deadline_remaining), 0.001)

                started = time.monotonic()
                try:
                    resp = self._post(model, key_index, body, request_timeout)
                except requests.RequestException as exc:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    last_status = "network_error"
                    logger.warning(
                        "gemini call model=%s key_index=%s latency_ms=%s status=network_error(%s)",
                        model, key_index, latency_ms, type(exc).__name__,
                    )
                    model_failures += 1
                    if model_failures >= self.model_failure_threshold:
                        self._set_model_cooldown(model)
                        break  # 이 모델은 당분간 건너뜀 -> 다음 모델
                    continue  # 타임아웃/네트워크 오류 -> 다음 키

                latency_ms = int((time.monotonic() - started) * 1000)
                status = resp.status_code
                logger.info(
                    "gemini call model=%s key_index=%s latency_ms=%s status=%s",
                    model, key_index, latency_ms, status,
                )

                if status == 200:
                    return resp.json(), model, key_index, latency_ms

                last_status = status

                if status in self.fail_fast_on_status:
                    if status == 400:
                        raise LLMUnavailable(f"Gemini 요청 오류(400): 스키마 또는 파라미터를 확인하세요.")
                    continue  # 401/403 -> 다음 키

                if status in self.skip_model_on_status:
                    break  # 404 -> 다음 모델(같은 모델의 남은 키는 건너뜀)

                if status in self.retry_on_status:
                    if status == 429:
                        self._set_cooldown(key_index)
                    else:
                        model_failures += 1
                        if model_failures >= self.model_failure_threshold:
                            self._set_model_cooldown(model)
                            break  # 500/503 반복 -> 이 모델 쿨다운, 다음 모델
                    continue  # 429/500/503 -> 다음 키

                # 설정에 없는 상태코드도 보수적으로 다음 키를 시도한다.
                continue

        raise LLMUnavailable(f"모든 모델/키 조합이 실패했습니다(last_status={last_status}).")

    def _build_body(self, system: str, user_text: str, *, response_schema: Optional[dict]) -> dict:
        generation_config: dict[str, Any] = {"temperature": self.temperature}
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation_config,
        }

    def extract(self, text: str, schema: dict, system: str) -> LLMResult:
        """JSON 스키마(OpenAPI 부분집합: type/properties/required/enum/items)로 구조화 추출.

        D4: `text`는 호출자가 이미 `guardrails.mask_pii`로 마스킹한 발화여야 한다.
        """
        body = self._build_body(system, text, response_schema=schema)
        raw, model, key_index, latency_ms = self._run_chain(body)
        text_out = _extract_text(raw)
        try:
            data = json.loads(text_out) if text_out else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = None
        return LLMResult(
            data=data,
            text=text_out,
            model=raw.get("modelVersion") or model,
            key_index=key_index,
            latency_ms=latency_ms,
            usage=raw.get("usageMetadata") or {},
        )

    def explain(self, slots: dict, template_id: str, system: str, schema: Optional[dict] = None) -> LLMResult:
        """범주형 슬롯만으로 설명 문장을 만든다(수치는 포함하지 않는다, D4).

        D4: `slots`는 호출자(`app.services.explain`)가 이미 `app.llm.slotfill.assert_no_digits`로
        검증한 숫자 없는 facts/placeholders여야 한다. `schema`가 있으면 JSON 모드로 호출해
        `LLMResult.data`를 채우고(없거나 파싱 실패면 `data=None`, 호출자가 `result.text`로
        재시도), 체인 전체 시간 상한은 `explain_total_deadline_seconds`를 쓴다(extract/chat과
        분리, SPEC 2.8).
        """
        user_text = json.dumps({"template_id": template_id, "slots": slots}, ensure_ascii=False)
        body = self._build_body(system, user_text, response_schema=schema)
        raw, model, key_index, latency_ms = self._run_chain(
            body, deadline_seconds=self.explain_total_deadline_seconds
        )
        text_out = _extract_text(raw)
        data = None
        if schema is not None and text_out:
            try:
                parsed = json.loads(text_out)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
        return LLMResult(
            data=data,
            text=text_out,
            model=raw.get("modelVersion") or model,
            key_index=key_index,
            latency_ms=latency_ms,
            usage=raw.get("usageMetadata") or {},
        )
