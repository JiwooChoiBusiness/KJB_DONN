# DONN PoC 구현 명세 (SPEC) v0.1

작성일 2026-09-06. 이 문서는 모듈 간 계약이다. `app/models.py`의 타입이 단일 진실이며, 본 문서와 다르면 `models.py`를 따른다. 범위는 `docs/DONN_POC_SCOPE.md`, 배경은 `docs/DONN_PLAN_v0.1.md`(본안)와 `docs/DONN_ADDENDUM_v0.1.md`(부록).

## 0. 원칙

1. 순위와 수치는 코드가, 문장은 LLM이 만든다. 첫 화면 로드 시 LLM 호출 0회.
2. 같은 입력이면 같은 결과다. `result_hash`가 실행마다 동일해야 하며, 날짜와 난수는 함수 인자(`today`, `seed`)로 받는다.
3. M0 모드: 상품 실명, 금융회사명, 판매 페이지 바로가기를 사용자 화면에 노출하지 않는다. 익명 라벨("A은행 신용대출")과 금감원 공시 링크만 쓴다. 내부 raw 저장은 허용.
4. 금액은 정수 원, 금리는 연 %. 반올림은 행 단위 round half up, 마지막 회차에서 잔액 0 보정.
5. 문구 규칙: em dash(—) 사용 금지. 면책과 AI 고지는 `models.DISCLAIMER`, `models.AI_NOTICE` 상수만 사용. "추천" 대신 "비교", "공시 열람" 용어. EN/KR 토글 없음.
6. 규제 수치는 `config/policy_params.yaml`에서만 읽는다. `needs_verification=True`면 화면에 "(확인 필요)" 배지.
7. 모듈 경계: `app/core`는 순수 함수(I/O 금지, `app.models`만 import). `app/data`만 네트워크와 DB를 만진다. `app/services`가 둘을 조합하고 `app/api`가 HTTP로 노출한다.

## 1. 디렉터리

```
app/models.py        공용 타입 (수정 시 SPEC 동기화)
app/core/            schedule.py loan.py capacity.py scenarios.py rules.py ranking.py hashing.py
app/data/            db.py finlife.py datago.py products.py policy.py synthetic.py
app/services/        insights.py compare.py actions.py decisions.py session.py
app/llm/             provider.py gemini.py guardrails.py
app/api/             schemas.py routes.py
app/main.py          FastAPI 앱, 정적 파일
web/                 index.html app.js styles.css
config/              llm.yaml policy_params.yaml
scripts/             load_products.py gen_synthetic.py
tests/               test_core_schedule.py test_core_rules.py test_core_ranking.py test_api.py
data/                donn.db (gitignore), cache/ (API 응답 캐시)
```

## 2. 모듈 계약

### 2.1 app/core (순수 함수)

schedule.py
- `monthly_payment_equal(principal: int, annual_rate_pct: float, months: int) -> int`
- `build_schedule(loan: Loan, *, rate_override: float | None = None, extra_payment: int = 0, months_override: int | None = None) -> LoanSchedule`
  현재 잔액 기준 남은 스케줄. `grace_months` 동안 이자만 납부, 이후 `remaining_months - grace_months`로 상환. BULLET은 매월 이자와 만기 원금. REVOLVING은 매월 이자만, 마지막 회차에 원금. `extra_payment`는 매월 원금 추가상환이며 잔액 0이면 종료. `monthly_payment_override`가 있고 원리금균등이면 그 값을 월납입액으로 사용하되 이자보다 작으면 무시하고 `assumptions`에 기록.

loan.py
- `prepay_fee(loan: Loan, amount: int, today: date) -> int`
  `prepay_fee_until`이 today 이후면 `amount × prepay_fee_rate/100 × (남은 수수료 개월 / 36)` 반올림, 아니면 0. 산식 근거는 policy 항목 `prepay_fee_period_months`(확인 필요).
- `refinance_compare(loan: Loan, new_rate: float, new_term_months: int, fee: int) -> dict`
  키: `current_total_interest, new_total_interest, interest_saving, monthly_before, monthly_after, breakeven_months`(수수료 / 월 이자 절감, 절감 없으면 None).
- `extra_payment_effect(loan: Loan, extra_monthly: int) -> dict`
  키: `months_saved, interest_saved, new_months`.

capacity.py
- `compute_capacity(profile: UserProfile, schedules: list[LoanSchedule]) -> Capacity`
  `debt_service = Σ first_payment`, `net = income - fixed - variable - debt_service`, `ratio = debt_service / income`.
  band: net < 0 → NEGATIVE; ratio ≥ 0.5 또는 net < 0.05 × income → TIGHT; net ≥ 0.2 × income → COMFORTABLE; 그 외 OK. `explanation`은 숫자가 든 한국어 한 문장, 상품명 없음.

scenarios.py
- `run_scenarios(profile: UserProfile, params: PolicyParams, *, horizon_months: int = 60) -> list[ScenarioResult]`
  BASE: 현재 조건. ADVERSE: 변동금리 대출 금리 + `stress_variable_rate_add_pct`(기본 1.0), 변동지출 +5%. FAVORABLE: 변동금리 -0.5%p, 매월 잉여의 50%를 최고금리 대출에 추가상환. `points`는 월별, `debt_free_month`는 모든 잔액이 0이 되는 첫 달(없으면 None).

rules.py
- `evaluate_rules(profile: UserProfile, schedules: list[LoanSchedule], capacity: Capacity, params: PolicyParams, *, today: date) -> list[ActionCard]` (priority 오름차순 정렬)
  - R0 안전모드(priority 0): `delinquency_signal` 플래그 또는 band NEGATIVE 또는 ratio ≥ 0.7. safe_mode=True 카드 1장: 공적 상담 창구 안내(신용회복위원회, 서민금융진흥원)와 오늘 할 일 1개. R0 발동 시 R3(대환)·R2(추가상환) 카드는 생성하지 않는다.
  - R5 만기·거치 종료 임박(10): BULLET/REVOLVING이고 remaining_months ≤ 3, 또는 grace_months가 1~2. 만기 금액과 준비 여력.
  - R4 비상금(20): `emergency_fund < fixed_expenses × emergency_fund_months`(기본 1). 비상금 먼저 카드. 이 경우 R2 priority를 45로 낮춤.
  - R6 고금리 소액(30): card_loan/overdraft, 금리 ≥ 12, 잔액 ≤ 월소득 × 2.
  - R2 상환 우선순위(40): 대출 2개 이상이면 최고금리 대출을 1순위로, 월 여력 전액 추가상환 시 `extra_payment_effect` 수치 제시(여력 ≤ 0이면 카드 생략).
  - R1 금리인하요구권(50): credit/card_loan/overdraft 중 금리 ≥ `rate_cut_request_min_rate`(기본 6.0)이고 플래그 `income_up` 또는 `job_changed`. 비용 0, 신청 절차 3단계.
  - R3 대환 후보(60): 잔여기간 ≥ 12, 금리 ≥ 7.0, 수수료 회수기간(`prepay_fee` / 월 이자 절감, 절감은 `refi_rate_gap_min_pct` 가정) < 잔여기간. 카드 문구는 "내 조건으로 공시 비교하기"이며 chip intent `compare`, params `{category, amount, term_months, target_loan_id}`. 상품명 언급 금지.
  카드의 `numbers`는 전부 코드 산출. `assumptions`에 사용한 policy 키와 확인 필요 여부를 적는다.

ranking.py
- `eligible(products: list[ProductSnapshot], ctx: CompareContext) -> list[ProductSnapshot]`
  category 일치, lender_group ∈ ctx.lender_groups, exclude_companies 제외, rate_type 필터, max_rate 이하 옵션 존재, credit_band가 있으면 해당 구간 옵션 존재(없으면 avg 옵션 허용).
- `select_option(product: ProductSnapshot, ctx: CompareContext) -> ProductOption | None`
  우선순위: credit_band 일치 → term 일치 → rate_kind (avg > base > min).
- `rank(products: list[ProductSnapshot], ctx: CompareContext, params: PolicyParams, *, current_total_interest: int | None = None) -> list[CompareItem]`
  옵션 금리로 `monthly_payment_equal`과 총이자를 계산(ctx.amount, ctx.term_months, ctx.repay_method). sort_key로 정렬, 동률은 product.id 사전순. anon_label은 정렬 후 순서대로 A, B, C… + 그룹명(은행/저축은행/정책상품) + 카테고리명. `rate_semantics == DISCLOSED_AVG_RATE`면 notes에 "신용점수 구간별 전월 평균금리 기준" 추가. 상위 10개.
- `sort_explain(ctx: CompareContext) -> str`

hashing.py
- `canonical_json(obj) -> str`, `fingerprint(obj) -> str`(sha256 hex), `result_hash(items: list[CompareItem], ctx: CompareContext, snapshot_id: str, versions: dict) -> str`.

### 2.2 app/data

db.py
- `DB_PATH` = env `DONN_DB_PATH`(기본 data/donn.db). `get_conn()`, `init_db()`.
- 테이블: `snapshots(snapshot_id PK, source, fetched_at, count, note)`, `products(id PK, snapshot_id, source, category, lender_group, company_code, company_name, product_code, product_name, rate_semantics, disclosure_month, disclosure_url, json)`, `profiles(id PK, json, updated_at)`, `decisions(decision_id PK, kind, input_fingerprint, result_hash, context_json, result_json, versions_json, created_at)`, `session(key PK, value)`.

