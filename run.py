"""DONN PoC 서버 실행 스크립트.

사용법
  python run.py            -> http://localhost:3666  (사용자용 기본 포트)
  python run.py 3676       -> 다른 포트로 실행 (Claude Code 테스트용은 3676)
  python run.py --reload   -> 코드 수정 시 자동 재시작

시스템 파이썬으로 실행해도 .venv가 있으면 그 인터프리터로 다시 실행한다.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
DEFAULT_PORT = 3666


def _reexec_in_venv() -> None:
    if os.name != "nt" or not os.path.exists(VENV_PY):
        return
    if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(VENV_PY):
        return
    code = subprocess.call([VENV_PY, os.path.abspath(__file__), *sys.argv[1:]], cwd=ROOT)
    sys.exit(code)


def main() -> None:
    _reexec_in_venv()
    os.chdir(ROOT)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    # 포트 우선순위: 위치 인자 > DONN_PORT > PORT(PaaS 관례, SPEC 2.10) > 기본값.
    env_port = os.environ.get("DONN_PORT") or os.environ.get("PORT")
    port = int(args[0]) if args else int(env_port or DEFAULT_PORT)
    reload = "--reload" in flags

    try:
        import uvicorn  # noqa: WPS433
    except ImportError:
        print("uvicorn이 없습니다. 먼저 실행하세요:  .venv\\Scripts\\python -m pip install -r requirements.txt")
        sys.exit(1)

    print(f"DONN PoC -> http://localhost:{port}  (종료: Ctrl+C)")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=reload)


if __name__ == "__main__":
    main()
