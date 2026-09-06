"""pytest 전역 설정: 어떤 테스트 모듈이 먼저 import되든 실제 data/donn.db를 건드리지 않게 한다.

`app.data.db.DB_PATH`는 최초 import 시점의 `DONN_DB_PATH`로 고정되므로, 테스트 모듈보다 먼저
로드되는 이 파일에서 임시 DB 경로와 결정론적 LLM 키·호출 제한 해제를 설정한다
(2026-09-06: `pytest tests/test_data.py tests/test_api.py`처럼 일부만 돌리면 test_data가
db를 먼저 import해 실제 DB에 픽스처가 섞여 들어가는 사고가 있었다).
"""
from __future__ import annotations

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="donn_pytest_")
os.environ.setdefault("DONN_DB_PATH", os.path.join(_TMP_DIR, "test.db"))
os.environ.setdefault("GEMINI_API_KEYS", "test-key-1,test-key-2")
os.environ.setdefault("GEMINI_MODEL_CHAIN", "test-model-a,test-model-b")
os.environ.setdefault("DONN_RATE_LIMIT_PER_5MIN", "0")
os.environ.setdefault("DONN_RATE_LIMIT_GLOBAL_PER_5MIN", "0")
os.environ.setdefault("DONN_SEED_ON_EMPTY", "0")
os.environ.setdefault("DONN_AUTOLOAD_PRODUCTS", "0")
