"""app/llm 테스트: Gemini 체인 동작(requests.post 모킹)과 가드레일 함수.

SPEC 2.4 체인 규칙: 모델이 바깥 루프, 키가 안쪽 루프. 429/500/503/타임아웃은
다음 키, 404는 다음 모델, 400은 즉시 실패, 401/403은 다음 키, 429를 받은 키는
key_cooldown_seconds 동안 건너뛴다. 실 네트워크 스모크 테스트는
DONN_NETWORK_TESTS=1일 때만 실행한다(기본 스킵).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.llm import guardrails
from app.llm.gemini import GeminiProvider
from app.llm.provider import LLMUnavailable

NETWORK = os.environ.get("DONN_NETWORK_TESTS") == "1"
skip_no_network = pytest.mark.skipif(not NETWORK, reason="DONN_NETWORK_TESTS=1 아니면 스킵")


def _resp(status_code: int, payload: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload or {}
    return r


def _ok_payload(text: str = '{"intent": "compare"}') -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"totalTokenCount": 12},
        "modelVersion": "gemini-test-version",
    }


def _provider(models: list[str], keys: list[str], **cfg_overrides) -> GeminiProvider:
    cfg = {
        "retry_on_status": [429, 500, 503],
        "skip_model_on_status": [404],
        "fail_fast_on_status": [400, 401, 403],
        "key_cooldown_seconds": 60,
        "temperature": 0,
        "timeout_seconds": 5,
        "endpoint": "https://example.invalid/v1beta",
    }
    cfg.update(cfg_overrides)
    return GeminiProvider(keys=keys, model_chain=models, cfg=cfg)


# ---------------------------------------------------------------------------
# 체인 동작
# ---------------------------------------------------------------------------


def test_503_on_key1_then_key2_succeeds():
    provider = _provider(["model-a"], ["k1", "k2"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.side_effect = [_resp(503), _resp(200, _ok_payload())]
        result = provider.extract("문의", {"type": "OBJECT"}, "system")
    assert mock_post.call_count == 2
    assert result.key_index == 1
    assert result.data == {"intent": "compare"}
    # 키 값이 로그에 남지 않아야 하므로 최소한 호출부에 키 자체를 그대로 넘기지 않는지 확인
    # (헤더로 전달되는지 확인 - URL에는 키가 없어야 한다).
    called_url = mock_post.call_args_list[0].args[0]
    assert "k1" not in called_url and "k2" not in called_url


def test_all_keys_503_on_model1_falls_back_to_model2_key1():
    provider = _provider(["model-a", "model-b"], ["k1", "k2"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.side_effect = [_resp(503), _resp(503), _resp(200, _ok_payload())]
        result = provider.extract("문의", {"type": "OBJECT"}, "system")
    assert mock_post.call_count == 3
    assert result.model == "gemini-test-version"
    # 세 번째 호출이 model-b, key1(인덱스 0)이었는지 URL과 key_index로 확인
    third_call_url = mock_post.call_args_list[2].args[0]
    assert "model-b" in third_call_url
    assert result.key_index == 0


def test_404_skips_remaining_keys_of_same_model():
    provider = _provider(["model-a", "model-b"], ["k1", "k2"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        # model-a/key1 -> 404 (모델 자체가 없음) -> key2는 시도하지 않고 바로 model-b/key1로
        mock_post.side_effect = [_resp(404), _resp(200, _ok_payload())]
        result = provider.extract("문의", {"type": "OBJECT"}, "system")
    assert mock_post.call_count == 2
    second_call_url = mock_post.call_args_list[1].args[0]
    assert "model-b" in second_call_url
    assert result.data == {"intent": "compare"}


def test_400_raises_immediately_without_trying_other_keys():
    provider = _provider(["model-a", "model-b"], ["k1", "k2"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.return_value = _resp(400)
        with pytest.raises(LLMUnavailable):
            provider.extract("문의", {"type": "OBJECT"}, "system")
    assert mock_post.call_count == 1  # 다른 키/모델을 추가로 시도하지 않는다


def test_401_moves_to_next_key():
    provider = _provider(["model-a"], ["k1", "k2"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.side_effect = [_resp(401), _resp(200, _ok_payload())]
        result = provider.extract("문의", {"type": "OBJECT"}, "system")
    assert mock_post.call_count == 2
    assert result.key_index == 1


def test_cooldown_skips_a_429_key_on_next_call():
    provider = _provider(["model-a"], ["k1", "k2"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        # 첫 호출: key1 429(쿨다운 등록) -> key2 성공
        mock_post.side_effect = [_resp(429), _resp(200, _ok_payload())]
        first = provider.extract("문의1", {"type": "OBJECT"}, "system")
    assert first.key_index == 1
    assert mock_post.call_count == 2

    with patch("app.llm.gemini.requests.post") as mock_post2:
        # 두 번째 호출: key1은 쿨다운 중이라 건너뛰고 바로 key2 성공(호출 1회만)
        mock_post2.return_value = _resp(200, _ok_payload())
        second = provider.extract("문의2", {"type": "OBJECT"}, "system")
    assert mock_post2.call_count == 1
    assert second.key_index == 1


def test_per_request_timeout_shrinks_as_deadline_approaches():
    """SEV3 #13: 느린(재시도되는) 응답이 이어질 때 요청별 timeout이 timeout_seconds
    고정값이 아니라 남은 total_deadline_seconds 예산만큼 줄어들어야, 체인 전체가
    total_deadline_seconds를 크게 초과하지 않는다(짧은 요청 한 번 정도의 초과만 허용)."""
    provider = _provider(
        ["model-a"], ["k1", "k2"],
        total_deadline_seconds=4, timeout_seconds=20,
    )
    # 실제 시간이 흐르는 것처럼 매 time.monotonic() 호출마다 1초씩 흐르게 한다(느린 응답 모사).
    fake_now = {"t": 0.0}

    def _tick() -> float:
        fake_now["t"] += 1.0
        return fake_now["t"]

    with patch("app.llm.gemini.time.monotonic", side_effect=_tick), \
         patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.side_effect = [_resp(503), _resp(503)]
        with pytest.raises(LLMUnavailable):
            provider.extract("문의", {"type": "OBJECT"}, "system")

    assert mock_post.call_count == 2
    timeouts = [call.kwargs["timeout"] for call in mock_post.call_args_list]
    # 두 번째 요청 시점에는 첫 번째보다 남은 예산이 줄어 있어야 한다.
    assert timeouts[1] < timeouts[0]
    # 어떤 요청도 설정된 timeout_seconds(20s)를 그대로 쓰지 않는다(둘 다 4s 예산 안에서 줄었다).
    assert all(t <= 4 for t in timeouts)


def test_all_exhausted_raises_llm_unavailable():
    provider = _provider(["model-a"], ["k1"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.return_value = _resp(503)
        with pytest.raises(LLMUnavailable):
            provider.extract("문의", {"type": "OBJECT"}, "system")


def test_available_false_without_keys_or_models():
    assert _provider([], ["k1"]).available() is False
    assert _provider(["model-a"], []).available() is False
    assert _provider(["model-a"], ["k1"]).available() is True


def test_explain_returns_text_without_data():
    provider = _provider(["model-a"], ["k1"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.return_value = _resp(200, _ok_payload(text="상환 계획을 확인해보세요."))
        result = provider.explain({"category": "credit"}, "template-1", "system")
    assert result.data is None
    assert result.text == "상환 계획을 확인해보세요."
    assert result.usage.get("totalTokenCount") == 12


def test_extract_invalid_json_returns_none_data():
    provider = _provider(["model-a"], ["k1"])
    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.return_value = _resp(200, _ok_payload(text="이건 JSON이 아닙니다"))
        result = provider.extract("문의", {"type": "OBJECT"}, "system")
    assert result.data is None
    assert result.text == "이건 JSON이 아닙니다"


def test_from_env_reads_keys_and_chain(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "a,b,c")
    monkeypatch.setenv("GEMINI_MODEL_CHAIN", "m1,m2")
    provider = GeminiProvider.from_env()
    assert provider.keys == ["a", "b", "c"]
    assert provider.model_chain == ["m1", "m2"]
    assert provider.available() is True


# ---------------------------------------------------------------------------
# 가드레일: check_text / mask_pii
# ---------------------------------------------------------------------------


def test_check_text_detects_banned_terms():
    banned = ["테스트은행", "가상저축은행"]
    hits = guardrails.check_text("테스트은행 신용대출을 추천합니다", banned)
    assert hits == ["테스트은행"]


def test_check_text_no_hits_returns_empty():
    banned = ["테스트은행"]
    assert guardrails.check_text("이번 달 남는 돈은 10,000원입니다", banned) == []


def test_check_text_empty_banned_list_is_safe():
    assert guardrails.check_text("아무 문장", []) == []


def test_mask_pii_phone_number():
    assert guardrails.mask_pii("010-1234-5678로 연락주세요") == "[전화번호]로 연락주세요"


def test_mask_pii_rrn():
    assert guardrails.mask_pii("주민등록번호 900101-1234567 입니다") == "주민등록번호 [주민등록번호] 입니다"


def test_mask_pii_dot_separated_phone_number():
    assert guardrails.mask_pii("010.1234.5678로 연락주세요") == "[전화번호]로 연락주세요"


def test_mask_pii_dot_separated_rrn():
    assert guardrails.mask_pii("주민등록번호 900101.1234567 입니다") == "주민등록번호 [주민등록번호] 입니다"


def test_mask_pii_email():
    assert guardrails.mask_pii("test.user@example.com으로 보내주세요") == "[이메일]으로 보내주세요"


def test_mask_pii_account_like_digits():
    masked = guardrails.mask_pii("계좌번호 110123456789 로 보내주세요")
    assert "[계좌번호]" in masked
    assert "110123456789" not in masked


def test_mask_pii_does_not_touch_small_amounts():
    # 9자리 이하 원화 금액은 계좌번호로 오인해 마스킹하지 않아야 parse_message가 동작한다.
    text = "신용대출 30000000원 비교해줘"
    assert guardrails.mask_pii(text) == text


def test_mask_pii_empty_string():
    assert guardrails.mask_pii("") == ""


# ---------------------------------------------------------------------------
# 가드레일: parse_message
# ---------------------------------------------------------------------------


def test_parse_message_rate_cap():
    result = guardrails.parse_message("금리 4% 이하로 비교해줘")
    assert result["intent"] == "compare"
    assert result["max_rate"] == 4.0


def test_parse_message_term_years_and_months():
    assert guardrails.parse_message("기간 5년으로 다시 보여줘")["term_months"] == 60
    assert guardrails.parse_message("36개월로 계산해줘")["term_months"] == 36


def test_parse_message_amount_variants():
    assert guardrails.parse_message("3천만원짜리 신용대출")["amount"] == 30_000_000
    assert guardrails.parse_message("5,000만 원 대출 비교")["amount"] == 50_000_000
    assert guardrails.parse_message("1억 대출 비교해줘")["amount"] == 100_000_000
    assert guardrails.parse_message("1억 5천만원 대출 비교")["amount"] == 150_000_000


def test_parse_message_exclude_company():
    result = guardrails.parse_message("가상은행 빼고 비교해줘")
    assert result["exclude_companies"] == ["가상은행"]


def test_parse_message_exclude_company_with_particle():
    result = guardrails.parse_message("가상은행은 제외하고 비교해줘")
    assert result["exclude_companies"] == ["가상은행"]


def test_parse_message_category_keywords():
    assert guardrails.parse_message("신용대출 비교해줘")["category"] == "credit"
    assert guardrails.parse_message("주택담보대출 금리 알려줘")["category"] == "mortgage"
    assert guardrails.parse_message("전세자금대출 비교")["category"] == "jeonse"
    assert guardrails.parse_message("적금 상품 보여줘")["category"] == "saving"
    assert guardrails.parse_message("예금 금리 비교")["category"] == "deposit"
    assert guardrails.parse_message("정책서민금융 상품")["category"] == "policy"


def test_parse_message_intents():
    assert guardrails.parse_message("상환표 보여줘")["intent"] == "schedule"
    assert guardrails.parse_message("시나리오 보여줘")["intent"] == "scenario"
    assert guardrails.parse_message("소비 패턴 보여줘")["intent"] == "spending"
    assert guardrails.parse_message("나 뭐부터 해야해?")["intent"] == "action"
    assert guardrails.parse_message("공시 비교하고 싶어")["intent"] == "compare"


def test_parse_message_no_signal_falls_back_to_faq():
    result = guardrails.parse_message("안녕하세요")
    assert result["intent"] == "faq"
    assert "amount" not in result
    assert "category" not in result


def test_parse_message_slots_without_intent_keyword_default_to_compare():
    # scope 문서 시나리오 4: "금리 4% 이하만", "기간 5년으로" 같은 후속 조건 발화는
    # 명시적 의도 키워드가 없어도 compare로 취급되어야 재실행이 이어진다.
    result = guardrails.parse_message("기간 5년으로")
    assert result["intent"] == "compare"
    assert result["term_months"] == 60


def test_parse_message_empty_text():
    result = guardrails.parse_message("")
    assert result == {"intent": "faq"}


# ---------------------------------------------------------------------------
# 실 네트워크 스모크 테스트 (DONN_NETWORK_TESTS=1일 때만)
# ---------------------------------------------------------------------------


@skip_no_network
def test_gemini_live_extract_smoke():
    provider = GeminiProvider.from_env()
    assert provider.available()
    schema = {
        "type": "OBJECT",
        "properties": {"intent": {"type": "STRING", "enum": ["compare", "faq"]}},
        "required": ["intent"],
    }
    result = provider.extract("신용대출 공시 비교하고 싶어요", schema, "의도만 추출하세요.")
    assert result.data is not None
    assert result.data.get("intent") in ("compare", "faq")
