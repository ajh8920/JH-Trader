from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # "admin" | "user"
    api_key = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    portfolio_items = db.relationship(
        "PortfolioItem", backref="user", cascade="all, delete-orphan"
    )
    alerts = db.relationship("Alert", backref="user", cascade="all, delete-orphan")
    infinite_positions = db.relationship(
        "InfinitePosition", backref="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self):
        return self.role == "admin"


class PortfolioItem(db.Model):
    __tablename__ = "portfolio_items"
    __table_args__ = (db.UniqueConstraint("user_id", "ticker", name="uq_user_ticker"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker = db.Column(db.String(16), nullable=False)
    qty = db.Column(db.Float, default=0)
    buy_price = db.Column(db.Float, default=0)
    name = db.Column(db.String(128), default="")
    current_price = db.Column(db.Float)
    target_price = db.Column(db.Float)
    change_pct = db.Column(db.Float, default=0)

    def to_dict(self):
        return {
            "ticker": self.ticker,
            "qty": self.qty,
            "buyPrice": self.buy_price,
            "name": self.name,
            "currentPrice": self.current_price,
            "targetPrice": self.target_price,
            "changePct": self.change_pct,
        }


class Alert(db.Model):
    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker = db.Column(db.String(16), nullable=False)
    price = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False, default="above")
    triggered = db.Column(db.Boolean, default=False)
    triggered_at = db.Column(db.String(20))
    created = db.Column(db.String(20))

    def to_dict(self):
        return {
            "id": self.id,
            "ticker": self.ticker,
            "price": self.price,
            "type": self.type,
            "triggered": self.triggered,
            "triggeredAt": self.triggered_at,
            "created": self.created,
        }


class InfinitePosition(db.Model):
    __tablename__ = "infinite_positions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker = db.Column(db.String(16), nullable=False)
    version = db.Column(db.String(10), default="v2")
    splits = db.Column(db.Integer, nullable=False, default=40)
    target_return_pct = db.Column(db.Float, nullable=False, default=10.0)
    seed = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    trades = db.relationship(
        "InfiniteTrade", backref="position", cascade="all, delete-orphan",
        order_by="InfiniteTrade.trade_date, InfiniteTrade.id",
    )


class InfiniteTrade(db.Model):
    __tablename__ = "infinite_trades"

    id = db.Column(db.Integer, primary_key=True)
    position_id = db.Column(db.Integer, db.ForeignKey("infinite_positions.id"), nullable=False)
    trade_date = db.Column(db.String(20), nullable=False)
    action = db.Column(db.String(4), nullable=False)  # "buy" | "sell"
    price = db.Column(db.Float, nullable=False)
    qty = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255), default="")

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.trade_date,
            "action": self.action,
            "price": self.price,
            "qty": self.qty,
            "note": self.note or "",
        }


class KrFundamental(db.Model):
    """DART(전자공시시스템)에서 받아온 국내 상장사 연간 재무제표 캐시.

    사용자별 데이터가 아니라 전체 사용자가 공유하는 시장 데이터라서 user_id가 없다.
    사업보고서(연간) 하나를 조회하면 당기/전기/전전기 3개 연도 금액이 함께 오므로,
    bsns_year는 "그 보고서가 표지에 적힌 기준연도"이고 실제로는 그 연도를 포함해
    앞선 2개 연도까지 알 수 있다 - 어느 연도 금액인지는 이 레코드 자체가 이미 특정
    연도(=조회 기준연도) 값만 담고 있으므로 신경 쓸 필요 없다(연도별로 별도 행 생성).
    """

    __tablename__ = "kr_fundamentals"
    __table_args__ = (
        db.UniqueConstraint("stock_code", "bsns_year", name="uq_kr_fund_stock_year"),
    )

    id = db.Column(db.Integer, primary_key=True)
    stock_code = db.Column(db.String(10), nullable=False, index=True)
    corp_code = db.Column(db.String(10), nullable=False)
    corp_name = db.Column(db.String(128), default="")
    bsns_year = db.Column(db.String(4), nullable=False)
    fs_div = db.Column(db.String(4), default="CFS")  # CFS=연결, OFS=별도
    rcept_no = db.Column(db.String(20), default="")  # 공시 접수번호(앞 8자리=제출일 YYYYMMDD)
    total_equity = db.Column(db.Float)  # 자본총계
    net_income = db.Column(db.Float)  # 당기순이익
    revenue = db.Column(db.Float)  # 매출액
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def rcept_date(self):
        """공시 제출일(YYYY-MM-DD). 이 날짜 이후에야 시장에 이 데이터가 공개된 것으로 본다."""
        if not self.rcept_no or len(self.rcept_no) < 8:
            return None
        d = self.rcept_no[:8]
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


