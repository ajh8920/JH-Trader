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
    auto_infinite_positions = db.relationship(
        "AutoInfinitePosition", backref="user", cascade="all, delete-orphan"
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


class AutoInfinitePosition(db.Model):
    """무한매수법 2.2버전 실매매 자동화 포지션 - 메리츠증권 해외주식 계좌(meritz_api.py)와
    연동된다. 수기 입력 기반인 InfinitePosition/InfiniteTrade("실전 현황" 탭)와는
    완전히 분리된 테이블이다 - 이 테이블의 포지션은 실제로 메리츠 계좌에 주문이
    나간다.

    평단가·보유수량·사이클은 이 테이블에 저장하지 않는다 - auto_infinite.py의
    replay_position_state()가 이 포지션에 체결된(status="filled") AutoInfiniteOrder
    기록을 처음부터 PositionState로 재생해 매번 다시 계산한다(live_tracker.py가
    InfiniteTrade를 재생하는 것과 같은 패턴). **2026-10-22 설계 변경** - 이전에는
    "메리츠 실계좌 잔고가 언제나 진실의 원천"이라는 원칙으로 실계좌를 직접 조회해
    썼으나, 같은 티커에 여러 사이클/포지션을 동시에 운용할 수 있게 하면서(사용자
    요청) 브로커 잔고는 "이 종목 전체 합계"만 알려줄 뿐 어느 주문이 어느 논리적
    포지션 몫인지 구분하지 못하므로 포지션별 재생 방식으로 바꿨다. 대신 여러
    포지션의 재생 결과 합계를 실계좌 보유량과 주기적으로 대조해(reconcile_holdings)
    어긋나면 경고한다 - 계좌 밖에서 수동 거래가 있었거나 체결 기록 누락이 있을 때
    이 대조에서 걸러진다.

    2.2버전만 지원한다(splits=40 고정, 중간 복리 없음 - infinite_buying.py의
    VERSION_DEFAULTS["v2"]와 동일). v3/v4 자동매매는 추후 범위."""

    __tablename__ = "auto_infinite_positions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    ticker = db.Column(db.String(16), nullable=False)
    exchange = db.Column(db.String(10), nullable=False)  # meritz_api.OVERSEAS_EXCHANGES 키
    label = db.Column(db.String(60), default="")  # 같은 티커로 여러 사이클을 동시에 돌릴 때 구분용
    splits = db.Column(db.Integer, nullable=False, default=40)
    target_return_pct = db.Column(db.Float, nullable=False, default=10.0)
    seed = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 완전 자동 제출(app.py의 auto_infinite_scheduler, 2026-10-22 추가) - 기본값
    # False(안전 기본값, 새로 만든 포지션이 실수로 자동매매되지 않도록). True인
    # 포지션만 매일 한국시간 17~18시 사이에 그날 가이드 주문이 전부 실제로
    # 제출된다(반자동 수동 클릭을 대신함). auto_last_run_date로 하루 중복 실행을
    # 막고, had_error/failure_notified_date로 18시 이후 카카오 실패 알림을
    # 하루 한 번만 보낸다.
    auto_trading_enabled = db.Column(db.Boolean, nullable=False, default=False)
    auto_last_run_date = db.Column(db.String(10))
    auto_last_run_had_error = db.Column(db.Boolean, default=False)
    auto_last_failure_notified_date = db.Column(db.String(10))

    orders = db.relationship(
        "AutoInfiniteOrder", backref="position", cascade="all, delete-orphan",
        order_by="AutoInfiniteOrder.id",
    )


class AutoInfiniteOrder(db.Model):
    """AutoInfinitePosition이 실제로 메리츠에 제출한(또는 제출을 시도한) 주문 1건의
    기록 - "반자동"(사용자가 매일 가이드를 보고 승인 클릭) 흐름의 감사 로그다.
    가이드 자체는 저장하지 않는다(매번 실계좌 상태로 다시 계산) - 사용자가 실제로
    제출 버튼을 눌러 API 호출을 시도한 건만 이 테이블에 남는다.

    status: "submitted"(API 제출 성공, 체결 대기) -> "filled"/"rejected"/
    "cancelled"(체결 확인 API로 갱신) 또는 "error"(제출 자체가 실패, 체결 대상 아님)."""

    __tablename__ = "auto_infinite_orders"

    id = db.Column(db.Integer, primary_key=True)
    position_id = db.Column(db.Integer, db.ForeignKey("auto_infinite_positions.id"), nullable=False)
    cycle_no = db.Column(db.Integer)  # 표시용(제출 시점 재생 결과) - 재생 로직 자체는 이 값에 의존 안 함
    trade_date = db.Column(db.String(20), nullable=False)
    action = db.Column(db.String(4), nullable=False)  # "buy" | "sell"
    order_type = db.Column(db.String(10), nullable=False)  # "loc" | "moc" | "limit" | "market"
    label = db.Column(db.String(60), default="")
    qty = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float)  # moc는 null
    status = db.Column(db.String(10), nullable=False, default="submitted")
    broker_order_no = db.Column(db.String(40))
    filled_qty = db.Column(db.Integer)
    filled_price = db.Column(db.Float)
    raw_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "date": self.trade_date, "action": self.action,
            "orderType": self.order_type, "label": self.label, "qty": self.qty,
            "price": self.price, "status": self.status,
            "brokerOrderNo": self.broker_order_no,
            "filledQty": self.filled_qty, "filledPrice": self.filled_price,
        }


