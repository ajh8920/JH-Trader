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
    # 스크리닝 상세 "재무상태표"/"포괄손익계산서" 카테고리 및 안정성 비율(유동비율 등)
    # 계산용으로 추가한 항목들. 전부 DART 응답에 이미 같이 오는 걸 더 뽑은 것뿐이라
    # API 호출은 추가로 들지 않지만, 기존에 저장해둔 행들은 이 필드들이 비어 있어
    # 한 번 재조회가 필요하다.
    total_assets = db.Column(db.Float)  # 자산총계
    current_assets = db.Column(db.Float)  # 유동자산
    current_liabilities = db.Column(db.Float)  # 유동부채
    total_liabilities = db.Column(db.Float)  # 부채총계
    equity_attributable = db.Column(db.Float)  # 자본총계(지배)
    issued_capital = db.Column(db.Float)  # 자본금
    inventories = db.Column(db.Float)  # 재고자산 (당좌비율 계산용)
    cash_and_equivalents = db.Column(db.Float)  # 현금및현금성자산 (순부채비율 계산용)
    operating_income = db.Column(db.Float)  # 영업이익
    net_income_attributable = db.Column(db.Float)  # 당기순이익(지배)
    profit_before_tax = db.Column(db.Float)  # 세전계속사업이익
    gross_profit = db.Column(db.Float)  # 매출총이익
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


class ScreeningBacktestJob(db.Model):
    """스크리닝(트렌드 템플릿/미너비니 단계) 전략의 과거 구간 워크포워드 백테스트.
    QuantBacktestJob과 같은 이유(전체 유니버스를 여러 시점에 재평가하느라 몇 분~
    몇십 분 걸릴 수 있어 Render 요청 타임아웃 안에 못 끝난다)로 백그라운드
    스레드 + 폴링 패턴을 그대로 따른다.
    """

    __tablename__ = "screening_backtest_jobs"

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


