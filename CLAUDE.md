# DONN PoC

생성형 LLM 기반 개인 부채 코치(한국). 이 저장소는 데모(PoC)용이며 범위는 `docs/DONN_POC_SCOPE.md`, 구현 계약은 `SPEC.md`, 공용 타입은 `app/models.py`가 단일 진실이다.

## 절대 규칙
- 순위와 수치는 코드가 계산하고 LLM은 문장만 쓴다. 첫 화면 로드 시 LLM 호출 0회.
- M0 모드: 상품 실명, 금융회사명, 판매 페이지 바로가기를 사용자 화면에 노출하지 않는다. 익명 라벨과 금감원 공시 링크만 쓴다. "추천" 대신 "비교", "공시 열람".
- 문서와 화면 어디에도 em dash(—)를 쓰지 않는다. EN/KR 언어 토글은 없다. UI 텍스트는 한국어.
- 규제 수치는 `config/policy_params.yaml`에서만 읽고, 검증 안 된 값은 "(확인 필요)"로 표시한다.
- 개인신용정보 파생 수치(G3)는 외부 LLM으로 보내지 않는다(슬롯 필링). 발화는 PII 마스킹 후 전송.
- 키는 `.env`에만 둔다. 소스, 로그, 커밋에 키를 넣지 않는다. 오픈뱅킹은 PoC 범위 밖.

## 실행
```
run.bat            # 서버 (http://localhost:3666)
run.bat load       # 상품 스냅샷 적재 (금감원 + 공공데이터)
.venv\Scripts\python -m pytest -q
```

## 모델 정책 (Claude Code 작업 시)
- 오케스트레이터(메인 세션): Fable 5.1. 판단, 통합, 규제·계산 검토는 여기서만.
- `builder` 에이전트(Sonnet): 백엔드, 데이터, 테스트 구현. 지정된 파일만 수정.
- `web-designer` 에이전트(Opus): `web/` 화면 구현과 다듬기.
- `critic` 에이전트(Opus, 읽기 전용): 교차 검토, 반박 검증. 파일 수정 금지.
- 서브에이전트에 Fable을 쓰지 않는다. 에이전트는 파일에 쓰고 짧은 보고만 반환한다.

## 모듈 경계
`app/core` 순수 함수(I/O 금지) → `app/data`만 네트워크·DB → `app/services` 조합 → `app/api` HTTP → `web/` 정적 페이지. LLM은 `app/llm` 어댑터 뒤에 두고 PoC는 Gemini 체인(`config/llm.yaml`).
