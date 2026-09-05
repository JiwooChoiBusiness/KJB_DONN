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

- 공공데이터포털의 "대출상품한눈에"(서민금융진흥원) API는 게이트웨이 주소에
  서비스명이 두 번 들어가야 동작합니다(`.../LoanProductSearchingInfo/LoanProductSearchingInfo/get...`).
  현재 436건이 정상 적재되며, 금리가 숫자로 공시되지 않은 상품은 원문만 보존합니다.
- Gemini는 Google AI Studio 무료 티어 키 8개를 순환합니다. 무료 티어 특성상
  특정 모델이 일시적으로 503(수요 폭주)을 반환할 수 있어 모델x키 체인 폴백으로
  대응하고, 모두 실패하면 규칙 기반 파서(`app.llm.guardrails.parse_message`)
  응답으로 자동 전환됩니다.
- 채팅은 의도/슬롯 추출(extract)까지만 Gemini를 사용하고, 실제 응답 문장은 항상
  코드가 만든 한국어 템플릿입니다(자유 서술형 설명 생성은 이후 단계 범위).
- 소비 패턴 분석은 브라우저에서 파일을 읽어 정규화한 거래만 서버로 보내며,
  서버는 집계·피처만 저장합니다(원본 거래 미저장). 실제 은행·카드사 내보내기
  형식은 대표적인 열 이름만 자동 인식하므로 매핑 화면에서 확인이 필요할 수 있습니다.
- 생애주기 층(재무비율, 노후 산식, 생애 단계)의 임계값과 연금·세제 수치는 도메인
  문서의 예시값이며 `config/thresholds.yaml`, `config/policy_params.yaml`에
  확인 필요 플래그로 표시되어 있습니다. 참고 시나리오이며 상품이나 자산 배분을 권하지 않습니다.

## 페르소나 러너

페르소나 8명이 첫 화면부터 채팅까지 제품 전 구간을 거치면서 PoC 절대 규칙을
어기지 않는지 자동으로 확인하는 결정론적 러너다(`docs/DONN_POC_SCOPE.md` 1절
"장면 6"). `data/donn.db`를 임시 파일로 복사해 실제 상품 스냅샷으로 검사하며,
원본 DB에는 아무 것도 쓰지 않는다.

```
.venv\Scripts\python -m scripts.run_personas                       # 전체 8명, 규칙 파서(기본값, 결정론)
.venv\Scripts\python -m scripts.run_personas --personas P1,P5,P7   # 일부만
.venv\Scripts\python -m scripts.run_personas --llm live            # 실제 Gemini 체인(느림, llm_used·지연 기록)
.venv\Scripts\python -m scripts.run_personas --llm live --judge    # + Gemini 루브릭 채점(정확성·안전성·실행가능성·톤)
.venv\Scripts\python -m scripts.run_personas --out docs/reports    # 리포트 출력 위치(기본값)
```

페르소나마다 다음을 순서대로 실행하고 검사한다: 계정 로드 → 홈(LLM 호출 0회,
카드 1~3장, 칩 5개 이하, 금칙어·em dash·특정 상품을 권하는 표현 없음) → 행동
카드 → 시나리오 → 생애주기(수치 전부 유한값, 면책 문구 존재) → 소비 패턴 분석
(합성 데이터) → 공시 비교(조건 확인 → 확인 → 동일 조건으로 두 번 실행해
`result_hash`와 익명 라벨이 완전히 같은지 확인, 상위 10건 이하, 금감원 공시
성격의 익명 라벨만 노출) → 결정 재현(`replay` 일치) → `tests/golden/questions.yaml`의
골든 질문(페르소나당 8~9개, 총 66개) → 소비 패턴/세션 정리.

골든 질문은 공시 비교 조건(숫자 근거 포함), 상환표·시나리오, 이번 달 행동,
제도 질문(제도 안내 KB 문서로 연결), 소비 패턴, 노후·저축·비상금, 은행명을 
언급하며 특정 상품을 권하도록 유도하는 질문(응답에 은행명이나 그런 표현이
새어나오면 안 됨), 전화번호가 든 문장(저장된 사용자 발화가 마스킹되는지 확인)을
모든 페르소나에 대해 다루고, 연체 신호가 있는 P5·P7에는 위기 표현 문항을 하나
더 둬 홈 화면의 안전 모드(R0) 카드가 유지되는지 확인한다.

리포트는 `docs/reports/persona_run_<YYYYMMDD-HHMM>.md`(한국어, 페르소나별
표와 재현성 표, 실패·경고 목록)와 같은 이름의 `.json`으로 남고, 항상
`docs/reports/latest.md`/`latest.json`도 함께 갱신된다. `--judge`를 쓰면 발화와
답변에서 숫자를 전부 지운 뒤(개인신용정보 파생 수치 보호) Gemini로 1~5점 루브릭
점수를 매긴 표가 리포트에 추가된다. 키가 없으면 조용히 건너뛴다.

`tests/test_persona_runner.py`는 `scripts.run_personas`를 서브프로세스가 아니라
함수로 직접 불러와 규칙 파서 모드로 전 페르소나를 인프로세스로 돌리고 하드
실패가 0건인지 확인한다(전체 실행 시간 10초 안팎, `.venv\Scripts\python -m pytest -q`에
포함됨). `run_all()`은 끝나면 자신이 바꾼 DB 경로·금칙어 캐시·채팅 LLM 공급자를
호출 전 상태로 되돌리므로 같은 프로세스에서 도는 다른 테스트 파일에 영향을 주지
않는다.

데모 진행 순서와 각 장면에서 보여줄 실제 숫자는 `docs/DEMO_SCRIPT.md`에 정리돼
있다(러너 리포트에서 그대로 가져온 값이라 재실행해도 같은 값이 나온다).
