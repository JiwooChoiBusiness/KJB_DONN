"""결정론적 직렬화와 해시 (순수 함수). I/O 없음, app.models만 import한다."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from app.models import CompareContext, CompareItem


def _default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(_default(x) for x in obj)
    raise TypeError(f"canonical_json: {type(obj)!r} 타입은 직렬화할 수 없습니다.")


def canonical_json(obj: Any) -> str:
    """키 정렬, enum/date/datetime의 안정적 직렬화로 결정론적 JSON 문자열을 만든다."""
    return json.dumps(
        obj,
        default=_default,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )


def fingerprint(obj: Any) -> str:
    """canonical_json의 sha256 hex digest."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def result_hash(
    items: list[CompareItem], ctx: CompareContext, snapshot_id: str, versions: dict
) -> str:
    """비교 결과 재현성 확인용 해시."""
    payload = {
        "items": items,
        "ctx": ctx,
        "snapshot_id": snapshot_id,
        "versions": versions,
    }
    return fingerprint(payload)
