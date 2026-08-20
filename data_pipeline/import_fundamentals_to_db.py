"""fetch_fundamentals_dart.py가 받아 parquet(fundamentals/kr/{연도}.parquet)에
쌓아둔 DART 재무데이터를 실제 앱이 읽는 KrFundamental DB 테이블로 가져온다.

fetch_fundamentals_dart.py는 여러 프로세스(API 키 샤딩)가 동시에 같은 DB 행을
건드리는 걸 피하려고 parquet 파일에만 쓰고 DB에는 손대지 않는다 - 그래서 이
가져오기 단계를 별도로, 한 프로세스에서만 실행해야 한다(멱등: 여러 번 실행해도
안전, stock_code+bsns_year 기준으로 upsert).

app.py를 직접 import하면 모듈 스코프에서 백그라운드 스레드(가격 리프레셔 등)가
전부 같이 기동돼(과거에 이 문제로 데드락을 겪은 적이 있다 - dart_fetch.py의
SKIP LOCKED 주석 참고) 이 일회성 스크립트 실행에는 부적절하다. app.py와 같은
DB 접속 설정만 최소로 복제한 별도 Flask 앱을 만들어 쓴다.

사용법:
  python -m data_pipeline.import_fundamentals_to_db              # 로컬 SQLite(.env 기본값)
  DATABASE_URL=postgresql://... python -m data_pipeline.import_fundamentals_to_db  # 프로덕션 Postgres
"""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import pandas as pd
from dotenv import load_dotenv
from flask import Flask

load_dotenv(PROJECT_DIR / ".env")

from models import KrFundamental, db  # noqa: E402
from data_pipeline.common import FUND_KR_DIR  # noqa: E402

EXTRA_FIELDS = [
    "total_assets", "current_assets", "current_liabilities", "total_liabilities",
    "equity_attributable", "issued_capital", "inventories", "cash_and_equivalents",
    "operating_income", "net_income_attributable", "profit_before_tax", "gross_profit",
]


def _make_app():
    app = Flask(__name__)
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{PROJECT_DIR / 'data' / 'app.db'}")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    return app, database_url


def _to_float(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return float(v)


def run():
    app, database_url = _make_app()
    print(f"[import_fundamentals] 대상 DB: {database_url}")

    files = sorted(FUND_KR_DIR.glob("*.parquet"))
    if not files:
        print("[import_fundamentals] parquet 파일이 없습니다:", FUND_KR_DIR)
        return

    with app.app_context():
        db.create_all()  # kr_fundamentals 테이블이 아직 없는 새 DB인 경우 대비
        existing = {
            (r.stock_code, r.bsns_year): r.id
            for r in KrFundamental.query.with_entities(
                KrFundamental.id, KrFundamental.stock_code, KrFundamental.bsns_year
            ).all()
        }
        print(f"[import_fundamentals] 기존 행 수: {len(existing)}")

        total_inserted = 0
        total_updated = 0
        for path in files:
            df = pd.read_parquet(path)
            year_inserted = year_updated = 0
            for rec in df.to_dict("records"):
                stock_code = str(rec["stock_code"])
                bsns_year = str(rec["bsns_year"])
                key = (stock_code, bsns_year)
                existing_id = existing.get(key)
                if existing_id is not None:
                    row = db.session.get(KrFundamental, existing_id)
                    year_updated += 1
                else:
                    row = KrFundamental(
                        stock_code=stock_code, corp_code=str(rec["corp_code"]),
                        corp_name=str(rec.get("corp_name") or ""), bsns_year=bsns_year,
                    )
                    db.session.add(row)
                    year_inserted += 1
                row.fs_div = rec.get("fs_div") or "CFS"
                row.rcept_no = rec.get("rcept_no") or ""
                row.total_equity = _to_float(rec.get("total_equity"))
                row.net_income = _to_float(rec.get("net_income"))
                row.revenue = _to_float(rec.get("revenue"))
                for f in EXTRA_FIELDS:
                    setattr(row, f, _to_float(rec.get(f)))
                fetched_at = rec.get("fetched_at")
                if fetched_at is not None and not pd.isna(fetched_at):
                    row.fetched_at = pd.Timestamp(fetched_at).to_pydatetime().replace(tzinfo=None)
            db.session.commit()
            total_inserted += year_inserted
            total_updated += year_updated
            print(f"[import_fundamentals] {path.stem}: 신규 {year_inserted}건, 갱신 {year_updated}건 "
                  f"(parquet 총 {len(df)}행)")

        total = KrFundamental.query.count()
        print(f"[import_fundamentals] 완료: 이번 실행 신규 {total_inserted}건, 갱신 {total_updated}건, "
              f"DB 전체 {total}건")


if __name__ == "__main__":
    run()
