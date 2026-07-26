"""무한매수법(라오어 v2.2/v3.0/v4.0) 공통 계산 모듈.

백테스트(backtest.py, 일봉 시세로 매일 체결 여부를 시뮬레이션)와 실전 현황
(live_tracker.py, 사용자가 입력한 실제 매매 기록을 재생)이 이 모듈 하나를
함께 참조한다. 두 곳 모두 같은 `PositionState`로 매수/매도를 적용하고 같은
`build_guide()`로 "오늘의 매매 가이드"를 계산하므로, 공식을 고칠 일이 생기면
이 파일 하나만 고치면 된다.

T값 공식 (실제 사용자 앱 화면의 시드/1회투자금/T값/Star값으로 검증됨)
----------------------------------------------------------------------
- 1회 매수금 = 이번 사이클 시드 / 분할수(N)  (사이클 동안 고정, v3/v4는 중간복리
  보너스가 더해진다)
- 사용한 시드(순 투입액) = 이번 사이클 누적 매수액 − 누적 매도액
    · 매도를 하면 회수한 자금만큼 곧바로 줄어든다(매도와 무관하게 누적되는
      단순 매수 총액이 아니다).
- T값 = 사용한 시드 / 1회 매수금
- ☆%(임계값) = 목표수익률 × (1 − 2T/N)
    · 원문의 SOXL 예시(N=40, 목표 12%)는 "12 − T×0.6"이고, 분할수가 a로 바뀌면
      "12 − T×0.6×(40/a)"가 된다는 것이 원문에 명시되어 있다. 이는 정확히
      목표수익률×(1−2T/a)와 동치이므로(T=0→+목표%, T=N→−목표%) 그대로 일반화했다.
- 손절모드(분할소진) 경계: 원문 "39<T≤40"(40분할 기준)을 T > 분할수−1로 일반화.

매수·매도 규칙 (원문 "매수편"/"매도편")
---------------------------------------
- 전반전(T<N/2): 1회 매수금의 절반은 "평단가(0%)"에, 절반은 "평단×(1+☆%)"에
  각각 LOC 매수.
- 후반전(T≥N/2): 1회 매수금 전체를 "평단×(1+☆%)"에 LOC 매수.
- 매도(전후반 공통): 누적수량의 1/4은 "평단×(1+☆%)"에 LOC 매도, 나머지 3/4은
  "평단×(1+목표수익률%)"에 지정가 매도.
- 손절모드: 매수 대신 보유수량의 1/4을 무조건(MOC) 매도한다. 매도로 T가 다시
  N−1 이하로 내려가면 정상 매수·매도로 복귀한다(자기 조정적).

버전별 차이
-----------
- v2.2: 40분할 기본. 사이클이 완전히 끝나야만(전량 매도) 복리 반영.
- v3.0/v4.0: 쿼터(1/4) 매도 등으로 중간에 이익이 나면 그 이익의 1/40을 즉시
  이후 1회 매수금에 더해 반영(복리를 더 빨리 태움). 손실이면 1회 매수금 유지.
"""

import math

VERSION_DEFAULTS = {
    "v2": {"splits": 40, "compound_mid_cycle": False},
    "v3": {"splits": 20, "compound_mid_cycle": True},
    "v4": {"splits": 20, "compound_mid_cycle": True},
}

# 종목별 목표수익률 고정값 (라오어 SOXL V2.2 원문 기준 12%, KORU는 변동성이 커 20%,
# 그 외 종목은 TQQQ 등 기존 무한매수법 표준값인 10%)
TICKER_TARGET_DEFAULTS = {
    "SOXL": 12.0,
    "KORU": 20.0,
}
DEFAULT_TARGET_RETURN = 10.0


def get_version_defaults(version):
    return VERSION_DEFAULTS.get(version, VERSION_DEFAULTS["v2"])


def get_target_default(ticker):
    return TICKER_TARGET_DEFAULTS.get((ticker or "").upper(), DEFAULT_TARGET_RETURN)


def threshold_pct(target_return_pct, splits, t_value):
    """☆%(Star값) = 목표수익률 × (1 − 2T/분할수)."""
    return target_return_pct * (1 - 2 * t_value / splits) if splits else 0.0


