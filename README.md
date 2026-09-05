# DONN PoC

생성형 LLM 기반 개인 부채 코치(한국어) PoC입니다. 순위와 금액 같은 수치는 전부
계산 엔진(코드)이 만들고, LLM(Gemini)은 사용자 발화에서 의도만 추출하거나
문장을 다듬는 보조 역할만 합니다. 데모 목적의 저장소이며 상세 계약은
`SPEC.md`, 공용 타입은 `app/models.py`가 단일 진실입니다.

## 실행

```
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m scripts.load_products --source finlife --groups 020000,030300
.venv\Scripts\python -m scripts.load_products --source datago

python run.py              # 사용자용 서버, http://localhost:3666
python run.py 3676         # Claude Code 테스트/미리보기 전용 포트
python run.py --reload     # 코드 수정 시 자동 재시작

run.bat load                # 위 두 적재 명령을 한 번에 실행(.venv 없으면 자동 생성)
```

브라우저에서 `http://localhost:3666`으로 접속하면 됩니다(`GET /`이 `web/index.html`을
그대로 서빙합니다). `/static`에는 `web/`의 정적 파일이 그대로 마운트됩니다.

## 테스트

```
.venv\Scripts\python -m pytest -q
```

현재 스위트 구성(총 136개 수집):

| 파일 | 개수 | 비고 |
|---|---|---|
| `tests/test_core_schedule.py`, `test_core_rules.py`, `test_core_ranking.py` | 71 | `app/core` 순수 함수(상환 스케줄, 대출 계산, 규칙 R0~R6, 랭킹, 해시) |
| `tests/test_data.py` | 16(2 스킵) | `app/data`(DB, finlife/datago 정규화, 정책 파라미터, 합성 페르소나) |
| `tests/test_llm.py` | 31(1 스킵) | Gemini 체인 폴백(`requests.post` 모킹), 가드레일(금칙어/PII 마스킹/규칙 기반 파싱) |
| `tests/test_api.py` | 18 | FastAPI 엔드포인트 전체(임시 DB, 가짜 상품 픽스처, Gemini는 monkeypatch로 통제) |

네트워크가 필요한 테스트(금감원/공공데이터/Gemini 실 호출)는 기본적으로 스킵되며
`DONN_NETWORK_TESTS=1`일 때만 실행됩니다.

## 폴더 구조

```
app/
  models.py     공용 타입(pydantic v2). 이 파일과 SPEC.md가 어긋나면 이 파일이 우선.
  core/         순수 계산 함수(I/O 없음): schedule, loan, capacity, scenarios, rules, ranking, hashing
  data/         DB(SQLite)와 외부 API: db, finlife, datago, products, policy, synthetic
  services/     core+data 조합: insights(홈), compare(공시 비교), actions(행동 카드),
                decisions(결정 기록/재현), session(단일 데모 세션)
  llm/          provider(공급자 계약), gemini(Gemini REST 체인 어댑터), guardrails(금칙어/PII/규칙 기반 파싱)
  api/          schemas, routes(엔드포인트)
  main.py       FastAPI 앱 진입점, 정적 파일 서빙, 시작 시 init_db()
web/            프론트엔드(빌드 없는 순수 HTML/CSS/JS, 외부 CDN 의존 없음)
config/         llm.yaml(Gemini 체인 설정), policy_params.yaml(규제/내부 기준 수치)
scripts/        load_products(상품 스냅샷 적재), gen_synthetic(합성 거래내역 생성) CLI
tests/          pytest 스위트
data/           donn.db(SQLite, 커밋 제외), cache/(외부 API 응답 원본 캐시)
```

## 환경변수 (`.env`, 키 이름만)

```
FINLIFE_AUTH_KEY          금융감독원 "금융상품한눈에" 오픈API 인증키
DATA_GO_KR_SERVICE_KEY    공공데이터포털 서비스키(디코딩된 값 그대로)
GEMINI_API_KEY            단일 키 자리(호환용, 현재 코드는 GEMINI_API_KEYS를 사용)
GEMINI_API_KEYS           Gemini API 키 목록, 쉼표 구분(체인의 안쪽 루프)
GEMINI_MODEL_CHAIN        Gemini 모델 체인, 쉼표 구분(체인의 바깥 루프)
DONN_DB_PATH              SQLite 파일 경로(기본값 data/donn.db)
```

키 값은 `.env`에만 두고 소스 코드, 로그, 커밋에는 절대 넣지 않습니다(로그에는
모델명/키 인덱스/지연시간/상태코드만 남깁니다).

## M0 절대 규칙

- 순위와 수치는 코드가 계산하고 LLM은 문장만 씁니다. 첫 화면 로드 시 LLM 호출은
  0회입니다(`GET /api/home`).
- 상품 실명, 금융회사명, 판매 페이지 바로가기를 사용자 화면에 노출하지 않습니다.
  익명 라벨("A은행 신용대출")과 금감원 공시 링크만 씁니다.
- "추천" 대신 "비교", "공시 열람" 용어를 씁니다. 화면과 문서 어디에도 em dash를
  쓰지 않습니다.
- 규제 수치는 `config/policy_params.yaml`에서만 읽고, 검증되지 않은 값은
  "(확인 필요)"로 표시합니다.
- 개인신용정보 파생 수치(잔액, 소득, 금리, 대출 금액 등)는 외부 LLM으로 보내지
  않습니다. 채팅 발화는 `guardrails.mask_pii`로 마스킹한 뒤에만 Gemini로 전송되며,
  Gemini에는 의도·카테고리 같은 범주형 슬롯만 요청합니다.

## 현재 한계

- 공공데이터포털의 "대출상품한눈에"(서민금융진흥원, KINFA) 데이터셋은 활용신청
  승인이 아직 완료되지 않아 호출 시 오류 응답을 받습니다. 예외를 던지는 대신
  빈 목록과 로그로 안전하게 처리해 두었으며, 승인되면 별도 코드 수정 없이 정상
  동작합니다.
- Gemini는 Google AI Studio 무료 티어 키 8개를 순환합니다. 무료 티어 특성상
  특정 모델이 일시적으로 503(수요 폭주)을 반환할 수 있어 모델x키 체인 폴백으로
  대응하고, 모두 실패하면 규칙 기반 파서(`app.llm.guardrails.parse_message`)
  응답으로 자동 전환됩니다.
- 채팅은 의도/슬롯 추출(extract)까지만 Gemini를 사용하고, 실제 응답 문장은 항상
  코드가 만든 한국어 템플릿입니다(자유 서술형 설명 생성은 이후 단계 범위).
- 소비 패턴 화면은 합성 거래내역 CSV 다운로드만 제공하며, 실제 분석 기능은
  준비 중입니다.
