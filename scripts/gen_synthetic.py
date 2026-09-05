"""CLI: 페르소나 합성 거래내역 생성.

사용례:
    python -m scripts.gen_synthetic --persona P1 --months 3 --out data/cache/P1.csv
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from app.data.synthetic import generate_transactions, transactions_to_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="DONN 합성 거래내역 생성")
    parser.add_argument("--persona", required=True, help="페르소나 ID (P1..P8)")
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--out", required=True, help="출력 CSV 경로")
    args = parser.parse_args()

    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    rows = generate_transactions(args.persona, months=args.months, seed=args.seed, end=end)
    csv_text = transactions_to_csv(rows)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(csv_text, encoding="utf-8-sig")
    print(f"wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
