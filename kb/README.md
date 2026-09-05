# 제도 안내 KB

DONN 채팅의 `faq` 의도가 참고하는 공공 제도·정책 설명 문서 모음이다. 임베딩이나 LLM
호출 없이 `app/kb/search.py`의 결정적 키워드 검색만으로 답을 찾는다.

## 문서 목록

| slug | 제목 | 분류 |
| --- | --- | --- |
| `rate-cut-request` | 금리인하요구권 | rights |
| `ccrs-debt-adjustment` | 신용회복위원회 채무조정 | debt-adjustment |
| `court-rehabilitation` | 개인회생과 개인파산 개요 | court |
| `dsr-and-stress-dsr` | DSR과 스트레스 DSR 개요 | regulation |
| `prepayment-fee` | 중도상환수수료 | fees |
| `refinance-infrastructure` | 온라인 대환대출 인프라 이용 방법 | refinance |
| `policy-microfinance` | 정책서민금융 상품 유형 | policy-products |
| `deposit-insurance` | 예금자보호 한도와 대상 | protection |
| `credit-score-basics` | 신용점수 관리 기본 | credit |
| `illegal-lending-response` | 불법 사금융과 채권추심 대응 | protection |
| `student-loan-repayment` | 학자금 대출 상환 제도 | student |
| `consumer-rights` | 금융소비자 권리 | consumer |

## 문서 형식

각 `*.md` 파일은 YAML 프런트매터(`---` 사이)와 6개 고정 섹션으로 이뤄진다.

```
---
title: 문서 제목
slug: 파일명과 동일한 slug
category: rights | debt-adjustment | court | regulation | fees | refinance |
          policy-products | protection | credit | student | consumer
keywords: [검색에 쓸 핵심어 여러 개]
sources:
  - {title: "출처 제목", url: "https://...", accessed: "YYYY-MM-DD"}
verified_at: YYYY-MM-DD
needs_verification: true|false   # 숫자·날짜 중 공식 출처로 확인 못한 항목이 있으면 true
last_reviewed: YYYY-MM-DD
---
## 개요
## 대상과 요건
## 절차
## 비용과 유의점
## 관련 기관과 창구
## 자주 묻는 질문
```

`app/kb/search.py`의 `yaml.safe_load`로 프런트매터 블록만 그대로 파싱하므로, 위 형식을
벗어나면(예: `---`가 두 번 나오지 않거나 `sources`가 매핑 목록이 아니면) 문서 로딩이
실패한다.

## 콘텐츠 규칙 (요약)

- 한국어, 짧고 평이한 문장. 어려운 용어는 처음 나올 때 한 번 풀어 쓴다.
- em dash(—)를 쓰지 않는다. "추천" 대신 "안내", "비교", "확인"을 쓴다.
- 은행·카드·저축은행·캐피탈·보험사 등 민간 금융회사명이나 민간 상품명을 쓰지 않는다.
  금융감독원, 금융위원회, 신용회복위원회, 서민금융진흥원, 한국주택금융공사,
  한국장학재단, 예금보험공사, 법원 같은 공공기관명과 햇살론, 새희망홀씨, 사잇돌,
  디딤돌, 보금자리론, 소액생계비대출 같은 공공 제도명은 써도 된다.
- 숫자·요율·한도·기한·시행일은 `sources`에 적힌 공식 출처로 확인했거나, 본문에
  "(확인 필요)"를 표시하고 그 문서의 `needs_verification`을 `true`로 둔다.
- 문서 길이는 60~120줄을 지킨다.

## 검색 모듈

`app/kb/search.py`가 이 디렉터리를 읽어 다음을 제공한다.

- `load_docs(kb_dir="kb")`: 프런트매터를 파싱한 `KbDoc` 목록. 모듈 레벨 캐시, `reload()`로 강제 재로딩.
- `search(query, k=3, docs=None)`: 한글 문자 바이그램 TF-IDF와 키워드/제목 가중치로 매긴
  `KbHit` 목록(동점이면 slug 오름차순).
- `answer(query)`: 최상위 결과 점수가 임계값을 넘으면 `{slug, title, snippet, sources,
  needs_verification, disclaimer}` dict를, 아니면 `None`을 반환한다. `disclaimer`는 항상
  "제도 설명은 참고용이며 최신 내용은 관련 기관 안내를 확인하세요."로 고정이다.

새 문서를 추가하거나 기존 문서를 고치면 `tests/test_kb.py`를 다시 돌려 형식과 검색
결과를 확인한다.
