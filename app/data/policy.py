"""정책 파라미터 로더. 규제/내부 기준 수치는 config/policy_params.yaml에서만 읽는다
(SPEC 원칙 6). 값을 코드에 하드코딩하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.models import PolicyParam, PolicyParams

DEFAULT_PATH = "config/policy_params.yaml"


def load_policy_params(path: str = DEFAULT_PATH) -> PolicyParams:
    """YAML을 읽어 PolicyParams로 만든다. 각 항목은 PolicyParam으로 검증된다."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    version = str(data.get("version", "0.0.0"))
    raw_params: dict[str, dict] = data.get("params", {}) or {}

    params: dict[str, PolicyParam] = {}
    for key, fields in raw_params.items():
        fields = dict(fields or {})
        fields["key"] = key
        params[key] = PolicyParam(**fields)

    return PolicyParams(version=version, params=params)