class QuantBacktestJob(db.Model):
    """국내 퀀트 리밸런싱 백테스트는 전체 시장 종목의 과거 시점 가격을 실시간으로
    조회해야 해서 몇 분씩 걸릴 수 있다 - Render의 요청 타임아웃(30초)을 넘기므로
    요청-응답 안에서 바로 계산하지 않고, 백그라운드 스레드에서 돌려 이 테이블에
    결과를 저장한다. 프런트는 job을 만든 뒤 status가 done/error가 될 때까지 이
    행을 폴링한다. user_id로 걸어두어 다른 사용자의 진행 중인 백테스트가 안 보이게 한다.
    """

    __tablename__ = "quant_backtest_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="pending")  # pending|running|done|error
    result_json = db.Column(db.Text)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KrPriceCache(db.Model):
    """국내 퀀트 스크리닝용 현재가 캐시. 프로세스 메모리가 아니라 DB에 두는 이유:
    gunicorn 워커가 여러 개면 각자 별도 프로세스라 메모리 캐시가 서로 안 보여서,
    캐시를 못 채운 워커가 요청을 받으면 매번 "준비 안 됨"이 뜨는 문제가 있었다.
    DB에 두면 어느 워커가 채웠든 모든 워커가 같이 읽을 수 있다. updated_at의
    최신값으로 "다른 워커가 이미 최근에 갱신 중/갱신함"을 판단해, 여러 워커가
    동시에 전종목을 중복으로 받아 메모리/CPU를 과하게 쓰는 것도 함께 막는다.
    """

    __tablename__ = "kr_price_cache"

    stock_code = db.Column(db.String(10), primary_key=True)
    price = db.Column(db.Float, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrendScreenCache(db.Model):
    """트렌드 템플릿(미너비니 스타일) 스크리닝 결과 캐시. 유니버스 전체(국내
    ~2,700종목 또는 미국 ~600종목)의 가격 히스토리를 받아 계산하는 데 시간이
    걸리므로(KrPriceCache와 같은 이유로) 요청 중이 아니라 백그라운드 스레드가
    미리 계산해 이 테이블에 저장해두고, API는 결과만 읽는다.
    """

    __tablename__ = "trend_screen_cache"
    __table_args__ = (
        db.UniqueConstraint("market", "code", name="uq_trend_screen_market_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    market = db.Column(db.String(4), nullable=False)  # "KR" | "US"
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(128), default="")
    industry = db.Column(db.String(128))
    price = db.Column(db.Float)
    ma50 = db.Column(db.Float)
    ma150 = db.Column(db.Float)
    ma200 = db.Column(db.Float)
    week52_high = db.Column(db.Float)
    week52_low = db.Column(db.Float)
    pct_above_52w_low = db.Column(db.Float)
    pct_below_52w_high = db.Column(db.Float)
    rs_rating = db.Column(db.Integer)
    pass_count = db.Column(db.Integer)
    all_pass = db.Column(db.Boolean, default=False)
    conditions_json = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScreenerWatchlist(db.Model):
    """스크리닝 탭에서 사용자가 별표(찜)한 종목 목록."""

    __tablename__ = "screener_watchlist"
    __table_args__ = (
        db.UniqueConstraint("user_id", "market", "code", name="uq_watchlist_user_market_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    market = db.Column(db.String(4), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(128), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