class KakaoToken(db.Model):
    """카카오톡 "나에게 보내기" API(kakao_notify.py)용 OAuth 토큰 저장소. 단일 행만
    쓴다(purpose="default" 고정) - 이 앱 소유자 한 명의 카카오 계정에만 연동한다.

    DB에 저장하는 이유: 액세스 토큰은 약 12시간, 리프레시 토큰은 약 60일마다
    갱신되는데(카카오 로그인 REST API 문서, 2026-10-22 확인) gunicorn 워커가 2개라
    모듈 전역 변수에 두면 워커마다 따로 토큰을 들고 있게 되어 서로 어긋난다
    (RULES.md R5와 같은 이유). 갱신 시 리프레시 토큰이 항상 재발급되는 건 아니다
    (남은 유효기간이 1개월 미만일 때만 새로 옴) - refresh_token 컬럼은 응답에
    새 값이 왔을 때만 덮어쓴다."""

    __tablename__ = "kakao_tokens"

    id = db.Column(db.Integer, primary_key=True)
    purpose = db.Column(db.String(20), unique=True, nullable=False, default="default")
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    access_expires_at = db.Column(db.DateTime, nullable=False)
    refresh_expires_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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


class KrFundamentalQuarter(db.Model):
    """DART 분기/반기 보고서 재무 캐시. KrFundamental(연간)과 따로 두는 이유는
    "누적 금액"이라는 성질이 완전히 다르기 때문이다.

    미너비니 SEPA는 "직전 분기 EPS·매출 증가율이 그 이전 분기보다 더 높아지는가"
    (성장 가속)를 본다. 연간 결산만으로는 이 판정이 불가능해서 분기가 필요하다.

    ## 왜 "누적"으로 저장하는가
    DART 손익 항목은 같은 보고서 안에서도 계정마다 해석이 다르다 - 실측(삼성전자
    2023 반기)에서 매출액은 thstrm_amount가 2분기 단독(60.0조)이고
    thstrm_add_amount가 상반기 누적(123.8조)인 반면, 당기순이익은 thstrm_amount에
    이미 상반기 누적(3.3조)이 들어 있고 thstrm_add_amount는 비어 있었다.
    그래서 "누적값이 있으면 그것, 없으면 당기값을 누적으로 간주"라는 한 가지
    규칙으로 통일해 항상 누적으로 저장한다. 분기 단독 실적이 필요하면 읽는 쪽에서
    인접 분기 누적값을 차분한다(2분기=반기누적-1분기, 4분기=연간-3분기누적).
    이렇게 해두면 계정별 표기 차이에 휘둘리지 않는다.

    재무상태표 항목(자본총계 등)은 시점 잔액이라 누적 개념이 없어 그대로 담는다.

    quarter: 1=1분기(11013), 2=반기(11012), 3=3분기(11014). 4분기 보고서는 따로
    없고 사업보고서(연간)가 그 역할이라 여기 담지 않는다 - KrFundamental을 쓴다.
    """

    __tablename__ = "kr_fundamentals_quarter"
    __table_args__ = (
        db.UniqueConstraint("stock_code", "bsns_year", "quarter", name="uq_kr_fund_q_stock_year_q"),
    )

    id = db.Column(db.Integer, primary_key=True)
    stock_code = db.Column(db.String(10), nullable=False, index=True)
    corp_code = db.Column(db.String(10), nullable=False)
    bsns_year = db.Column(db.String(4), nullable=False)
    quarter = db.Column(db.Integer, nullable=False)  # 1=1분기, 2=반기, 3=3분기(전부 누적)
    fs_div = db.Column(db.String(4), default="CFS")
    rcept_no = db.Column(db.String(20), default="")
    # 손익 항목 - 전부 그 사업연도 1분기부터의 "누적" 금액
    revenue_cum = db.Column(db.Float)
    operating_income_cum = db.Column(db.Float)
    net_income_cum = db.Column(db.Float)
    gross_profit_cum = db.Column(db.Float)
    # 기본주당이익(누적). DART가 직접 실어주는 값이라 과거 발행주식수 없이도
    # SEPA의 분기 EPS 성장 가속을 판정할 수 있다(dart_fetch._Q_INCOME_MAP 주석 참고).
    eps_cum = db.Column(db.Float)
    # 재무상태표 항목 - 그 시점 잔액(누적 아님)
    total_equity = db.Column(db.Float)
    total_assets = db.Column(db.Float)
    total_liabilities = db.Column(db.Float)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def rcept_date(self):
        """공시 제출일(YYYY-MM-DD). KrFundamental.rcept_date와 같은 이유로,
        백테스트는 이 날짜 이후에만 이 수치를 알 수 있었던 것으로 취급해야 한다."""
        if not self.rcept_no or len(self.rcept_no) < 8:
            return None
        d = self.rcept_no[:8]
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"