finlife.py (금감원 금융상품한눈에)
- `FinlifeClient(auth_key)`, `.fetch(service: str, top_fin_grp_no: str, page: int) -> dict`, `.fetch_all(service, top_fin_grp_no) -> tuple[list, list]`(baseList, optionList).
- `normalize_finlife(service: str, group: str, base_rows, option_rows, snapshot_id) -> list[ProductSnapshot]`
- 서비스와 카테고리: `depositProductsSearch`→DEPOSIT, `savingProductsSearch`→SAVING, `mortgageLoanProductsSearch`→MORTGAGE, `rentHouseLoanProductsSearch`→JEONSE, `creditLoanProductsSearch`→CREDIT(rate_semantics=DISCLOSED_AVG_RATE, 신용점수 구간별 금리 필드를 credit_band + rate_kind="avg" 옵션으로). 권역 020000 은행 → BANK, 030300 저축은행 → SAVINGS_BANK, 030200 여신전문 → CARD/CAPITAL.
- 주소 `https://finlife.fss.or.kr/finlifeapi/{service}.json`, 파라미터 `auth, topFinGrpNo, pageNo`. http는 307로 https로 넘어간다. 실제 필드명은 첫 응답을 `data/cache/finlife_{service}_{group}.json`에 저장해 확정한다. `disclosure_url`은 금감원 비교공시 페이지.

datago.py (공공데이터포털)
- `DataGoClient(service_key)`(디코딩된 키를 `requests` params로 전달). 공통 파라미터 `serviceKey, pageNo, numOfRows, resultType=json`(데이터셋마다 다를 수 있음).
- 디딤돌 금리: `https://apis.data.go.kr/B551408/didimdol-loan-rate/didimdol-info` (데이터셋 15082028) → MORTGAGE, lender_group POLICY, source DATAGO_HF.
- 서민금융 상품 기본정보: base `https://apis.data.go.kr/1160100/service/GetSmallLoanFinanceInstituteInfoService` (데이터셋 15094787). 오퍼레이션명은 데이터셋 페이지의 API 명세에서 확인해 상수로 기록 → POLICY, source DATAGO_FSC.
- 대출상품한눈에: base `https://apis.data.go.kr/B553701/LoanProductSearchingInfo` (데이터셋 15106208). 오퍼레이션명 확인 후 상수로 기록 → POLICY, source DATAGO_KINFA.
- 응답은 `data/cache/`에 저장. 실패 시 예외 대신 빈 목록과 로그. `disclosure_url`은 데이터셋 페이지 URL 또는 기관 안내 URL.

products.py
- `load_snapshot(sources: list[str], groups: list[str]) -> str`(snapshot_id 반환, 형식 `YYYYMMDD-HHMM-<source>`), `latest_snapshot_id() -> str | None`, `query(category: ProductCategory, snapshot_id: str | None = None) -> list[ProductSnapshot]`, `stats() -> dict`.

policy.py
- `load_policy_params(path: str = "config/policy_params.yaml") -> PolicyParams`.
- 시드 키(값, needs_verification): `dsr_limit_bank_pct`(40, 확인), `dsr_limit_nonbank_pct`(50, 확인), `stress_dsr_rate_metro_pct`(3.0, 확인), `stress_dsr_rate_other_pct`(1.5, 확인), `credit_loan_dsr_assumed_term_months`(60, 확인), `prepay_fee_period_months`(36, 확인), `interest_income_tax_pct`(15.4, 확인), `rate_cut_request_min_rate`(6.0, 내부 기준), `refi_rate_gap_min_pct`(1.0, 내부), `stress_variable_rate_add_pct`(1.0, 내부), `emergency_fund_months`(1, 내부). 각 항목에 `source_url, source_title, verified_at` 기입(본안 7.2.2와 12장 참고), 모르면 빈 값과 `needs_verification: true`.

synthetic.py
- `PERSONAS: list[UserProfile]` 8명. 본안 8장(`docs/DONN_PLAN_v0.1.md` 1319~1452행)의 페르소나 정의를 따른다. 문서에 수치가 없으면 유형에 맞게 채운다: P1 다중 소액부채 청년 직장인, P2 주담대·전세 가구, P3 다중채무 40대, P4 자영업자, P5 취약차주(연체 신호), P6 학자금 사회초년생, P7 은퇴 앞둔 50대, P8 취약 청년.
- `generate_transactions(persona_id: str, *, months: int = 3, seed: int = 42, end: date) -> list[Transaction]`, `transactions_to_csv(rows) -> str`.

scripts
- `python -m scripts.load_products --source finlife --groups 020000,030300` / `--source datago`
- `python -m scripts.gen_synthetic --persona P1 --months 3 --out data/cache/P1.csv`

### 2.3 app/services

insights.py
- `build_home(profile: UserProfile | None, product_stats: dict, params: PolicyParams, *, today: date) -> HomePayload`
  - 프로필 없음: onboarding 카드 2장(가정을 명시한 미리 보기) + Tier 0 정적 칩 5개, top_action None.
  - 프로필 있음: schedules → capacity → actions. 카드: (1) top_action 카드(kind action), (2) debt 카드(총잔액, 월 원리금, 남은 총이자, evidence에 수치), (3) progress 또는 neutral 카드(가장 먼저 끝나는 대출과 남은 개월). 균형 규칙: negative 톤만 있으면 neutral/positive 카드 1장을 추가. 칩 Tier 1은 카드에서 파생(intent compare/schedule/scenario). 모든 문장은 `guardrails.check_text`를 통과해야 한다.
- 각 카드의 `explain`에는 어떤 입력과 규칙에서 나왔는지 한 문장.

compare.py
- `prepare_context(profile: UserProfile | None, intent_params: dict) -> CompareContext`
  추정 규칙: target_loan = intent_params.target_loan_id 또는 최고금리 신용대출, amount = 잔액, term = 남은 개월, credit_band = 프로필 값. 추정한 필드명을 `estimated_fields`에 나열, `user_confirmed=False`.
- `run_compare(ctx: CompareContext, profile: UserProfile | None, *, today: date) -> CompareResult`
  `user_confirmed`가 False면 `ValueError`. products.query → ranking.eligible/rank → result_hash → decisions.save. `assumptions`에 스냅샷 ID, 공시 기준월, 반올림 규칙, 공시 금리와 실제 금리의 차이 문구.

actions.py
- `list_actions(profile: UserProfile, params: PolicyParams, *, today: date) -> list[ActionCard]`

decisions.py
- `save(record: DecisionRecord)`, `get(decision_id) -> DecisionRecord | None`, `list_recent(limit: int = 20) -> list[DecisionRecord]`, `replay(decision_id) -> dict`(`{match: bool, result_hash, replay_hash}`; compare만 지원).

session.py
- `get_profile() -> UserProfile | None`, `set_profile(profile)`, `clear_profile()` (db.session 테이블 key `current_profile`). 단일 데모 세션.

### 2.4 app/llm

- provider.py: `class LLMProvider(Protocol)`: `available() -> bool`, `extract(text: str, schema: dict, system: str) -> LLMResult`, `explain(slots: dict, template_id: str, system: str) -> LLMResult`, `health() -> dict`. `LLMResult`는 `{"data": dict | None, "text": str, "model": str, "key_index": int, "latency_ms": int, "usage": dict}` 형태의 dict(또는 dataclass).
- gemini.py: `GeminiProvider(keys: list[str], model_chain: list[str], cfg: dict)`. `.env`의 `GEMINI_API_KEYS`(쉼표 구분 8개)와 `GEMINI_MODEL_CHAIN`, `config/llm.yaml`을 읽는다. 키가 하나도 없으면 `available()=False`.
  - 체인 규칙(PMO 지정): 모델이 바깥 루프, 키가 안쪽 루프. `gemini-3.8-flash`를 키 1~8 순서로 시도해 모두 실패하면 `gemini-3.7-flash`를 다시 키 1부터, 이어서 `gemini-3.6-flash`, `gemini-3.5-flash-lite`. 전부 실패하면 `LLMUnavailable` 예외를 던지고 호출자는 템플릿 폴백을 쓴다.
  - 오류 처리: 429/500/503/타임아웃은 다음 키. 404는 다음 모델. 400은 즉시 실패(요청 문제). 401/403은 다음 키. 429를 받은 키는 `key_cooldown_seconds`(60초) 동안 건너뛴다(프로세스 메모리).
  - 호출: `POST {endpoint}/models/{model}:generateContent`, 헤더 `x-goog-api-key`(URL에 키를 넣지 않는다), 본문 `systemInstruction.parts[].text`, `contents[{role:"user", parts:[{text}]}]`, `generationConfig {temperature: 0, responseMimeType: "application/json", responseSchema: <OpenAPI 부분집합: type, properties, required, enum, items만>}`. `explain`은 responseMimeType 없이 텍스트. 응답은 `candidates[0].content.parts[0].text`, 사용량은 `usageMetadata`, 실제 모델은 `modelVersion`.
  - 로그: 모델, 키 인덱스, 지연, 토큰 수만 기록. 키 값과 사용자 발화 원문은 로그에 남기지 않는다(발화는 `mask_pii` 후 전송).
  - 검증(2026-09-06): 키 1로 모델 목록 조회 성공(54개, 체인 4개 모델 모두 존재). `gemini-3.5-flash-lite`에 JSON 스키마 요청 성공(의도·금리 상한·기간 추출 정확). `gemini-3.8-flash`는 같은 시각 503(수요 폭주)이었으므로 체인 폴백이 실제로 필요하다.
- guardrails.py: `check_text(text: str, banned: list[str]) -> list[str]`(검출된 금지어), `mask_pii(text: str) -> str`(전화번호, 주민등록번호, 계좌번호 패턴), `parse_message(text: str) -> dict`(규칙 기반: 금리 상한 "4% 이하", 기간 "5년"/"36개월", 금액 "3천만원"/"5,000만 원", 제외 은행 "○○ 빼고", 카테고리 키워드 신용대출/주담대/전세/예금/적금, 의도 compare/schedule/scenario/action/faq).
- `banned` 목록은 products 테이블의 company_name과 product_name에서 만든다(services가 조합).

### 2.5 app/api와 main

schemas.py: `ComparePrepareRequest{intent: str, params: dict}`, `ChatRequest{message: str}`, `ReplayResponse{match, result_hash, replay_hash}`, `PersonaSummary{id, display_name, one_liner, loans_count, total_balance}`.

