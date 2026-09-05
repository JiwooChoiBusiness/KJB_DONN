"""CLI: 상품 스냅샷 적재.

사용례:
    python -m scripts.load_products --source finlife --groups 020000,030300
    python -m scripts.load_products --source datago
"""
from __future__ import annotations

import argparse
import logging

from app.data.products import load_snapshot, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="DONN 상품 스냅샷 적재")
    parser.add_argument(
        "--source", required=True, choices=["finlife", "datago"], help="적재할 데이터 소스"
    )
    parser.add_argument(
        "--groups", default="020000,030300", help="finlife 권역코드 콤마 구분 (기본 020000,030300)"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    snapshot_id = load_snapshot([args.source], groups)
    print(f"snapshot_id={snapshot_id}")
    print(stats())


if __name__ == "__main__":
    main()
