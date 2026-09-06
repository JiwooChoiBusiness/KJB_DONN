# DONN PoC 배포 가이드 (Render)

이 문서는 데모 서버(`https://kjb-donn.onrender.com`)를 Render 무료 플랜에 배포하고
운영하는 절차를 설명한다. 계약 문서는 `SPEC.md`, 프로젝트 규칙은 `CLAUDE.md`가
우선이며, 이 문서는 그 위의 실행 가이드다.

## 1. 배포 절차 (Render 블루프린트)

1. Render 대시보드에서 "New" -> "Blueprint"를 선택하고 이 GitHub 저장소를 연결한다.
   저장소 루트의 `render.yaml`을 그대로 읽어 서비스를 만든다(서비스명 `kjb-donn`,
   런타임 Python, 플랜 free, 리전 singapore).
2. 빌드 명령은 `pip install -r requirements.txt`, 시작 명령은
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`이다(`render.yaml`에 고정).
   헬스체크 경로는 `/api/health`.
3. `render.yaml`에 `sync: false`로 표시된 키(`FINLIFE_AUTH_KEY`,
   `DATA_GO_KR_SERVICE_KEY`, `GEMINI_API_KEYS`)는 소스에 값이 없으므로, Render
   대시보드의 서비스 -> Environment 탭에서 직접 입력해야 한다. 키 값은 대시보드에만
   존재해야 하며 저장소나 커밋 메시지, 로그에 남기지 않는다.
4. 첫 배포가 끝나면 `/api/health`를 열어 `status: "ok"`와 `snapshot_id`(빈 문자열이
   아닌지)를 확인한다. `DONN_SEED_ON_EMPTY` 기본값이 "1"이라 상품 테이블이 비어
   있으면 시작 시 `seed/products_seed.json.gz`를 자동으로 적재한다.
5. `DONN_AUTOLOAD_PRODUCTS=1`(render.yaml 기본값)이면 금감원/공공데이터 키가 있을 때
   시작 후 백그라운드 스레드로 실 데이터를 추가 적재한다. 실패해도 로그만 남고 첫 화면
   응답에는 영향이 없다.

## 2. 환경변수

| 이름 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `FINLIFE_AUTH_KEY` | 아니오 | (없음) | 금감원 금융상품한눈에 API 인증키. 없으면 finlife 자동 적재를 건너뛴다. |
| `DATA_GO_KR_SERVICE_KEY` | 아니오 | (없음) | 공공데이터포털 서비스키(디코딩된 값). 없으면 datago 자동 적재를 건너뛴다. |
| `GEMINI_API_KEYS` | 아니오 | (없음) | 쉼표로 구분한 Gemini API 키 목록(최대 8개, PMO 지정 순서대로 시도). 비어 있으면 `llm_available=false`이고 채팅은 규칙 기반 파서로만 동작한다. |
| `GEMINI_MODEL_CHAIN` | 아니오 | `gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash-lite` | 쉼표로 구분한 모델 폴백 체인(바깥 루프가 모델, 안쪽 루프가 키). |
| `DONN_AUTOLOAD_PRODUCTS` | 아니오 | `0`(로컬) / `1`(render.yaml) | "1"이면 시작 후 금감원/공공데이터 실 데이터를 백그라운드로 추가 적재한다. |
| `DONN_SEED_ON_EMPTY` | 아니오 | `1` | 상품 테이블이 비어 있을 때만 시작 시 `seed/products_seed.json.gz`를 적재한다. |
| `DONN_CORS_ORIGINS` | 아니오 | (없음, CORS 끔) | 쉼표로 구분한 허용 오리진. 정적 프론트엔드(GitHub Pages 등)가 다른 호스트에서 이 API를 부를 때만 쓴다. 이 배포(같은 출처로 화면과 API를 함께 서빙)에서는 비워 둔다. |
| `DONN_RATE_LIMIT_PER_5MIN` | 아니오 | `20` | 브라우저 세션(sid)별 5분 슬라이딩 윈도 호출 제한. 대상은 `POST /api/chat`, `/api/chat/stream`, `/api/compare/{id}/explain`, `/api/actions/{id}/explain`(Gemini를 부르는 경로). `0`이면 무제한. |
| `DONN_RATE_LIMIT_GLOBAL_PER_5MIN` | 아니오 | `150` | 위 경로들의 전체 접속자 합산 5분 호출 제한(Gemini 무료 티어 보호). `0`이면 무제한. |
| `PORT` | Render가 자동 주입 | - | `render.yaml`의 시작 명령이 그대로 쓴다. 로컬 실행에는 관여하지 않는다(3번 참고). |

키 값은 절대 로그나 커밋에 남기지 않는다(`CLAUDE.md` 절대 규칙).

## 3. 로컬 실행과 포트 규칙

```
python run.py              # 사용자용 서버, http://localhost:3666
python run.py 3676         # Claude Code의 테스트/미리보기 전용 포트(.claude/launch.json)
python run.py --reload     # 코드 수정 시 자동 재시작
run.bat load                # 금감원 + 공공데이터 상품 스냅샷 적재
.venv\Scripts\python -m pytest -q
```

두 포트를 동시에 띄워도 된다(서로 다른 프로세스, 같은 `data/donn.db`를 공유). 포트
우선순위는 위치 인자 -> `DONN_PORT` -> `PORT`(PaaS 관례) -> 기본값 3666 순이다. Render
배포에서는 `PORT`를 Render가 주입하므로 이 우선순위상 자동으로 그 값이 쓰인다.

## 4. 무료 플랜 제한

- 절전: 15분 동안 요청이 없으면 인스턴스가 잠들고, 다음 요청이 오면 다시 깨운다(첫
  응답까지 30초에서 1분 정도 걸릴 수 있다).
- 월 750시간 무료 실행 한도(계정 전체 무료 서비스 합산).
- 메모리 512MB.
- 디스크는 영구적이지 않다(재배포하면 초기화된다). `data/donn.db`(상품 스냅샷,
  세션, 대화, 결정 기록, 소비 분석)가 전부 이 디스크에 있으므로 재배포할 때마다
  같이 비워진다. 상품 스냅샷은 `DONN_SEED_ON_EMPTY`가 자동으로 복구하지만, 세션과
  대화 등 사용자 데이터는 애초에 데모용으로 휘발성이라는 전제로 설계됐다(PoC 범위).

## 5. 발표 전 체크리스트

1. 발표 10분 전쯤 미리 접속해 인스턴스를 깨워 둔다(절전 상태면 첫 로딩이 느리다).
2. `GET /api/health`를 열어 `status: "ok"`, `snapshot_id`가 비어 있지 않은지,
   `llm_available`이 기대한 값인지 확인한다.
3. 아무 페르소나로 채팅을 한 번 보내 실제로 응답이 오는지 확인한다(첫 호출이 콜드
   스타트와 겹치면 느릴 수 있다).
4. `llm_available`은 키가 설정돼 있는지만 보여준다. Gemini 무료 티어의 일일 호출
   한도를 이미 소진했다면 `llm_available=true`여도 실제 호출은 실패하고, 체인의
   모든 모델과 키를 다 시도한 뒤 자동으로 규칙 기반 파서로 폴백한다(화면은 계속
   동작하지만 문장이 더 정형화된다). 발표 중 이런 폴백이 보이면 정상 동작이라고
   안내하면 된다.
5. 브라우저별 세션 분리가 켜져 있으므로, 발표자와 참관객이 같은 링크에 각자
   접속해도 프로필과 대화가 서로 섞이지 않는다(6절 참고). 시크릿창으로 열면
   새 세션(새 `donn_sid`)이 된다는 점을 참고한다.

## 6. 세션 분리와 호출 횟수 제한

- Render에는 서버가 하나만 떠 있고 접속자는 여러 명일 수 있다. `app/main.py`의
  미들웨어가 요청마다 `donn_sid` 쿠키(HttpOnly, SameSite=Lax, 30일, https면 Secure)를
  발급/유지해 브라우저 하나당 하나의 세션으로 프로필, 대화, 결정 기록(공시 비교
  재현 포함), 소비 패턴 분석을 분리한다. 같은 페르소나를 여러 명이 동시에 선택해도
  서로의 화면에 영향을 주지 않는다.
- 같은 미들웨어가 Gemini를 호출하는 경로(`POST /api/chat`, `/api/chat/stream`,
  `/api/compare/{id}/explain`, `/api/actions/{id}/explain`)에 세션별(`DONN_RATE_LIMIT_PER_5MIN`,
  기본 20회/5분)과 전체 합산(`DONN_RATE_LIMIT_GLOBAL_PER_5MIN`, 기본 150회/5분) 호출
  제한을 건다. 초과하면 429와 함께 "요청이 너무 많아요. 잠시 후 다시 시도해 주세요."를
  돌려준다. 두 값 다 0으로 두면 제한이 없다(로컬 테스트와 `scripts/run_personas`는
  항상 0으로 실행된다).
- 카운터는 서버 프로세스 메모리에만 있다(재배포하면 초기화되고, Render 무료 플랜처럼
  워커가 여러 개면 워커별로 따로 센다).

## 7. 롤백

Render 대시보드에서 서비스 선택 -> Events 탭에서 이전 성공 배포를 찾아 옆의
"Rollback"을 누른다. 새로 커밋을 되돌려 다시 배포하는 방법도 가능하지만, 급할 때는
Rollback이 더 빠르다.

## 8. 로그 확인

Render 대시보드의 서비스 -> Logs 탭에서 실시간 로그를 볼 수 있다. 자동 적재 실패
(`app/main.py`의 `_autoload_products_in_background`)는 예외 스택트레이스와 함께
`donn.main` 로거로 남는다. Gemini 호출 로그는 모델명, 키 인덱스, 지연시간, 토큰 수만
남기고 키 값과 사용자 발화 원문은 남기지 않는다(`CLAUDE.md` 절대 규칙).

## 9. 상품 시드 갱신

배포 환경은 `DONN_SEED_ON_EMPTY=1`일 때 상품 테이블이 비어 있으면
`seed/products_seed.json.gz`를 적재한다. 최신 공시로 갱신하려면 로컬에서:

```
run.bat load
.venv\Scripts\python -m scripts.export_seed
```

`seed/products_seed.json.gz`가 새로 만들어지면 이 파일을 커밋하고 푸시한다. 다음
배포부터(디스크가 초기화돼 상품 테이블이 비어 있는 상태로 시작하므로) 새 시드가
반영된다.

## 10. GitHub Pages는 랜딩만

`_config.yml`은 GitHub Pages(Jekyll) 설정이다. Pages는 정적 파일만 서빙할 수 있으므로
이 저장소의 Pages는 `README.md`를 랜딩 페이지로만 보여준다(앱 코드,
테스트, 설정 파일 등은 `exclude` 목록에 넣어 Pages 빌드에서 제외한다). 실제 FastAPI
백엔드와 화면은 이 문서의 1절 절차로 Render에 별도 배포한다.