| 메서드 | 경로 | 응답 | 비고 |
|---|---|---|---|
| GET | /api/health | `{status, engine_version, snapshot_id, llm_provider, llm_available}` | |
| GET | /api/personas | `list[PersonaSummary]` | |
| POST | /api/session/persona/{persona_id} | `UserProfile` | 현재 세션 프로필로 로드 |
| DELETE | /api/session | `{ok}` | 프로필 초기화 |
| GET | /api/profile | `UserProfile` 또는 404 | |
| PUT | /api/profile | `UserProfile` | 수기 입력 저장 |
| GET | /api/home | `HomePayload` | LLM 0회 |
| GET | /api/loans/{loan_id}/schedule?extra=0 | `LoanSchedule` | |
| GET | /api/scenarios?horizon=60 | `list[ScenarioResult]` | |
| GET | /api/actions | `list[ActionCard]` | |
| POST | /api/compare/prepare | `CompareContext` | body `ComparePrepareRequest` |
| POST | /api/compare/run | `CompareResult` | body `CompareContext`, `user_confirmed=false`면 422 |
| GET | /api/products/stats | `{snapshot_id, fetched_at, by_source, by_category, total}` | |
| GET | /api/decisions?limit=20 | `list[DecisionRecord]`(result 제외 요약) | |
| GET | /api/decisions/{id} | `DecisionRecord` | |
| POST | /api/decisions/{id}/replay | `ReplayResponse` | |
| POST | /api/chat | `ChatReply`(+`chat_id`) | body `{message, chat_id?}`. Gemini 추출 → 규칙 파서 폴백. 사용자 발화(PII 마스킹본)와 응답을 현재 프로필의 대화 로그에 저장 |
| GET | /api/chats | `[{id, profile_id, title, created_at, updated_at, message_count}]` | 현재 프로필(페르소나)의 대화 목록, 최근순. 프로필 없으면 guest |
| POST | /api/chats | 대화 1건 | body `{title?}` 새 대화 |
| GET | /api/chats/{id}/messages | `[{id, role(user/reply), text, llm_used, action, chips, created_at}]` | 다른 프로필의 대화면 404 |
| DELETE | /api/chats/{id} | `{ok}` | |
| GET | /api/meta | `{credit_bands, categories, repay_methods, sort_keys, lender_groups}` | 화면 선택지 |
| GET | /api/synthetic/{persona_id}/transactions.csv | text/csv | 합성 거래내역 |
| POST | /api/compare/{decision_id}/explain | `ExplainResult` | body `{refresh?: bool}`(선택). 결정 기록 없으면 404. 저장된 설명이 있으면 LLM 호출 없이 `cached=true` |
| GET | /api/compare/{decision_id}/explain | `ExplainResult` | 저장된 설명만. 없으면 404(생성하지 않음) |
| POST | /api/actions/{action_id}/explain | `ExplainResult` | 현재 프로필의 행동 카드. 프로필 없음 또는 카드 없음이면 404 |
| POST | /api/chat/stream | `text/event-stream` | body `{message, chat_id?}`. `stage`/`reply`/`error` 이벤트(2.9). 대화 로그 저장은 `/api/chat`과 동일 |

main.py: `FastAPI(title="DONN PoC")`, `GET /` → `web/index.html`, `/static` → `web/`. 시작 시 `init_db()`.

### 2.6 소비 패턴 (P5)

배경: `docs/DONN_ADDENDUM_v0.1.md` 2.6~2.9절(기간별 자동 분석, MVP 피처, 카드·칩 생성 규칙)과
`docs/reference/lifecycle_domain_v1.txt` 4.3~4.4절(재무비율 위험신호, 생애주기 이벤트 감지). 모델은
`app/models.py`가 단일 진실이다: `SpendingCategory`(식비·카페간식·교통·주거·통신·구독·의료·쇼핑·여가·
교육·보험·이체·대출상환·현금서비스·급여·기타 16종), `CategoryTotal`, `SubscriptionItem`, `AnomalyItem`,
`LifeEventSignal`, `SpendingSummary`, `SpendingFeatures`.

**프라이버시(D3/D4)와 동의(D8)**: addendum 2.2절 P-A(온디바이스)/P-C(수기·요약만) 절충을 PoC 규모로
구현한다.
- 서버는 원본 거래내역을 저장하지 않는다. 브라우저가 파일을 파싱·정규화해 `Transaction` 목록을 만들어
  `POST /api/spending/analyze`로 보내면, 서버는 그 요청을 처리하는 동안만 메모리에서 집계하고 계산된
  `SpendingSummary`/`SpendingFeatures`만 DB에 남긴다(원문 거래는 응답 후 폐기, 어떤 테이블에도 없음).
- 분석은 사용자가 파일을 업로드하거나(`/api/spending/analyze`) 합성 데이터를 명시적으로 선택했을 때만
  (`/api/spending/analyze-synthetic`) 실행된다(자동 실행 없음, 기본 OFF). 이 실행 시각을
  `SpendingFeatures.spending_consent_at`에 기록한다.
- 세션 프로필이 없으면(온보딩 전) 소득 0인 게스트 프로필로 계산한다(404 대신 체험 허용).

app/core/spending.py (순수 함수, I/O 없음, 택소노미는 코드 내장 - config 파일 없음)
- `categorize(merchant: str, amount: int, kind: str) -> SpendingCategory`
  가맹점명 키워드 규칙. 빈 문자열/None은 예외 없이 `기타`. `Transaction.category` 값은 신뢰하지 않고
  merchant 문자열에서 항상 다시 계산한다(업로드마다 원본 표기가 다를 수 있어 규칙을 통일한다).
- `aggregate(transactions, *, end, months=3) -> SpendingSummary`
  `end` 기준 최근 `months`개월을 월별로 버킷팅한다. 이체·급여는 소비 집계에서 제외한다(addendum 2.6:
  TRANSFER_INTERNAL 제외, INCOME 별도 집계). 나머지 14개 카테고리 합이 `total_spend`와 정확히 같다.
  고정(주거·통신·구독·보험·대출상환)/변동(식비·교통·의료·교육·현금서비스)/재량(카페간식·쇼핑·여가·기타)
  세 집합의 합도 `total_spend`와 같다(세 집합이 14개 카테고리를 정확히 분할). 대출상환·현금서비스는
  addendum 원안의 DEBT_SERVICE 분리 원칙을 PoC 범위에서 단순화해 "고정" 소비로 함께 집계한다(상환
  여력 자체는 `app.core.capacity`가 이미 별도로 계산). 구독 = 같은 가맹점이 인접한 두 달 이상 월합계
  ±10% 이내로 반복(건당 단가가 아니라 월 합계를 비교 - 고빈도 변동 가맹점 오탐 방지). 이상치 = 카테고리
  월 지출이 전월 대비 +30% 이상이며 절대 증가액 5만원 이상 동시 충족. 상위 가맹점/구독 표시명은 앞 2자
  + "**"로 마스킹한다. 이 함수는 프로필을 받지 않으므로 `profile_id`(빈 문자열)와 `life_events`(빈
  목록)는 채우지 않는다 - 서비스 레이어가 `detect_life_events` 결과와 함께 채운다.
- `detect_life_events(transactions, profile) -> list[LifeEventSignal]`
  lifecycle_domain_v1.txt 4.4절 프록시. `wedding`(예식장·웨딩·스튜디오·혼수 키워드),
  `childbirth`(산부인과·유아·기저귀·분유), `job_change`/`income_drop`(거래가 있는 마지막 두 달을 비교해
  급여 입금이 0으로 중단되었거나 30% 이상 줄었을 때), `retirement_near`(나이 55세 이상 그리고 소득 변화
  신호 또는 프로필 `retirement_near` 플래그), `refinance_window`(잔여 6개월 이하 대출이 있거나,
  `income_up` 플래그와 함께 금리 연 7% 이상인 신용성 대출이 있을 때). `evidence`는 가맹점 원문 대신
  범주 수준 설명 문자열만 담는다(SPEC D3/D4, 개인신용정보 원문 비노출).
- `compute_features(summary, profile) -> SpendingFeatures`
  결정론(시계 미사용, 같은 입력이면 같은 JSON). `computed_at`은 `summary.period_end`를 쓴다(실행
  시각을 쓰면 재현성이 깨진다). `spending_consent_at`은 이 함수에서는 항상 None(서비스 레이어가 분석
  실행 시각으로 채운다 - D8). addendum 2.7 MVP 피처 중 요약만으로 계산 가능한 `income_monthly_est`,
  `net_cash_flow_monthly`, `data_coverage_days`, `classification_quality`를 추가로 포함한다.
- `build_spending_cards(features, summary, profile) -> list[InsightCard]`
  addendum 2.9 카드 규칙 중 이번 스코프가 지원하는 6개: `IC01` 소비 급증 카테고리, `IC02` 구독 합계,
  `IC03` 고정지출 비율, `IC04` 저축 여력(수입 - 지출 - 상환), `IC05` 생애 이벤트 신호(질문형 카드, 상품
  언급 금지), `IC06` 소득 불규칙(급여 입금이 전혀 관측되지 않으면 "불규칙"과 혼동되지 않도록 억제). 이
  IC01~IC06 번호는 이번 기능 전용 로컬 번호이며 addendum 2.9의 전역 IC01~IC13 카탈로그 번호와는
  다르다. 금칙어(비난·낙인·공포 표현, "~하세요"류 권유형 어미, "추천", em dash) 없음, 상품·회사명 없음.
  각 카드는 `explain`과 chip(intent `spending`/`scenario`, params 포함)을 가진다.
- `taxonomy_info() -> dict` : `GET /api/spending/taxonomy` 응답 본문(카테고리 목록 + 키워드 규칙).

app/services/spending.py
- `analyze(transactions, profile, *, end, months=3, consent_at=None) -> (SpendingSummary, SpendingFeatures)`
  `aggregate` → `detect_life_events` 결과와 `profile.id`를 병합 → `compute_features` → `consent_at`
  (생략 시 호출 시각)을 `spending_consent_at`에 채운다.
- `save(profile_id, summary, features)` / `load(profile_id) -> (summary, features) | None` / `clear(profile_id)`
  `db.spending_features` 테이블에 프로필당 최신 1건만 upsert로 유지한다.
