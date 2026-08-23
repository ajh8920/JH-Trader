"""data_pipeline.fetch_delisted_kr가 로컬에 받아둔 상장폐지 종목 parquet들을
실제 앱이 읽는 KrDelistedPrice DB 테이블로 가져온다. 이미 상장폐지된 종목의
과거 시세는 다시 바뀌지 않는 정적 데이터라, 이미 들어간 종목은 건너뛴다
(--force로 삭제 후 재수집). import_fundamentals_to_db.py와 같은 이유로
app.py 전체를 임포트하지 않고 최소 Flask 앱만 따로 만든다.

사용법:
  python -m data_pipeline.import_delisted_to_db              # 로컬 SQLite(.env 기본값)
  DATABASE_URL=postgresql://... python -m data_pipeline.import_delisted_to_db  # 프로덕션 Postgres
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import pandas as pd
from dotenv import load_dotenv
from flask import Flask

load_dotenv(PROJECT_DIR / ".env")

from models import KrDelistedPrice, db  # noqa: E402
from data_pipeline.common import PRICE_KR_DELISTED_DIR  # noqa: E402


def _make_app():
    app = Flask(__name__)
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{PROJECT_DIR / 'data' / 'app.db'}")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app, database_url


def run(force):
    app, database_url = _make_app()
    print(f"[import_delisted] 대상 DB: {database_url}")

    files = sorted(PRICE_KR_DELISTED_DIR.glob("*.parquet"))
    if not files:
        print("[import_delisted] parquet 파일이 없습니다:", PRICE_KR_DELISTED_DIR)
        return

    with app.app_context():
        db.create_all()  # kr_delisted_prices 테이블이 아직 없는 새 DB인 경우 대비
        existing_codes = set()
        if not force:
            existing_codes = {r[0] for r in db.session.query(KrDelistedPrice.stock_code).distinct().all()}
            print(f"[import_delisted] 이미 들어간 종목 {len(existing_codes)}개(건너뜀, --force로 재수집)")

        total_rows, done = 0, 0
        for path in files:
            code = path.stem
            if code in existing_codes:
                continue
            df = pd.read_parquet(path)
            if force:
                KrDelistedPrice.query.filter_by(stock_code=code).delete()
            mappings = [
                {
                    "stock_code": code, "date": r.date, "close": float(r.close),
                    "high": float(r.high), "low": float(r.low),
                    "volume": float(r.volume) if r.volume == r.volume else None,
                }
                for r in df.itertuples()
            ]
            db.session.bulk_insert_mappings(KrDelistedPrice, mappings)
            db.session.commit()
            total_rows += len(mappings)
            done += 1
            if done % 50 == 0 or done == len(files) - len(existing_codes):
                print(f"[import_delisted] {done}종목 처리, 누적 {total_rows}행")

        total = KrDelistedPrice.query.count()
        print(f"[import_delisted] 완료: 이번 실행 {done}종목 / {total_rows}행 추가, DB 전체 {total}행")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="상장폐지 종목 시세를 KrDelistedPrice DB 테이블로 가져오기")
    parser.add_argument("--force", action="store_true", help="이미 있는 종목도 삭제 후 재수집")
    args = parser.parse_args()
    run(args.force)