class KrDisclosure(db.Model):
    """DART 공시 목록(제목·접수일) 캐시. 미너비니 SEPA 3단계 "촉매(Catalyst)" 판정용.

    ## 왜 유형을 안 가리고 전부 담는가
    공시를 받아오는 게 비싼 작업이고(10년치 수만 건) 어떤 제목을 촉매로 볼지
    분류하는 건 공짜다. 받을 때 골라 담으면 분류 기준을 조금만 바꿔도 전부
    다시 받아야 한다. 그래서 제목(report_nm)을 원문 그대로 저장하고, 촉매냐
    아니냐는 읽는 쪽에서 판정한다(양의 촉매/음의 신호 분류를 나중에 바꿔도
    재수집이 필요 없다).

    ## 실측으로 확인한 주요 유형(2017/2020/2023/2025 표본 4,800건 기준)
    양의 촉매 후보: 단일판매ㆍ공급계약체결(505건 - 정기주총 다음으로 많다),
      타법인주식및출자증권취득결정(105), 투자판단관련주요경영사항(95),
      신규시설투자등(35), 매출액또는손익구조30%변동(15 - 어닝서프라이즈 성격)
    음의 신호 후보: 유상증자결정(43), 단기차입금증가결정(41), 전환사채(39),
      조회공시요구(57), 주권매매거래정지(22), 타인에대한채무보증결정(157),
      횡령ㆍ배임혐의발생, 회계처리기준위반 - 이 마지막 둘은 CLAUDE.md가
      "데이터가 없어 반영 못 했다"고 적어둔 관리종목/회계위반 이력에 해당한다.

    rcept_dt(접수일)가 곧 시장이 그 정보를 알게 된 날이라, 백테스트는 이 날짜
    이후에만 해당 촉매를 참조해야 한다(KrFundamental.rcept_date와 같은 원칙).
    """

    __tablename__ = "kr_disclosures"
    __table_args__ = (
        db.UniqueConstraint("rcept_no", name="uq_kr_disclosure_rcept"),
        db.Index("ix_kr_disclosure_code_date", "stock_code", "rcept_dt"),
    )

    id = db.Column(db.Integer, primary_key=True)
    stock_code = db.Column(db.String(10), index=True)
    corp_code = db.Column(db.String(10), nullable=False)
    corp_name = db.Column(db.String(128), default="")
    rcept_no = db.Column(db.String(20), nullable=False)
    rcept_dt = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD(시장이 알게 된 날)
    report_nm = db.Column(db.String(300), default="")  # 공시 제목 원문
    corp_cls = db.Column(db.String(2), default="")  # Y=유가증권, K=코스닥
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)


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
    open = db.Column(db.Float)  # 익일 시가 체결 모델용(vcp_strategy.run_vcp_backtest)
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
    donchian_high_252 = db.Column(db.Float)  # 직전 252거래일(52주) 고가(오늘 제외) - 52주 신고가 돌파 스크리닝용
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
    paper_trading_scheduler)에서 매일 한국시간 14:30에 이 계좌를 찾아 진행
    상황을 갱신한다.
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
    # 주소로 요약 메일을 보내는 기능(app.py의 paper_trading_scheduler)용.
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
    # rank_exit_top_n/rank_exit_persist_days(JPEX_V11_PARAMS, 2026-10-13 신규)용 -
    # vcp_strategy.run_vcp_backtest의 pos["rankMissStreak"]와 동일 개념이지만,
    # 모의투자는 하루에 한 번만 깨어나므로(14:30) 이 값을 DB에 영속화해야
    # "N일 연속 순위이탈"을 여러 번의 실행에 걸쳐 추적할 수 있다.
    rank_miss_streak = db.Column(db.Integer, nullable=False, default=0)
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