- `load_synthetic(profile_id, months=3, seed=42, end=None) -> list[Transaction]`
  `app.data.synthetic.generate_transactions`에 위임(알 수 없는 페르소나면 `ValueError`, 라우트가 404로 변환).

db.py: 테이블 `spending_features(profile_id PK, features_json, summary_json, updated_at)`.

엔드포인트

| 메서드 | 경로 | 응답 | 비고 |
|---|---|---|---|
| POST | /api/spending/analyze | `{summary, features, cards}` | body `{transactions: list[Transaction], months?: int}`. 원본 거래내역 미저장(D3/D4) |
| POST | /api/spending/analyze-synthetic | `{summary, features, cards}` | body `{persona_id?, months?, seed?}`. persona_id 생략 시 현재 세션 프로필 id 사용 |
| GET | /api/spending | `{summary, features, cards}` 또는 404 | 저장된 최신 분석 결과 |
| DELETE | /api/spending | `{ok}` | 저장된 분석 결과 삭제 |
| GET | /api/spending/taxonomy | `{categories: list[str], rules: dict[str, list[str]]}` | 화면이 분류 기준을 보여줄 때 사용 |

`insights.build_home` 연동: 저장된 소비 패턴 분석이 있으면 `build_spending_cards` 결과 중 최대 2장을
홈 카드에 더한다. 홈 카드는 최대 3장(SPEC 3장)이므로 자리가 모자라면 progress 카드부터 제거해 자리를
만든다. Tier 1 칩 "소비 패턴 보기"(intent=spending)를 추가한다(칩은 기존과 같이 최대 5개 유지).
`llm_calls`는 항상 0(이 절 전체가 LLM을 호출하지 않는다).

### 2.7 생애주기 층 (P7)

배경: `docs/DONN_LIFECYCLE_PLAN.md`(반영 계획)와 `docs/reference/lifecycle_domain_v1.txt`(PMO
도메인 지식 문서, 1.5~1.6·2.1~2.5·3.3~3.13·4.3절). 부채 코어 위에 재무비율·생애주기 단계·
노후자금 시뮬레이션 층을 얹는다. 문서의 "상품 추천"은 전부 "참고 시나리오·행동 카드"로
바꾼다. 자산·연금 수치는 G3 등급이며 외부 LLM으로 보내지 않는다.