class PositionState:
    """무한매수법 한 포지션의 상태(평단가·보유수량·T값·사이클)를 담는 값 객체."""

    def __init__(self, seed, splits, target_return_pct, compound_mid_cycle=False):
        self.seed = seed
        self.splits = splits
        self.target_return_pct = target_return_pct
        self.compound_mid_cycle = compound_mid_cycle

        self.cash = seed
        self.cycle_seed = seed
        self.bonus_per_split = 0.0
        self.net_deployed = 0.0
        self.holding_qty = 0
        self.avg_price = 0.0
        self.t_value = 0.0
        self.cycle_no = 1

    @property
    def split_amount(self):
        return (self.cycle_seed / self.splits + self.bonus_per_split) if self.splits else 0.0

    @property
    def star_pct(self):
        return threshold_pct(self.target_return_pct, self.splits, self.t_value)

    @property
    def loss_cut_mode(self):
        return self.holding_qty > 0 and self.t_value > (self.splits - 1)

    def start_cycle(self, cash_override=None):
        """보유수량이 0일 때(새 사이클 1일차) 1회 매수금의 기준(cycle_seed)을 고정한다."""
        self.cycle_seed = self.cash if cash_override is None else cash_override
        self.bonus_per_split = 0.0
        self.net_deployed = 0.0

    def apply_buy(self, price, qty):
        spend = price * qty
        self.cash -= spend
        self.avg_price = (self.avg_price * self.holding_qty + spend) / (self.holding_qty + qty)
        self.holding_qty += qty
        self.net_deployed += spend
        self._recalc_t()
        return spend

    def apply_sell(self, price, qty):
        """qty를 price에 매도 처리하고 (매도금액, 실현손익)을 반환한다."""
        proceeds = price * qty
        cost_of_sold = qty * self.avg_price
        self.cash += proceeds
        self.holding_qty -= qty
        self.net_deployed -= proceeds
        profit = proceeds - cost_of_sold

        if self.holding_qty <= 0:
            self.holding_qty = 0
            self.cycle_no += 1
            self.avg_price = 0.0
            self.net_deployed = 0.0
            self.t_value = 0.0
        else:
            if self.compound_mid_cycle and profit > 0:
                self.bonus_per_split += profit / 40
            self._recalc_t()

        return proceeds, profit

    def _recalc_t(self):
        amt = self.split_amount
        self.t_value = (self.net_deployed / amt) if (amt > 0 and self.holding_qty > 0) else 0.0


def build_guide(state):
    """현재 PositionState를 바탕으로 "오늘의 매매 가이드"(다음 주문가·수량)를 계산한다."""
    splits = state.splits
    target = state.target_return_pct
    avg_price = state.avg_price
    t_value = state.t_value
    holding_qty = state.holding_qty

    if holding_qty == 0:
        return {
            "type": "start", "action": "buy", "orderType": "MOC",
            "note": "보유 중인 수량이 없습니다 — 새 사이클 1일차(원금/분할수 만큼 종가 매수)를 시작하세요",
        }

    if state.loss_cut_mode:
        qty_moc = math.floor(holding_qty * 0.25)
        return {
            "type": "loss_cut_moc_sell", "action": "sell", "orderType": "MOC",
            "qty": qty_moc,
            "note": (
                f"분할을 모두 소진했습니다(T={t_value:.2f}/{splits}) — "
                f"보유수량의 1/4인 {qty_moc}주를 오늘 종가로 무조건(MOC) 매도하는 것을 검토하세요"
            ),
            "targetSellPrice": round(avg_price * (1 + target / 100), 4),
        }

    star = threshold_pct(target, splits, t_value)
    quarter_sell_price = round(avg_price * (1 + star / 100), 4)
    target_sell_price = round(avg_price * (1 + target / 100), 4)
    half_point = splits / 2

    if t_value < half_point:
        buy_price_a = round(avg_price, 4)
        buy_price_b = round(avg_price * (1 + star / 100), 4)
        return {
            "type": "normal_buy_dual", "action": "buy", "orderType": "LOC",
            "buyPriceA": buy_price_a, "buyPriceB": buy_price_b,
            "quarterSellPrice": quarter_sell_price, "targetSellPrice": target_sell_price,
            "note": (
                f"전반전(T={t_value:.2f}/{splits}) — 1회 매수금의 절반은 평단가 ${buy_price_a} 이하, "
                f"나머지 절반은 ${buy_price_b} 이하로 종가가 마감되면 매수(LOC)하세요"
            ),
        }

    buy_price = round(avg_price * (1 + star / 100), 4)
    return {
        "type": "normal_buy_single", "action": "buy", "orderType": "LOC",
        "buyPrice": buy_price,
        "quarterSellPrice": quarter_sell_price, "targetSellPrice": target_sell_price,
        "note": (
            f"후반전(T={t_value:.2f}/{splits}) — 1회 매수금 전액을 ${buy_price} 이하로 "
            f"종가가 마감되면 매수(LOC)하세요"
        ),
    }