class PersonaDebateJob(db.Model):
    """투자 대가 페르소나 토론 생성 작업. Anthropic API 호출이 몇 초~수십 초
    걸릴 수 있어 QuantBacktestJob과 같은 이유로 요청-응답 안에서 바로 처리하지
    않고 백그라운드 스레드로 돌린다. persona_a/persona_b는 investor_debate.py의
    PERSONAS 딕셔너리 키(서버가 화이트리스트로 검증한 값만 저장).
    """

    __tablename__ = "persona_debate_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    persona_a = db.Column(db.String(32), nullable=False)
    persona_b = db.Column(db.String(32), nullable=False)
    topic = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="pending")  # pending|running|done|error
    result_json = db.Column(db.Text)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpsHeartbeat(db.Model):
    """백그라운드 스레드 6종의 7개 컴포넌트(alert_checker/kr_quant_price_cache_refresher/
    trend_screen_refresher_KR/trend_screen_refresher_US/us_fundamentals_refresher/dart_fundamentals_refresher/
    paper_trading_scheduler)이 "이번 주기 끝까지 도달했다"는 흔적을 남기는 범용
    헬스체크 테이블(2026-10-16 DevOps 대시보드용 신규) - 기존에는 이 흔적이
    전혀 없거나(alert_checker) 기능 테이블의 updated_at에 암묵적으로만 있어서
    (trend_screen_refresher), "스레드가 죽었다"가 아니라 "네트워크 호출이
    타임아웃 없이 무한정 블로킹돼 멈춰있다"는 조용한 장애(실제로 trend_screen_
    refresher가 29시간 멈췄던 사고)를 아무도 알아챌 방법이 없었다. 매 주기
    성공/실패와 무관하게 "주기가 끝났다"를 기록하면, 대시보드가 last_success_at
    경과 시간만으로 각 컴포넌트의 건강도를 판정할 수 있다(스레드 생존 여부를
    직접 확인할 방법은 Python에 없으므로 이게 사실상 유일한 신호).

    component는 위 7개 이름 문자열을 그대로 쓴다(trend_screen_refresher는 KR/US가
    독립적으로 실패할 수 있어 시장별로 행을 분리했다 - 코드와 1:1 매핑,
    화이트리스트 성격 - 새 컴포넌트를 추가할 때만 여기 주석에 이름을 더한다).
    """

    __tablename__ = "ops_heartbeats"

    id = db.Column(db.Integer, primary_key=True)
    component = db.Column(db.String(64), unique=True, nullable=False)
    last_success_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    last_error_at = db.Column(db.DateTime)
    run_count = db.Column(db.Integer, nullable=False, default=0)
    error_count = db.Column(db.Integer, nullable=False, default=0)


class AgentActivity(db.Model):
    """OpsHeartbeat(위)와 짝을 이루는 테이블이지만 성격이 정반대다 - OpsHeartbeat는
    24시간 자동으로 도는 백그라운드 스레드가 스스로 남기는 흔적인 반면, 이
    테이블은 "일하는 주체"인 Claude(이 프로젝트에서 작업하는 에이전트) 본인이
    기획/개발/QA/배포/총괄 단계를 시작·종료할 때마다 /api/ops/agent-status로
    직접 보고해야만 채워진다(2026-10-17, 사용자가 "운영 대시보드에서 에이전트가
    실시간으로 어떻게 동작하는지 보고 싶다"고 요청해 추가). 백그라운드 프로세스가
    없으므로 Claude가 세션 중간에 끊기거나 active=True로 보고한 채 끄는 걸
    깜빡하면 영영 "작업중"으로 멈춰 보일 수 있다 - 그래서 읽는 쪽(app.py의
    ops_status)이 updated_at이 너무 오래된 active=True는 신뢰하지 않고 대기로
    간주하는 안전장치를 둔다(정확한 시간은 app.py 주석 참고).

    component는 "plan"(기획)/"dev"(개발)/"qa"(테스트)/"deploy"(배포)/"lead"
    (총괄) 5종 고정 화이트리스트 - app.py의 ops_agent_status가 강제한다.
    """

    __tablename__ = "agent_activity"

    id = db.Column(db.Integer, primary_key=True)
    component = db.Column(db.String(20), unique=True, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=False)
    detail = db.Column(db.Text)
    passed = db.Column(db.Boolean)  # qa 전용 - 마지막 검증 통과 여부
    live = db.Column(db.Boolean)  # deploy 전용 - 마지막 배포 사이트 생존 확인 여부
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