class KrDelistedPrice(db.Model):
    """상장폐지 종목의 과거 일별 시세(백테스트 생존편향 제거용). 상장일부터
    상장폐지일까지 전체 이력을 한 번만 채우면 다시 바뀌지 않는 정적 데이터라
    (더 이상 거래되지 않으므로) KrPriceCache와 달리 주기적 갱신이 필요 없다.
    yfinance는 상장폐지 종목 데이터를 제공하지 않아 FinanceDataReader(FDR)로
    data_pipeline.fetch_delisted_kr가 로컬에서 한 번 받고, data_pipeline.
    import_delisted_to_db가 이 테이블에 채워 넣는다.

    vcp_strategy.run_vcp_backtest(include_delisted=True)만 이 테이블을 읽어
    백테스트 유니버스에 상장폐지 종목을 포함시킨다 - 실시간 스크리닝/모의투자는
    절대 이 테이블을 쓰지 않는다(오늘 살 수 없는 종목이 후보로 뜨면 안 되므로).
    종목명/시장/상장폐지일/사유 같은 메타데이터는 자주 안 바뀌는 정적 목록이라
    DB가 아니라 kr_stocks.json과 같은 방식으로 kr_delisted_stocks.json에 둔다.
    """

    __tablename__ = "kr_delisted_prices"
    __table_args__ = (
        db.UniqueConstraint("stock_code", "date", name="uq_kr_delisted_price_code_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    stock_code = db.Column(db.String(10), nullable=False, index=True)
    date = db.Column(db.String(10), nullable=False)
    close = db.Column(db.Float, nullable=False)
    high = db.Column(db.Float, nullable=False)
    low = db.Column(db.Float, nullable=False)
    volume = db.Column(db.Float)


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
    sector = db.Column(db.String(128))  # 국내는 신뢰할 만한 소스가 없어 비워둠, 미국은 S&P500 편입 종목만 채움
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
    stage = db.Column(db.Integer)  # 1~4, 와인스타인/미너비니류 사이클 근사 분류(trend_screener._classify_stage)
    conditions_json = db.Column(db.Text)
    volume = db.Column(db.Float)  # 최근 거래일 거래량
    rel_volume = db.Column(db.Float)  # 최근 거래량 / 직전 20거래일 평균거래량
    avg_trade_value = db.Column(db.Float)  # 최근 20거래일 평균 거래대금(원) - 유동성 팩터(미너비니 v2)용
    donchian_high_15 = db.Column(db.Float)  # 직전 15거래일 고가(오늘 제외) - "어나니머스" 모의투자 돈치안 브레이크아웃 판정용
    market_cap = db.Column(db.Float)  # 국내: 원, 미국: 백만 달러(Finnhub 기준)
    pe_ratio = db.Column(db.Float)
    eps_growth = db.Column(db.Float)  # YoY %. 국내는 순이익 증가율로 근사
    dividend_yield = db.Column(db.Float)  # %. 미국만 제공(국내는 소스 없음)
    analyst_rating = db.Column(db.String(16))  # "Buy"|"Hold"|"Sell". 미국만 제공
    # 종목 상세 모달의 수익성/성장성/안정성/가치지표 아코디언용 확장 재무 지표.
    # 항목이 계속 늘어날 걸 감안해 컬럼을 늘리는 대신 JSON 하나로 묶어둔다.
    # 키: grossMargin/operatingMargin/netMargin/roe/roa/revenueGrowth/
    #     currentRatio/quickRatio/debtRatio/pbr/psr/evEbitda (전부 % 또는 배수, 없으면 키 자체가 없음)
    metrics_json = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # 가격/조건 갱신(trend_screen_refresher, 12시간 주기)과 재무 보강(미국만, Finnhub
    # 무료 API 호출 제한 때문에 더 느린 별도 주기) 갱신 시각을 분리해서 추적한다 -
    # 같은 컬럼을 쓰면 서로의 "최근에 갱신했는지" 판단이 꼬인다.
    fund_updated_at = db.Column(db.DateTime)


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


class PaperStrategyAccount(db.Model):
    """모의투자 탭 - 전략 하나(예: "미너비니 v2")를 실제 돈 없이 매일 자동으로
    그대로 따라가며 시뮬레이션하는 가상 계좌. 사용자별로 전략마다 독립된
    계좌를 갖는다(같은 전략을 여러 사용자가 각자 시작해도 서로 섞이지 않음).

    paper_trading.py의 일별 처리 로직이 background_thread(app.py의
    paper_trading_runner)에서 주기적으로 이 계좌를 찾아 진행 상황을 갱신한다.
    실제 백테스트(screening_backtest.py)와 같은 매매 규칙을 쓰지만, 여기는
    "그 시점까지의 과거"가 아니라 "오늘 실제로 확정된 가격"을 매일 하루치씩
    누적 반영한다는 점이 다르다(워크포워드가 아니라 진짜 실시간 진행).
    """

    __tablename__ = "paper_strategy_accounts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "strategy", name="uq_paper_account_user_strategy"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    strategy = db.Column(db.String(30), nullable=False)  # 예: "minervini_v2"
    market = db.Column(db.String(4), nullable=False, default="KR")
    seed = db.Column(db.Float, nullable=False, default=10_000_000)
    cash = db.Column(db.Float, nullable=False, default=10_000_000)
    peak_equity = db.Column(db.Float, nullable=False, default=10_000_000)
    started_on = db.Column(db.String(10))  # 최초 시작일(YYYY-MM-DD)
    # 마지막으로 "일별 처리"(보유 종목 손절/본전/트레일링/시간손절 판정)를 끝낸
    # 거래일. 이 날짜까지는 이미 반영이 끝났다는 뜻이라, 같은 날 리프레셔가
    # 여러 번 깨어나도 중복 처리하지 않는다.
    last_processed_date = db.Column(db.String(10))
    last_rescan_date = db.Column(db.String(10))  # 마지막으로 신규 진입 후보를 스캔한 날(주 단위)
    # cash_equitize(현금 유휴화 방지)를 쓰는 전략("어나니머스" 등)의 지수 프록시 보유
    # 수량 - vcp_strategy.run_vcp_backtest의 index_units와 같은 개념(주식이 아니라
    # 벤치마크 시리즈 기준 가상 수량).
    index_units = db.Column(db.Float, nullable=False, default=0.0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # "스위퍼" 전용 - 매매(신규진입/피라미딩/청산)가 발생한 거래일에 사용자가 지정한
    # 주소로 요약 메일을 보내는 기능(app.py의 sweeper_trade_alert_scheduler)용.
    # alert_email이 비어 있으면 그 계좌는 메일 알림 대상에서 제외된다.
    # last_alert_sent_date로 같은 거래일에 중복 발송하지 않는다.
    alert_email = db.Column(db.String(255))
    last_alert_sent_date = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    positions = db.relationship(
        "PaperPosition", backref="account", cascade="all, delete-orphan",
        order_by="PaperPosition.entry_date",
    )
    trades = db.relationship(
        "PaperTrade", backref="account", cascade="all, delete-orphan",
        order_by="PaperTrade.entry_date",
    )


class PaperPosition(db.Model):
    """모의투자 계좌가 현재 보유 중인 포지션 - screening_backtest.py의
    run_risk_managed_backtest가 메모리에서만 들고 있는 포지션 dict를 그대로
    DB 행으로 옮긴 것(재시작해도 유지되어야 하므로)."""

    __tablename__ = "paper_positions"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("paper_strategy_accounts.id"), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(128), default="")
    entry_date = db.Column(db.String(10), nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    shares = db.Column(db.Integer, nullable=False)
    entry_atr = db.Column(db.Float, nullable=False)
    risk_per_share = db.Column(db.Float, nullable=False)
    stop_price = db.Column(db.Float, nullable=False)
    stop_state = db.Column(db.String(20), nullable=False, default="initialStop")
    highest_high = db.Column(db.Float, nullable=False)
    bars_held = db.Column(db.Integer, nullable=False, default=0)  # 시간손절용 - 일별 처리 통과 횟수
    # 피라미딩(분할매수)을 쓰는 전략("어나니머스" 등)용 - entry_price/shares는 피라미딩
    # 이후엔 "평균단가/총보유수량"이 되고, last_entry_price는 다음 피라미딩 트리거
    # 판정에 쓰는 "가장 최근 매수가"(vcp_strategy의 pos["lastEntryPrice"]와 동일 개념),
    # total_cost는 평균단가 재계산 시 부동소수점 오차 누적을 피하려고 원가 총액을
    # 별도로 들고 있는 것(vcp_strategy의 pos["totalCost"]와 동일).
    pyramid_count = db.Column(db.Integer, nullable=False, default=0)
    last_entry_price = db.Column(db.Float)
    total_cost = db.Column(db.Float)
    initial_shares = db.Column(db.Integer)  # 최초 진입 수량(피라미딩 추가매수 규모 산정 기준, 피라미딩해도 안 바뀜)
    # 분할익절(+2R에 일부 매도, "스위퍼" 등 partial_profit_fraction>0 전략용) 1회
    # 한도 소진 여부 - vcp_strategy의 pos["partialTaken"]과 동일 개념.
    partial_taken = db.Column(db.Boolean, nullable=False, default=False)
    # 매매 알림 메일용 - 가장 최근 피라미딩(추가매수)이 반영된 거래일과 그때 추가된
    # 수량. 피라미딩은 이 행 자체를 갱신할 뿐 별도 이력 테이블이 없어서, "오늘
    # 피라미딩이 있었는지"를 조회하려면 이 두 필드가 필요하다.
    last_pyramid_date = db.Column(db.String(10))
    last_pyramid_shares = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PaperTrade(db.Model):
    """모의투자 계좌의 확정(청산 완료) 매매 이력. exit_reason으로 왜 팔았는지
    (손절/본전손절/트레일링손절/시간손절/기간종료) 그대로 남긴다."""

    __tablename__ = "paper_trades"

    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("paper_strategy_accounts.id"), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(128), default="")
    entry_date = db.Column(db.String(10), nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    exit_date = db.Column(db.String(10), nullable=False)
    exit_price = db.Column(db.Float, nullable=False)
    shares = db.Column(db.Integer, nullable=False)
    pnl_pct = db.Column(db.Float, nullable=False)
    exit_reason = db.Column(db.String(20), nullable=False)
    hold_days = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