모델(`app/models.py`, append): `PensionAssets{national_pension_months_paid, db_dc_balance,
irp_pension_savings_balance, isa_balance, expected_national_pension_monthly}`,
`Assets{liquid, investment, pension: PensionAssets, real_estate}`,
`Goal{id, kind, label, target_amount, target_date, priority, saved_amount,
monthly_income_change_pct}`(kind: wedding/childbirth/housing/education/retirement/
emergency/other), `LifeStage` str-enum 7종(early_career/family_formation/asset_building/
pre_retirement/retirement_transition/active_retirement/late_retirement, 한글 라벨은
`LIFE_STAGE_LABELS_KR`), `FinancialRatios{liquidity_months, saving_rate, debt_ratio,
debt_service_ratio, investment_ratio, total_assets, net_worth, interpretations,
thresholds, flags}`(flags 값은 ratio별 "ok"|"warn"|"na"), `LifeStageResult{stage, label,
reasons, priorities, avoid, accounts_note}`, `RetirementProjection{scenario, real_return,
inflation, years_to_retirement, retirement_age, retirement_living_cost,
guaranteed_income_monthly, monthly_gap, required_fund_pv, projected_fund_fv, shortfall,
required_monthly_saving, assumptions}`, `LifecycleView{profile_id, ratios, stage,
retirement, income_gap_map, net_worth_path, goals, assumptions, disclaimer}`(disclaimer
기본값은 `LIFECYCLE_DISCLAIMER` = "참고 시나리오이며 특정 상품이나 자산 배분을 권하지
않습니다."). `UserProfile`에 `assets`, `goals`, `dependents`, `risk_tolerance`,
`income_type`, `life_stage_override`, `retirement_age`,
`target_retirement_monthly_expense`(전부 선택, 기본값 있음)를 추가했다.

`config/thresholds.yaml`: 생애 단계 7개 키(LifeStage 값)마다 `min_liquidity_months`,
`min_saving_rate`, `max_debt_service_ratio`(모든 단계 0.40), `min_coverage_ratio`(50대
이상 단계만 0.60), `needs_verification`, `note`. 문서 4.3절 예시값이라 전부
`needs_verification: true`. I/O 리더는 `app.services.lifecycle.load_thresholds()`, 파싱은 순수
함수 `app.core.lifecycle.parse_thresholds(data)`가 맡는다(SPEC 원칙 7: core는 I/O 금지).

`app/core/ratios.py`: `compute_ratios(profile, schedules, thresholds_for_stage) ->
FinancialRatios`(문서 1.5/5.2절). 저축률은 `(연소득 - 연지출 - 연원리금상환) / 연소득`으로
`capacity.py`의 net_monthly 개념과 맞춘다. 분모가 0이거나 `profile.assets`가 없으면 해당
비율은 None이고 flag는 "na". 임계값이 있고 계산 가능하면 "ok"/"warn", 없으면 "ok".

`app/core/retirement.py`(순수 함수, Decimal 내부 계산 후 정수 원 반올림): `fv_lump`,
`pv_lump`, `real_rate`, `real_value`, `retirement_living_cost`, `annuity_pv`,
`fv_monthly_saving`, `required_monthly_saving`, `coverage_ratio`, `withdrawal_rate`,
`rebalance_amounts(total, target_weights, current_amounts) -> dict[str,int]`(값은
목표금액-현재금액 이동량), `national_pension_estimate(a_value, b_value, months_paid,
months_after_2026=None, payout_rate_rule=None) -> int`(문서 3.3절 산식의 교육용 추정,
연액을 12로 나눠 월액화), `retirement_gap_projection(profile, params, *, today,
scenarios=None) -> list[RetirementProjection]`(기본 시나리오는
`DEFAULT_RETIREMENT_SCENARIOS` = 낙관 5%/기준 3%/비관 1% 실질수익률, 물가 2%; 인출 종료
연령은 `LATE_LIFE_END_AGE`=90 내부 가정; 적립 예상액은 연금성 자산(db_dc+irp+isa)과 현재
저축여력(capacity.net_monthly)의 은퇴 시점까지 합). 모든 골든 벡터는
`tests/test_core_retirement.py` 참고(허용 오차 ±0.5%).

`app/core/lifecycle.py`(순수 함수): `parse_thresholds`, `thresholds_for_stage`,
`classify_stage(profile, *, today) -> LifeStageResult`(나이 구간 기본값을 부양가족·2년 이내
결혼/출산 목표·`retirement_near` 플래그·무소득+55세 이상·`life_stage_override` 순으로
보정, 근거 문장을 `reasons`에 누적), `stage_priorities(stage)`(문서 2.3절 표, 계좌 역할은
제도 일반론 문구만), `glide_path_reference(age)`(문서 2.4절 표를 선형보간한 참고 모델,
자산배분 권고 아님), `income_gap_map(profile, params, *, today)`(50세 미만이면 None, 이상이면
문서 2.5절 4구간).

`app/core/rules.py`: R4(비상자금, id 유지)는 `thresholds`가 있으면
`min_liquidity_months`를, 없으면 기존 `policy.emergency_fund_months`를 쓴다(하위 호환).
신규 R8(저축률 미달, priority 55), R9(원리금상환비율 초과, priority 25, chip "내 조건으로
공시 비교" intent compare), R10(50세 이상 노후소득 충당률 미달, priority 35, chip
"노후자금 시뮬레이션 보기" intent lifecycle)은 `thresholds`가 없으면 평가하지 않는다(하위
호환). `evaluate_rules`에 키워드 전용 `thresholds: dict | None = None`을 추가했다. R0
안전모드는 기존처럼 R2·R3를 억제하고, R9도 같은 이유로 억제한다(R4·R8·R10은 정보성 안내라
안전모드에서도 유지).

`app/core/scenarios.py`: `run_scenarios`에 키워드 전용 `today`, `goals`를 추가했다(둘 다
없으면 기존과 동일하게 동작, 하위 호환). 목표의 `target_date`가 `today` 기준 시야 안의
달력월에 들면 그 달에 `target_amount - saved_amount`를 일시 지출로, 그 달부터
`monthly_income_change_pct`를 소득에 반영한다. `run_lifecycle_projection(profile, params,
*, today, until_age, scenario_returns) -> list[dict]`는 시나리오별 연 단위
{scenario, age, year, debt_balance, liquid_assets, investment_assets, pension_assets,
net_worth} 행을 만든다(자산은 시나리오 실질수익률로 증식, 부채는 현재 연간
원리금상환액만큼 선형 근사로 감소).

`app/services/lifecycle.py`: `build_lifecycle_view(profile, params, thresholds, *, today)
-> LifecycleView`가 위 core 함수를 조합한다. `GET /api/lifecycle`(응답
`LifecycleView`, 프로필 없으면 404)이 이를 호출한다. `assumptions`는 단계 판정 근거,
사용한 임계값 출처와 확인 필요 여부, 시나리오별 가정, 글라이드패스 고지, 순자산 경로
근사 설명을 담는다.

`insights.build_home`: 목표가 있으면 목표 진행률 카드(kind="progress", "OO 목표 진행률"
제목에 "목표 금액의 N% 확보" 프레이밍, chip intent lifecycle)를 만든다. 카드 총량 상한
3장은 top_action·debt 카드를 우선 채운 뒤 남는 자리에 목표 카드 → 대출 진행 카드 순으로
채워 지킨다(소비 패턴 카드의 기존 자리 확보 로직과 동일하게 "progress" kind로 취급).

채팅 의도(`app.llm.guardrails.parse_message`와 `app/api/routes.py`의 Gemini 추출
스키마·정규화 집합에 추가): `retirement`(노후·연금·은퇴 키워드), `saving`(저축률·저축·
자동이체), `liquidity`(비상금·비상자금). 세 의도 모두 프로필이 있으면
`app.core.ratios`/`app.core.retirement`가 계산한 실제 수치가 든 문장으로 답하고
`action: {"type":"open_view","payload":{"view":"lifecycle"}}`을 반환한다.

KB 문서 3편 추가(`kb/national-pension-estimate.md`, `kb/retirement-pension-db-dc-irp.md`,
`kb/pension-savings-isa-tax.md`, category `pension`/`tax`, 기존 12편과 같은 프런트매터·
6섹션 형식): 국민연금 예상연금 확인과 산식, 퇴직연금 DB·DC·IRP 개요, 연금저축·ISA 세제
개요. 수치가 공식 1차 출처로 확인되지 않은 항목은 본문에 "(확인 필요)"를 표시하고 해당
문서의 `needs_verification: true`를 유지한다. `tests/test_kb.py`의 기대 문서 수는
12에서 15로 늘었다.

`config/policy_params.yaml`에 연금·세제 수치를 추가했다(전부 `needs_verification: true`,
출처는 nps.or.kr/nts.go.kr/law.go.kr): `national_pension_a_value`(2026년 A값 근사,
`national_pension_estimate`가 직접 읽는다), `national_pension_premium_rate_pct`,
`national_pension_income_replacement_rate_pct`, `pension_tax_credit_limit_combined_krw`,
`pension_tax_credit_limit_pension_savings_only_krw`, `pension_tax_credit_rate_standard_pct`,
`pension_tax_credit_rate_high_income_pct`, `pension_tax_credit_income_threshold_krw`,
`isa_annual_limit_krw`, `isa_total_limit_krw`, `isa_mandatory_years`,
`isa_nontax_limit_general_krw`, `isa_nontax_limit_special_krw`,
`isa_separate_tax_rate_pct`.

### 2.8 설명 문장 (슬롯 필링, (f) 단계)

원칙: LLM은 숫자를 보지도 쓰지도 않는다. 코드가 범주형 사실(facts)과 플레이스홀더 목록만 보내고, LLM은 `{amount}` 같은 플레이스홀더가 든 문장을 돌려주며, 코드가 검증한 뒤 숫자를 채운다. 검증에 실패하거나 LLM이 불가하면 템플릿 문장을 쓴다. 첫 화면 로드에서는 호출하지 않는다(사용자가 비교를 실행했거나 "AI 설명 보기"를 눌렀을 때만).

- `app/llm/slotfill.py` (순수 문자열 처리, app.data/app.models 비의존)
  - `PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z_]*)\}")`. 플레이스홀더 이름은 소문자와 밑줄만(숫자 없음). 순위는 항목 라벨 글자를 따라 `_a`, `_b`, `_c` 접미사를 쓴다(`rate_a` = 첫 번째 항목 금리).
  - `assert_no_digits(payload: Any) -> None`: 키, 값, 중첩 구조 어디에든 숫자 문자(0~9)가 있으면 `ValueError`. LLM으로 보내기 직전에 반드시 호출한다(D4 하드 불변식). 실패하면 호출하지 않고 템플릿으로 간다.
  - `sanitize(text: str) -> str`: 앞뒤 공백 정리, 연속 공백 축약, em dash(—)와 en dash(–)를 공백으로 치환, 마크다운 기호(`*`, 백틱, 줄머리 `-`/`•`) 제거.
  - `validate(text: str, allowed: set[str], banned: list[str], *, max_chars: int, max_sentences: int) -> list[str]`: 문제 코드 목록(빈 리스트면 통과). 코드: `empty`, `unknown_placeholder:<name>`, `digit_outside_placeholder`, `banned_term:<term>`, `forbidden_phrase:<phrase>`, `too_long`, `too_many_sentences`. 플레이스홀더를 제거한 나머지 문자열에 숫자가 하나라도 있으면 `digit_outside_placeholder`.
  - `FORBIDDEN_PHRASES = ("추천", "가입하세요", "가입을 권", "갈아타세요", "권합니다", "권해드", "권장", "보장", "무조건", "최고의", "최선의", "가장 좋은", "가장 유리")`.
  - `fill(text: str, values: dict[str, str]) -> str`: 플레이스홀더를 값으로 치환. 값이 없는 플레이스홀더가 남으면 `KeyError`.
  - `josa(word: str, pair: str) -> str`: 받침 유무로 "은/는", "이/가", "을/를"을 고른다(템플릿용).
- `app/services/explain.py`
  - `PROMPT_VERSION = "explain-v1"`, `EXPLAIN_SYSTEM`(한국어 시스템 프롬프트: 숫자 금지와 플레이스홀더 필수, 상품명·회사명 금지, 권유 표현 금지, 입력에 없는 사실(우대조건·한도·심사·자격) 금지, 해요체, 요약 2~3문장·항목 이유 1문장, 특수 기호 금지).
  - `COMPARE_SCHEMA = {"type":"OBJECT","properties":{"summary":{"type":"STRING"},"reasons":{"type":"ARRAY","items":{"type":"STRING"}}},"required":["summary","reasons"]}`, `ACTION_SCHEMA = {"type":"OBJECT","properties":{"summary":{"type":"STRING"}},"required":["summary"]}`.
  - `compare_slots(result: CompareResult, profile: UserProfile | None) -> tuple[dict, dict, dict]` = (facts, placeholders, values). 상위 3개 항목만. facts는 전부 숫자 없는 문자열: category 라벨, sort_basis, has_current_loan(예/아니오), current_loan_type 라벨, estimated_fields 한글 라벨 목록, items[{label, lender_group, rate_kind 라벨, vs_current("현재보다 총이자 절감" | "현재보다 총이자 증가" | "현재 대출과 같음" | "비교 기준 없음"), notes}]. placeholders는 이름→설명(숫자 없는 한국어): amount, term_months, candidates_total, shown_count, label_a/b/c, rate_a/b/c, monthly_a/b/c, total_a/b/c, vs_a/b/c(vs_current가 없으면 제외). values는 코드가 포맷한 문자열: 금액 `f"{n:,}원"`, 금리 `f"{r:.4g}%"`, 기간 `f"{m}개월"`, 개수 `f"{n}개"`, vs는 절대값 금액.
  - `action_slots(card: ActionCard, profile: UserProfile, capacity: Capacity) -> tuple[dict, dict, dict]`: facts = rule_id, title, gist(`RULE_GIST[rule_id]`, 숫자 없는 규칙 요지 문장), capacity_band 라벨(여유/보통/빠듯/부족), safe_mode(예/아니오), loan_type 라벨(관련 대출 첫 건). placeholders/values는 `card.numbers`의 원시 키(영문)를 이름으로 쓰고, 설명은 `app/services/actions.py`의 라벨 맵, 값은 같은 포맷터(`format_action_number(key, value) -> str` 신설, `format_action_numbers`가 이를 재사용)로 만든다. steps/assumptions/caveats(숫자 포함)는 보내지 않는다.
  - `explain_compare(decision_id: str, provider, *, refresh: bool = False) -> ExplainResult | None`: `decisions.get`이 없으면 None. 저장된 설명이 있고 refresh가 아니면 `cached=True`로 반환. 아니면 `provider.explain(slots, template_id, EXPLAIN_SYSTEM, schema=COMPARE_SCHEMA)` → sanitize → validate(summary 300자·3문장, reason 140자·1문장) → fill. 하나라도 실패하면 전체를 템플릿으로(부분 혼합 없음) 가고 `problems`에 코드를 남긴다. `explanations` 테이블에 저장.
  - `explain_action(action_id: str, provider, profile: UserProfile, params: PolicyParams, *, today: date, refresh: bool = False) -> ExplainResult | None`: `evaluate_rules` 원시 카드에서 id로 찾는다(없으면 None). ref_id = `f"{profile.id}:{action_id}@{hashing.fingerprint(card.numbers)[:8]}"`. 템플릿 폴백은 `card.summary` 그대로.
  - `get_stored(kind: str, ref_id: str) -> ExplainResult | None`.
  - 템플릿(`compare_summary_v1`): "{category} 공시 상품 {candidates_total} 중 {sort_basis}으로 상위 {shown_count}를 골랐어요. {label_a}은(는) 금리 {rate_a}, 월 납입 {monthly_a}, 총이자 {total_a}로 첫 번째예요." 뒤에 vs_a가 있으면 방향에 따라 "현재 대출보다 총이자를 {vs_a} 줄일 수 있는 조건이에요." 또는 "현재 대출보다 총이자가 {vs_a} 더 들어요."를, 추정 필드가 있으면 "금액과 기간은 프로필에서 추정한 값이라 조건 확인에서 바꿀 수 있어요."를 붙인다. 항목 이유 템플릿: "{sort_basis} 기준 {ordinal}이에요. 금리 {rate_x}({rate_kind}), 월 납입 {monthly_x}, 총이자 {total_x}." 뒤에 vs 문장.
  - provider에 `explain`이 없거나 `available()`이 False면 LLM을 호출하지 않고 템플릿(`llm_used=False`, `source="template"`, `problems=["llm_unavailable"]`).
- `app/llm/provider.py`: `explain(slots: dict, template_id: str, system: str, schema: dict | None = None) -> LLMResult`. schema가 있으면 JSON 모드(`responseMimeType`/`responseSchema`)로 호출하고 `data`를 채운다. `config/llm.yaml`의 `explain_total_deadline_seconds`(기본 15)를 이 호출의 체인 상한으로 쓴다(`_run_chain(body, deadline_seconds=...)`).
- `app/data/db.py`: 테이블 `explanations(kind TEXT NOT NULL, ref_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(kind, ref_id))`. payload_json은 `ExplainResult.model_dump(mode="json")`.
- 결정 기록, `result_hash`, replay는 바뀌지 않는다(설명은 별도 테이블이며 `CompareItem`/`CompareResult`에 필드를 추가하지 않는다).
- 로그: 모델명, 키 인덱스, 지연, 검증 문제 코드만 남긴다. 사용자 발화와 프로필 수치는 이 경로에 등장하지 않는다.

### 2.9 대화 화면 스트리밍과 생각 과정 (P4b)

목적: 사용자가 메시지를 보내면 대화 화면으로 전환되고, 응답이 만들어지는 동안 파이프라인 단계("생각 과정")가 실시간으로 보이며, 응답 문장이 순서대로 나타난다. 생각 과정은 LLM의 내부 사고가 아니라 코드 파이프라인의 실제 단계·소요 시간·폴백 여부다(정직성 원칙: 없는 사고를 꾸며 보여주지 않는다).

- `POST /api/chat/stream` body `ChatRequest{message, chat_id?}` → `text/event-stream`(Server-Sent Events). 프레임은 `event: <name>
data: <json 한 줄>

`.
  - `stage`: `{"id": "guard"|"intent"|"compute"|"explain"|"check", "label": str, "status": "start"|"done"|"fallback"|"skip", "detail": str, "ms": int}`. 같은 id로 `start` 뒤에 `done`/`fallback`/`skip`이 한 번 온다.
    - `guard` "발화 점검": PII 마스킹과 위기 신호 확인. detail 예 "개인정보 마스킹 완료", "위기 신호 감지: 공적 상담 안내로 전환".
    - `intent` "의도·조건 추출": done detail "Gemini {model}, {ms}ms, 의도: {의도 라벨}"; fallback detail "규칙 파서로 대체(LLM 응답 없음)".
    - `compute` "계산 엔진": 의도별 detail. compare "비교 조건 준비: {카테고리}, 금액 {amount}, 기간 {term}(추정 {n}개)" / 후속 질의 "이전 조건에서 {바뀐 항목}만 변경" / action "행동 규칙 평가: {n}건, 최우선 {카드 제목}" / faq "제도 안내 검색: {title}" 또는 "해당 문서 없음" / retirement·saving·liquidity "재무비율·노후자금 계산" / schedule·scenario·spending "화면 안내". detail에는 코드가 계산한 숫자가 들어가도 된다(사용자 화면용이며 LLM으로 가지 않는다).
    - `explain` "설명 작성": done detail "Gemini {model}, {ms}ms"; fallback detail "템플릿 문장 사용({problems 첫 코드})"; skip detail "규칙 문장"(설명 생성을 쓰지 않는 의도).
    - `check` "응답 점검": done detail "상품명·회사명·권유 표현 없음 확인"; fallback detail "금칙어 감지로 기본 안내로 대체".
  - `reply`: `ChatReply` JSON(`/api/chat`과 동일, `chat_id` 포함) + `trace`(종료 상태 stage 목록, 순서대로) + `model`(설명 또는 추출에 실제 응답한 모델명, 없으면 null).
  - `error`: `{"message": str}` 뒤 스트림 종료.
- 구현: `_build_chat_reply(message, base_params=None, emit=None)`에 stage 콜백을 추가한다. 스트림 엔드포인트는 이 함수를 워커 스레드에서 실행하고 `queue.Queue`로 stage 이벤트를 받아 즉시 내보낸 뒤, 대화 로그 저장(`/api/chat`과 동일)을 마치고 `reply`를 보낸다. `POST /api/chat`은 그대로 두되 응답 `ChatReply`에 `trace`와 `model`이 추가로 실린다.
- 대화 로그: `chat_messages.trace_json TEXT`(없으면 `init_db`가 `PRAGMA table_info`로 확인해 `ALTER TABLE ... ADD COLUMN`). `GET /api/chats/{id}/messages`의 각 응답 메시지에 `trace` 배열이 실린다.
- 설명 생성 연결(2.8 재사용):
  - `compare` 의도(예·적금 제외): 조건 준비 뒤 `explain.explain_chat_compare(ctx, followup_changed, provider)`로 안내 문장을 만든다. template_id `chat_compare_prep_v1`, facts = 카테고리 라벨, 추정 필드 라벨 목록, 후속 질의로 바뀐 항목 라벨 목록, 금리 상한 유무("예"/"아니오"), 제외 회사 유무. placeholders = amount, term_months, max_rate(있을 때만). 출력 스키마 `{"summary": STRING}`, 검증은 220자·2문장. 폴백은 기존 템플릿 문장. 이 호출의 체인 상한은 `config/llm.yaml`의 `chat_explain_deadline_seconds`(기본 8). 설명 문장 뒤에 "아래 버튼으로 조건을 확인하고 실행해보세요." 같은 유도 문장은 코드가 붙인다(LLM은 화면 구조를 모른다). `explanations` 테이블에는 저장하지 않는다(대화 로그가 기록).
  - `action` 의도(최우선 카드가 있을 때): `explain.explain_action(top.id, provider, profile, params, today=...)`의 `summary`를 응답 문장으로 쓴다(저장·캐시 규칙은 2.8과 같다). LLM 실패 시 `card.summary`.
  - 그 밖의 의도는 `explain` 단계 skip.
  - `ChatReply.llm_used`는 추출 또는 설명 중 하나라도 LLM이 성공했으면 True.
- `LLMProvider.explain`에 선택 인자 `deadline_seconds: float | None = None`을 둔다(없으면 `explain_total_deadline_seconds`).
- D4: 이 경로에서 LLM으로 가는 것은 마스킹된 발화(추출)와 숫자 없는 facts/placeholders(설명)뿐이다.

### 2.10 배포 준비와 브라우저 전용 모드(보류)

**상태(2026-09-06 PMO 결정)**: 서버 배포는 Render(`render.yaml`)로 진행한다. 이 절의 브라우저 전용 모드(Pyodide 브리지, 정적 번들, Pages 워크플로)는 보류하며, 시드 데이터·자동 적재·CORS·PORT 항목만 유효하다. `app/bridge.py`, `web/static-mode.js`, `.github/workflows/pages.yml`, `scripts/build_static_bundle.py`는 만들지 않는다.

목적(보류된 설계): 서버 없이 GitHub Pages 주소에서 앱 전체를 실행한다. 방문자의 브라우저 안에서 Pyodide(파이썬 WebAssembly)가 `app/` 패키지를 그대로 실행하고, 화면은 같은 `web/`를 쓴다. 서버가 없으므로 세션은 브라우저마다 독립이고 키는 어디에도 저장되지 않는다(방문자가 원하면 설정에서 Gemini 키를 넣고, 키는 그 브라우저의 localStorage에만 남는다. 없으면 규칙 기반 모드).

- 데이터: `seed/products_seed.json.gz`(공시 상품 스냅샷, 공개 데이터만. 프로필·결정·대화는 절대 포함하지 않는다). `scripts/export_seed.py`가 현재 DB에서 각 카테고리 조회(`products.query`)에 실제로 쓰이는 최신 스냅샷(소스별)만 골라 만든다. `app/data/products.py::import_seed(path) -> dict`(snapshots·products에 INSERT OR IGNORE, 건수 반환), `ensure_seed_loaded(path="seed/products_seed.json.gz") -> bool`(products 테이블이 비어 있을 때만 적재). `app/main.py` lifespan은 `DONN_SEED_ON_EMPTY`(기본 "1")면 시작 시 이를 호출하고, `DONN_AUTOLOAD_PRODUCTS=1`이고 금감원·공공데이터 키가 있으면 백그라운드 스레드로 실 적재(`scripts.load_products.load_snapshot`)를 추가로 돌린다. `DONN_CORS_ORIGINS`(쉼표 구분)가 있으면 CORSMiddleware를 붙인다. `run.py`는 `DONN_PORT`가 없으면 `PORT`(PaaS 관례)를 본다.
- 브리지: `app/bridge.py::dispatch(method, path, query=None, body=None) -> {"status": int, "body": str, "content_type": str}`. `app.api.routes.router.routes`를 순회해 각 `APIRoute`의 `path_regex`로 경로를 맞추고 엔드포인트 함수를 직접 호출한다(FastAPI/Starlette 실행 계층과 스레드풀 없음). 경로 인자와 쿼리는 시그니처 애너테이션(`Query` 기본값 포함)으로 변환하고, pydantic 모델 인자는 `model_validate_json(body)`(선택 인자면 body 없을 때 None)로 만든다. `HTTPException` → status/detail, `ValidationError` → 422(FastAPI와 같은 `{"detail": [...]}`), pydantic 모델 → `model_dump_json`, list/dict → json, `Response` → body/media_type. 모든 라우트가 브리지로 호출 가능해야 하며 `tests/test_bridge.py`가 라우트 커버리지(모든 APIRoute가 매칭됨)와 TestClient 응답 동일성(JSON 엔드포인트 표본)을 검사한다. 대화 스트리밍은 `bridge.chat_with_trace(body_json, emit) -> str`가 `routes.run_chat(body, emit=emit)`(2.9)을 직접 호출한다(SSE·스레드 없음). `run_chat`이 아직 없으면 `post_chat`으로 대신한다.
- 브라우저: `web/static-mode.js`(웹 워커에서 Pyodide 0.29 로드, 번들 압축 해제, 패키지 `pydantic`·`requests`·`pyyaml`·`fastapi` 로드, `python-dotenv`는 `sys.modules` 스텁, `DONN_DB_PATH=/tmp/donn.db`, 시드 적재, `dispatch`·`chat_with_trace` 호출을 postMessage로 중계). `web/app.js`의 `apiGet/apiSend`와 대화 스트림 클라이언트는 정적 모드면 워커로 보낸다. 정적 모드 판정: `window.DONN_STATIC === true`(index.html 스크립트가 github.io 호스트이거나 `?static=1`일 때 설정). 로딩 오버레이("브라우저 안에서 계산 엔진을 준비하는 중", 단계별 진행), 설정 모달의 Gemini 키 입력(localStorage 저장, 워커에 전달해 provider 재생성), 상단 배지("브라우저 실행 모드").
- 배포: `.github/workflows/pages.yml`이 main push마다 `scripts/build_static_bundle.py`로 `app/`, `config/`, `kb/`, `seed/`를 `donn_bundle.zip`으로 묶어 `web/` 내용과 함께 Pages 아티팩트로 올린다(Pages 소스: GitHub Actions). 앱 소스는 번들에만 들어가고 키는 어디에도 없다.
- 제약: 첫 로드 10~20초(이후 브라우저 캐시), 새로고침하면 세션·대화가 초기화된다(IndexedDB 영속화는 후속), Gemini 호출은 방문자 브라우저에서 직접 나간다(요청 본문은 서버 모드와 동일: 마스킹된 발화와 숫자 없는 facts).

### 2.11 답변 경로(direct/internal/external), 노드 카드, 리소스 패널 (P4b-2)

목적(PMO 2026-09-06, Ai365 Unified 워크스페이스 화면 참조): 채팅 답변이 KB 본문을 그대로 쏟아내지 않고, (1) 질문을 세 경로 중 하나로 보내고 (2) 어떤 노드가 무엇을 했는지 카드로 보여주며 (3) 답변에 쓰인 자료를 오른쪽 "리소스" 패널에 모아 보여주고 (4) 답변은 제목·굵게·목록이 있는 읽기 좋은 형식으로 낸다.

- 경로(코드가 결정, `ChatReply.route`):
  - `safety`: 위기 발화(기존 2.9 분기).
  - `direct`: 자료가 필요 없는 질문(인사, 할 수 있는 일, 용어의 일반 정의 중 KB에 없는 것). LLM이 있으면 짧게 직접 답하고(개인 수치·상품명·회사명 금지, 금칙어·권유 표현 검사), 없으면 고정 안내. 의도 스키마에 `direct`를 추가한다.
  - `internal`: 기존 의도(compare, schedule, scenario, action, spending, retirement, saving, liquidity, faq에 KB 문서가 있을 때). 내부 노드가 자료를 모아 답한다.
  - `external`: faq인데 KB에 맞는 문서가 없거나(검색 점수 임계값 미만) "최신·요즘·지금 금리·뉴스"처럼 시점성 질문일 때. Gemini의 Google 검색 그라운딩(`tools: [{"google_search": {}}]`)으로 답하고 `groundingMetadata`의 웹 출처(title, uri)를 리소스로 붙인다. 요약문은 "외부 검색 요약(확인 필요)" 배지와 함께 보여주고, 금칙어(상품명·회사명)가 검출되면 요약을 버리고 출처 링크만 보여준다. Google 검색 제안(`searchEntryPoint.renderedContent`)이 오면 화면에 그대로 표시한다(이용약관 요건). 그라운딩이 불가능(키·모델·한도)하면 `kb/*.md` frontmatter의 공식 기관 링크 목록을 "외부 안내"로 대신 보여준다.
- 노드 카드(`ChatReply.trace` 항목 확장, 2.9의 stage와 같은 dict): `id`는 guard, intent, direct, debt_data(내 부채 자료), calc(계산 엔진), kb(제도 안내), products(공시 자료), external(외부 검색), explain(설명 작성), check. 추가 키 `steps: list[str]`(사람이 읽는 단계 문장, 예 "대출 4건의 상환표를 계산했어요", "제도 문서 2편에서 3개 문단을 참조했어요")와 `resource_refs: list[str]`(`ChatResource.ref`). 화면은 이 항목들을 Ai365의 에이전트 카드처럼 "사용한 노드 N개" 묶음으로 그리고, 진행 중에는 실시간으로 채운다.
- 리소스(`ChatReply.resources: list[ChatResource]`): kind는 profile(프로필), loan(대출 1건), calc(상환표·시나리오·비교 결과·행동 카드, ref=decision_id 등), kb(문서, ref=slug#section, verified_at, url=출처), products(공시 스냅샷, detail "2026년 8월 공시, 후보 78건"), policy(규제 기준값, needs_verification), external(웹 출처). 화면 오른쪽 패널에 kind별로 묶어 개수와 함께 표시하고, 클릭하면 해당 화면·패널(내 부채, 공시 비교 결과, 제도 안내 패널, 외부 링크 새 창)을 연다.
- KB 답변 형식(faq internal): `answer_format="markdown"`. 구조는 "한 줄 요약" → "핵심 3개(문서 섹션당 1문장, 굵은 소제목)" → "출처(문서명, 섹션, 확인일)". LLM이 있으면 참조 문단만 입력으로 넣어 요약·핵심을 쓰게 하고(공개 제도 문서이므로 D4 대상 아님), 답변에 나온 모든 숫자가 참조 문단 안에 실제로 있는지 검사한다(없으면 규칙 렌더링으로 폴백). 규칙 모드에서는 섹션 제목과 첫 문장으로 같은 구조를 만든다. 본문 전체를 붙여 넣지 않는다.
- 여러 노드가 필요한 질문(예 "내 상황에서 금리인하요구권 쓸 수 있어?")은 주 의도 노드(debt_data+calc)와 kb 노드를 함께 실행하고 리소스를 합친다.
- 러너 골든 질문에 direct/external 사례를 추가하고, external은 네트워크 테스트(`DONN_NETWORK_TESTS=1`)로만 실호출한다. 단위 테스트는 그라운딩 응답을 모킹한다.

## 3. 화면 규격 (web/)

- 단일 페이지, 빌드 없음. `index.html`, `app.js`, `styles.css`. 글꼴은 Pretendard(jsdelivr CDN, 오프라인이면 system-ui·"Malgun Gothic" 폴백). 그 외 외부 CDN 의존 없음.
- 레이아웃: 상단 바(좌: 로고 "DONN"과 작은 부제, 우: 원형 아바타 "나"), 좌측 사이드바 250px(배경 #FAFAFA, 우측 1px #E5E7EB, 접기 버튼), 본문 흰색 중앙 정렬 최대 폭 960px.
- 색: 포인트 #4F46E5, 포인트 연한 배경 #EEF2FF, 텍스트 #111827, 보조 텍스트 #6B7280, 경계 #E5E7EB, 긍정 #059669, 부정 #DC2626, 중립 #6B7280. 카드 radius 16px, 그림자 `0 1px 3px rgba(0,0,0,.06)`. 전송 버튼은 원형 포인트색.
- 사이드바(2026-09-06 PMO 수정): 맨 위 "계정 선택 (PoC)" 그룹에 현재 페르소나(아바타 이니셜 + 이름 + "이 계정으로 보는 중")와 "계정 바꾸기" 링크(페르소나 화면). 그 아래 `[+ 새 대화]`; "리소스" 그룹: 내 부채, 공시 비교, 소비 패턴, 결정 기록; "고정" 그룹: 이번 달 행동; "최근" 그룹: 현재 페르소나의 대화 목록(`/api/chats`, 클릭 시 대화 로그 로드); 하단: 환경설정. 상단 바 우측 아바타는 현재 페르소나 이름의 첫 글자.
- 홈 하단의 원형 아이콘 4개 줄은 제거(PMO 결정). 모든 하위 화면과 비교 2단계에는 "뒤로" 버튼.
- 로고: DONN 전용 마크(인라인 SVG)와 워드마크. 글꼴: Pretendard(시스템 폴백). 부제는 PMO 확정 문구 사용.
- 홈(C안): 중앙 큰 제목 "DONN"과 한 줄 부제 "빚의 다음 한 걸음을 숫자로". 아래 인사이트 카드 1~3장(가로 카드, 톤별 좌측 색 바, 제목, 본문, 근거 수치 배지, 질문 버튼(칩), "이 분석은 왜 나왔나요?" 토글). 그 아래 입력 박스(둥근 카드, 좌측 + 버튼, "모드: M0 공시 비교" 배지, 우측 원형 전송 버튼, placeholder "어떤 부채 고민을 도와드릴까요?"). 그 아래 칩 3~5개. 그 아래 원형 아이콘 4개(내 부채, 공시 비교, 소비 패턴, 페르소나). 화면 하단 고정 면책 1줄과 AI 고지 1줄(작은 글씨, 항상 보임).
- 내 부채: 프로필 요약과 편집(월소득, 고정지출, 변동지출, 비상금, 신용 구간, 플래그 체크박스), 대출 목록 표, 추가/수정 폼(필수 5개: 종류, 잔액, 금리, 남은 개월, 상환방식. 나머지는 접힘), 대출 선택 시 상환표(첫 12행과 합계)와 시나리오 3종 요약 표(총이자, 부채 완료 월, 최소 누적 순현금).
- 공시 비교: 1단계 조건 확인 카드(추정 필드에 "추정" 배지, 수정 가능, 버튼 "이 조건으로 비교" → user_confirmed=true) → 2단계 결과 블록(정렬 기준 문장, 항목 카드: 순위, 익명 라벨, 금리와 종류 배지("전월 평균" 등), 월 납입, 총이자, 현재 대비 차이, 링크 "금융상품한눈에에서 확인", 유의 문구; 가정 목록; decision_id와 result_hash 작은 글씨; 버튼 "조건 바꿔서 다시 보기"). 실명 표시 금지.
- 소비 패턴: P5 전까지 "준비 중" 카드와 합성 CSV 다운로드 버튼.
- 페르소나: 8명 카드(이름, 한 줄, 부채 수, 총잔액) 버튼 "이 페르소나로 보기".
- 결정 기록: 목록과 버튼 "재현" → 일치 여부 표시.
- 채팅 입력: `POST /api/chat` → 응답 텍스트, 칩, action(`open_view`, `prepare_compare`) 처리. LLM 미연결 시 "규칙 기반 응답" 배지.
- 접근성: 버튼 aria-label, Enter로 전송. 375px에서 사이드바 접힘.

- 설명 문장(2.8): 공시 비교 2단계는 결과(숫자)를 먼저 그린 뒤 `POST /api/compare/{decision_id}/explain`을 호출해 "왜 이 순서인가요?" 블록을 채운다(대기 중 "AI가 계산 결과를 읽고 설명을 쓰는 중이에요" 자리표시, 완료 후 `source`에 따라 "AI 응답" 또는 "규칙 기반 설명" 배지와 모델명·지연 표시). 상위 3개 항목 카드에는 `item_reasons[rank]` 한 줄을 넣는다. 응답 실패(네트워크)면 블록을 조용히 숨긴다. 다른 비교를 실행하거나 화면을 떠났으면 늦게 도착한 응답은 버린다(decision_id 대조). 결정 기록 상세는 `GET`으로 저장된 설명만 보여준다(없으면 생략, 생성하지 않음). 행동 카드(홈 top_action 카드, 행동 제안 목록)에는 "AI 설명 보기" 버튼이 있고, 클릭했을 때만 `POST /api/actions/{id}/explain`을 호출한다(첫 화면 LLM 0회 유지). 모든 설명 블록 옆에 AI 고지가 보인다.
- 대화 화면(2.9): 라우트 `#chat`. 사용자가 홈 입력창에서 메시지를 보내면 즉시 `#chat`으로 전환한다(홈 자체는 그대로, 첫 화면 LLM 0회 유지). 구성: 상단 헤더(뒤로 가기 → 홈, 대화 제목, "Gemini 체인" 모델 라벨), 가운데 대화 스레드(사용자 말풍선은 오른쪽, 응답은 왼쪽), 하단에 고정된 입력 카드(홈과 같은 카드, 375px에서도 하단 고정). 사이드바 "최근"에서 대화를 열면 `#chat`으로 간다.
- 응답 렌더링 순서: (1) 사용자 말풍선 추가 → (2) "생각 과정" 블록이 `stage` 이벤트마다 갱신된다(진행 중 스피너, done 체크, fallback 주의 색, skip 흐리게. 각 줄은 label, detail, ms) → (3) `reply`가 오면 블록을 한 줄 요약("생각 과정 5단계 · 3.2초", 클릭해 펼침)으로 접고 응답 문장을 타자 효과로 표시한다(글자당 8~15ms, 전체 2.5초 이내가 되도록 속도 조절, 클릭하면 즉시 전체 표시) → (4) 인라인 액션 카드(prepare_compare: 조건 요약과 "조건 확인하고 비교하기" 버튼, open_view: 이동 버튼, open_kb: 기존 제도 안내 카드) → (5) 칩 행. 자동 화면 이동은 하지 않는다(버튼으로 이동하고 뒤로 가기로 대화에 복귀).
- 배지: `llm_used`면 "AI 응답"과 모델명, 아니면 "규칙 기반 응답". AI 고지는 대화 화면에도 보인다. 저장된 대화를 다시 열면 각 응답의 trace가 접힌 상태로 표시된다. 스트림 실패(비 2xx, 네트워크)면 `POST /api/chat`으로 폴백해 생각 과정 없이 같은 렌더링을 한다.
- 답변 경로·노드·리소스(2.11): 대화 화면 오른쪽에 접을 수 있는 "리소스" 패널(데스크톱은 고정 열, 1024px 미만은 답변 아래 접이식)이 `resources`를 kind별 그룹과 개수로 보여준다. 응답 말풍선 위에는 노드 카드 묶음("사용한 노드 N개": 각 카드에 이름, 단계 문장 목록, 참조 리소스 수)이 오고, 생성 중에는 stage 이벤트로 실시간 갱신된다. `answer_format="markdown"`이면 제목·굵게·목록·번호 목록만 안전하게 렌더링한다(링크는 resources의 url만 허용, 그 밖의 HTML은 이스케이프). external 답변에는 "외부 검색 요약(확인 필요)" 배지와 Google 검색 제안 영역을 표시한다.

## 4. 골든 벡터 (tests)

| 케이스 | 입력 | 기대값 |
|---|---|---|
| 원리금균등 1 | 100,000,000원, 5.0%, 12개월 | 월 8,560,748원, 총이자 2,728,976원 ± 12원 |
| 원리금균등 2 | 30,000,000원, 6.0%, 36개월 | 월 912,658원, 총이자 2,855,688원 ± 36원 |
| 원금균등 | 12,000,000원, 12.0%, 12개월 | 1회차 1,120,000원(원금 1,000,000 + 이자 120,000), 총이자 780,000원 |
| 만기일시 | 10,000,000원, 6.0%, 12개월 | 매월 이자 50,000원, 12회차 10,050,000원, 총이자 600,000원 |
| 거치 | 10,000,000원, 6.0%, 거치 2 + 상환 10개월 원리금균등 | 1~2회차 50,000원, 3회차 1,027,706원 ± 5원, 총이자 377,057원 ± 60원 (2026-09-06 정정: 초안의 1,027,660/376,600은 계산 착오) |
| 재현성 | 같은 CompareContext, 같은 스냅샷 | result_hash 동일, 순위 동일 |
| 안전모드 | delinquency_signal 플래그 | R0 카드만 safe_mode, R3 카드 없음 |

## 5. 실행

```
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m scripts.load_products --source finlife --groups 020000,030300
.venv\Scripts\python -m scripts.load_products --source datago
python run.py            # 사용자 3666
python run.py 3676       # Claude Code 테스트용
.venv\Scripts\python -m pytest -q
```

## 6. 2026-09-06 릴리스 리뷰 보정 (계약이 바뀐 항목만 기록)

- `app/core/retirement.py::retirement_gap_projection`: 모든 금액(생활비·확정소득·월
  부족액·필요자금·적립 예상액)을 오늘 기준 실질 금액으로 통일했다(이전에는 생활비만
  물가상승률로 은퇴시점 명목값으로 부풀려 확정소득과 단위가 섞였다). `retirement_age`가
  policy `national_pension_start_age`(신규)보다 빠르면 브릿지 구간(국민연금 개시 전)을
  필요자금(PV)에 더한다: `annuity_pv(생활비, r, 브릿지연수) +
  pv_lump(annuity_pv(월부족액, r, 잔여연수), r, 브릿지연수)`. 단위 고지와 브릿지 가정은
  `assumptions`에 문장으로 남는다. 1.6/3.7~3.13절 원시 함수(fv_lump, annuity_pv 등) 자체의
  골든 벡터는 바꾸지 않았다.
- `config/policy_params.yaml`에 `national_pension_start_age`(65세, 국민연금 수급개시연령,
  needs_verification: true)를 추가했다.
- `app/models.py::CashflowPoint`에 `goal_outflow: int = 0`을 추가했다
  (`net = income - debt_payment - expenses - goal_outflow`). `GET /api/scenarios`는 이제
  `today=date.today()`와 `goals=profile.goals`를 함께 넘겨 목표(Goal)의 목표 시점을
  현금흐름에 반영한다.
- `app/models.py::CompareContext`: `amount`는 `Field(gt=0)`, `term_months`는
  `Field(ge=1, le=600)`로 범위를 제한했다(위반 시 422). `GET /api/scenarios`의 `horizon`
  쿼리는 `ge=1, le=360`, `GET /api/loans/{id}/schedule`의 `extra` 쿼리는 `ge=0`(모두 위반 시
  422).
- `app/services/compare.py::run_compare`(결정 D5): `category`가 `deposit`/`saving`이면
  `ValueError`("예·적금은 순위 비교 대상이 아닙니다. 공시 열람만 제공합니다.") → HTTP 422.
  `prepare_context`는 이 두 카테고리에는 프로필의 `credit_band`를 추정해 넣지 않는다.
  채팅 `compare` 의도도 예금/적금 카테고리면 이 문구로만 답하고 `prepare_compare` 액션을
  만들지 않는다.
- `app/api/routes.py`의 채팅 응답은 이제 KB(`kb/*.md`) 답변도 예외 없이
  `guardrails.check_text` 금칙어 검사를 통과해야 한다(기존 우회 제거). 이에 맞춰
  `kb/*.md`는 상호금융권 등 개별 금융기관 실명과 "대출비교 플랫폼" 안내 문구를 쓰지
  않는다(공공기관·정책상품명·금융결제원·금감원 파인·금융회사 앱/창구 안내만 허용, 원칙 3).
- `app/data/datago.py`의 정책 데이터셋 `disclosure_url`이 data.go.kr 개발자 문서 대신
  공공기관 이용자 안내 페이지를 가리킨다: 디딤돌 → `https://www.hf.go.kr`, 서민금융
  상품 기본정보·대출상품한눈에 → `https://www.kinfa.or.kr`.
- `app/services/actions.py::list_actions`는 이제 생애 단계 임계값(`config/thresholds.yaml`)을
  로드해 `evaluate_rules`에 넘긴다. 그 결과 R8(저축률 미달)·R9(원리금상환비율 초과)·
  R10(노후소득 충당률 미달)이 `GET /api/actions`·`GET /api/home`에도 나타날 수 있다.
- `app/core/loan.py::prepay_fee`에 선택 인자 `params: PolicyParams | None = None`을
  추가했다(있으면 `prepay_fee_period_months`를 분모로 쓰고, 없으면 기존처럼 36).
- (설명 문장 보강) `app/models.py::ExplainResult` 신설, 2.8절 추가. `LLMProvider.explain`에 선택 인자 `schema`가 생겼다(없으면 기존처럼 텍스트). `config/llm.yaml`에 `explain_total_deadline_seconds`(15)를 추가했다. `db.explanations` 테이블 신설. API 표에 `/api/compare/{decision_id}/explain`(POST, GET)과 `/api/actions/{action_id}/explain`(POST)을 추가했다. 화면 규격 3절 마지막 항목 참고.
- (대화 스트리밍) 2.9절 추가. `ChatReply`에 `trace: list[dict] = []`, `model: str | None = None`을 추가했다. `chat_messages.trace_json` 컬럼(마이그레이션). `POST /api/chat/stream` 신설. `config/llm.yaml`에 `chat_explain_deadline_seconds`(8). `LLMProvider.explain`에 `deadline_seconds` 선택 인자.
- (배포 준비) 2.10절 추가. `seed/products_seed.json.gz`, `scripts/export_seed.py` 신설, `app/main.py`에 시드 적재·자동 적재·CORS 환경변수와 DB 폴더 생성, `run.py`에 `PORT` 지원. 브라우저 전용 모드(Pyodide)는 PMO 결정으로 보류(설계만 2.10절에 남김).
- (답변 경로·리소스) 2.11절 추가. `app/models.py`에 `ChatResource` 신설, `ChatReply`에 `route`, `resources`, `answer_format` 추가(기본값이라 기존 호출 호환).
