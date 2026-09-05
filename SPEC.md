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
| POST | /api/chat | `ChatReply` | P4 전까지 `guardrails.parse_message` 기반 |
| GET | /api/synthetic/{persona_id}/transactions.csv | text/csv | 합성 거래내역 |

main.py: `FastAPI(title="DONN PoC")`, `GET /` → `web/index.html`, `/static` → `web/`. 시작 시 `init_db()`.

## 3. 화면 규격 (web/)

- 단일 페이지, 빌드 없음. `index.html`, `app.js`, `styles.css`. 폰트 `system-ui, "Malgun Gothic", sans-serif`. 외부 CDN 의존 없음.
- 레이아웃: 상단 바(좌: 로고 "DONN"과 작은 부제, 우: 원형 아바타 "나"), 좌측 사이드바 250px(배경 #FAFAFA, 우측 1px #E5E7EB, 접기 버튼), 본문 흰색 중앙 정렬 최대 폭 960px.
- 색: 포인트 #4F46E5, 포인트 연한 배경 #EEF2FF, 텍스트 #111827, 보조 텍스트 #6B7280, 경계 #E5E7EB, 긍정 #059669, 부정 #DC2626, 중립 #6B7280. 카드 radius 16px, 그림자 `0 1px 3px rgba(0,0,0,.06)`. 전송 버튼은 원형 포인트색.
- 사이드바: `[+ 새 대화]`; "리소스" 그룹: 내 부채, 공시 비교, 소비 패턴, 페르소나 테스트, 결정 기록; "고정" 그룹: 이번 달 행동(top_action 제목, 없으면 "부채를 입력하면 나타나요"); "최근" 그룹: 결정 기록 최근 5개; 하단: 환경설정(모델 설정 읽기 전용 표시).
- 홈(C안): 중앙 큰 제목 "DONN"과 한 줄 부제 "빚의 다음 한 걸음을 숫자로". 아래 인사이트 카드 1~3장(가로 카드, 톤별 좌측 색 바, 제목, 본문, 근거 수치 배지, 질문 버튼(칩), "이 분석은 왜 나왔나요?" 토글). 그 아래 입력 박스(둥근 카드, 좌측 + 버튼, "모드: M0 공시 비교" 배지, 우측 원형 전송 버튼, placeholder "어떤 부채 고민을 도와드릴까요?"). 그 아래 칩 3~5개. 그 아래 원형 아이콘 4개(내 부채, 공시 비교, 소비 패턴, 페르소나). 화면 하단 고정 면책 1줄과 AI 고지 1줄(작은 글씨, 항상 보임).
- 내 부채: 프로필 요약과 편집(월소득, 고정지출, 변동지출, 비상금, 신용 구간, 플래그 체크박스), 대출 목록 표, 추가/수정 폼(필수 5개: 종류, 잔액, 금리, 남은 개월, 상환방식. 나머지는 접힘), 대출 선택 시 상환표(첫 12행과 합계)와 시나리오 3종 요약 표(총이자, 부채 완료 월, 최소 누적 순현금).
- 공시 비교: 1단계 조건 확인 카드(추정 필드에 "추정" 배지, 수정 가능, 버튼 "이 조건으로 비교" → user_confirmed=true) → 2단계 결과 블록(정렬 기준 문장, 항목 카드: 순위, 익명 라벨, 금리와 종류 배지("전월 평균" 등), 월 납입, 총이자, 현재 대비 차이, 링크 "금융상품한눈에에서 확인", 유의 문구; 가정 목록; decision_id와 result_hash 작은 글씨; 버튼 "조건 바꿔서 다시 보기"). 실명 표시 금지.
- 소비 패턴: P5 전까지 "준비 중" 카드와 합성 CSV 다운로드 버튼.
- 페르소나: 8명 카드(이름, 한 줄, 부채 수, 총잔액) 버튼 "이 페르소나로 보기".
- 결정 기록: 목록과 버튼 "재현" → 일치 여부 표시.
- 채팅 입력: `POST /api/chat` → 응답 텍스트, 칩, action(`open_view`, `prepare_compare`) 처리. LLM 미연결 시 "규칙 기반 응답" 배지.
- 접근성: 버튼 aria-label, Enter로 전송. 375px에서 사이드바 접힘.

## 4. 골든 벡터 (tests)

| 케이스 | 입력 | 기대값 |
|---|---|---|
| 원리금균등 1 | 100,000,000원, 5.0%, 12개월 | 월 8,560,748원, 총이자 2,728,976원 ± 12원 |
| 원리금균등 2 | 30,000,000원, 6.0%, 36개월 | 월 912,658원, 총이자 2,855,688원 ± 36원 |
| 원금균등 | 12,000,000원, 12.0%, 12개월 | 1회차 1,120,000원(원금 1,000,000 + 이자 120,000), 총이자 780,000원 |
| 만기일시 | 10,000,000원, 6.0%, 12개월 | 매월 이자 50,000원, 12회차 10,050,000원, 총이자 600,000원 |
| 거치 | 10,000,000원, 6.0%, 거치 2 + 상환 10개월 원리금균등 | 1~2회차 50,000원, 3회차 1,027,660원 ± 5원, 총이자 376,600원 ± 60원 |
| 재현성 | 같은 CompareContext, 같은 스냅샷 | result_hash 동일, 순위 동일 |
| 안전모드 | delinquency_signal 플래그 | R0 카드만 safe_mode, R3 카드 없음 |

## 5. 실행

```
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m scripts.load_products --source finlife --groups 020000,030300
.venv\Scripts\python -m scripts.load_products --source datago
.venv\Scripts\python -m uvicorn app.main:app --reload --port 3666
.venv\Scripts\python -m pytest -q
```
