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
    """무한매수법 한 포지션의 상태(평단가·보유수량·T값·사이클·쿼터손절모드)를 담는 값 객체.

    quarter_cut_round: 쿼터손절모드(T>분할수-1) 진행 단계.
      None = 정상모드(전반전/후반전). 0 = 방금 모드에 진입했거나 10회 매수가 끝나
      MOC 1/4 강제매도를 해야 하는 단계. 1~10 = 그 강제매도 이후 재매수 배치의
      몇 번째 회차인지.
    quarter_cut_split_amount: 그 재매수 배치에서 쓰는 1회 매수금(원문: "(매수후
      남은 자금 + 기존수익금)/10, 단 기존 1회매수금보다 많게 하지는 않음").
    전부 apply_buy/apply_sell 안에서 T값 변화에 따라 자동으로 전이한다(원문의
    "쿼터매도 성공 시 후반전 복귀", "10회째 MOC가 우연히 좋은 가격이면 복귀"를
    모두 "매도 후 T가 분할수-1 이하로 내려가면 이탈"이라는 하나의 규칙으로
    일반화했다 - 세 경우 모두 결과적으로 같은 조건이다)."""

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
        self.quarter_cut_round = None
        self.quarter_cut_split_amount = 0.0

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

        if self.quarter_cut_round is None:
            if self.t_value > self.splits - 1:
                # 새로 쿼터손절모드 진입 - 0단계(MOC 1/4 강제매도 대기)부터 시작
                self.quarter_cut_round = 0
        elif self.quarter_cut_round >= 1:
            # 재매수 배치 중 매수 1건 체결 = 회차 +1. 10회를 넘기면 다시 MOC
            # 강제매도 단계(0)로 돌아간다(원문: "10회 매수가 끝난 직후에는... MOC 매도").
            self.quarter_cut_round += 1
            if self.quarter_cut_round > 10:
                self.quarter_cut_round = 0
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
            self.quarter_cut_round = None
            self.quarter_cut_split_amount = 0.0
        else:
            if self.compound_mid_cycle and profit > 0:
                self.bonus_per_split += profit / 40
            self._recalc_t()

            if self.t_value <= self.splits - 1:
                # 이 매도로 T가 정상범위로 복귀 - 쿼터손절모드 이탈(후반전 복귀).
                # 원문의 "-목표% LOC 매도 성공" / "10회째 MOC가 우연히 -목표%
                # 안쪽" 두 경우 모두 이 조건으로 수렴한다.
                self.quarter_cut_round = None
                self.quarter_cut_split_amount = 0.0
            elif self.quarter_cut_round == 0:
                # 방금 그 MOC 1/4 강제매도가 체결됨 - 남은 자금(=이 시점의 cash,
                # 실현손익이 이미 반영된 값)으로 10회 배치용 1회 매수금을 재계산하고
                # 1회차로 진입한다. 원문: "기존 1회매수금보다 많게 하지는 않습니다."
                self.quarter_cut_split_amount = min(self.split_amount, self.cash / 10) if self.cash > 0 else 0.0
                self.quarter_cut_round = 1

        return proceeds, profit

    def _recalc_t(self):
        amt = self.split_amount
        self.t_value = (self.net_deployed / amt) if (amt > 0 and self.holding_qty > 0) else 0.0


def build_guide(state):
    """현재 PositionState를 바탕으로 "오늘의 매매 가이드"(그날 걸어둘 주문 목록)를 계산한다.

    실전에서는 매수·매도 주문을 매일 동시에 미리 걸어두고(원문: "무한매수법은
    매수매도를 매일 미리 거는 매매법") 그중 조건을 만족하는 주문만 체결되므로,
    보유 중이고 손절모드가 아니면 매수 주문(전반전 1~2개)과 매도 주문(쿼터 LOC +
    목표 지정가) 두 종류를 항상 함께 반환한다. 매수 지정가에는 매도가와 정확히
    겹치지 않도록 원문의 -$0.01 보정을 적용한다.
    """
    splits = state.splits
    target = state.target_return_pct
    avg_price = state.avg_price
    t_value = state.t_value
    holding_qty = state.holding_qty

    if holding_qty == 0:
        # 원문의 1일차 매수 기준("+목표% LOC")이 실제로 어느 가격 대비인지 불명확하고
        # (라오어 카페 원문 자체가 모호함, 2026-10-21 확인), 애초에 새 사이클을 시작할지
        # 말지·얼마로 시작할지는 사용자가 정할 문제이므로 시스템이 추측해 주문을 만들지
        # 않는다 - 사용자가 수량/가격을 직접 입력해 제출하면 거기서부터 정상모드가
        # 자동으로 이어진다.
        return {
            "holding": False, "lossCutMode": False, "quarterCutRound": None,
            "orders": [],
            "note": "보유 중인 수량이 없습니다 — 새 사이클 1일차 매수 수량·가격을 직접 정해 기록하세요",
        }

    if state.loss_cut_mode and state.quarter_cut_round == 0:
        qty_moc = math.floor(holding_qty * 0.25)
        return {
            "holding": True, "lossCutMode": True, "quarterCutRound": 0,
            "orders": [{"action": "sell", "orderType": "MOC", "label": "Stop-Loss Forced Sell (1/4)", "price": None, "qty": qty_moc}],
            "note": (
                f"분할을 모두 소진했습니다(T={t_value:.2f}/{splits}) — 쿼터손절모드 진입: "
                f"보유수량의 1/4인 {qty_moc}주를 오늘 종가로 무조건(MOC) 매도하세요. "
                f"체결되면 남은 자금으로 10회 재매수 배치가 자동으로 시작됩니다"
            ),
        }

    if state.loss_cut_mode:
        # 쿼터손절모드 1~10회차: 매수는 평단대비 -목표% 고정 LOC(T에 따라 안 변함),
        # 매도는 같은 자리 1/4 LOC(체결되면 후반전으로 자동 복귀) + 나머지 3/4는
        # 평소와 같은 +목표% 지정가. 원문: "매수는 -12% LOC 매수로 쭉 가면 됩니다 /
        # 누적수량의 1/4를 -12% LOC 매도... 나머지는 12% 지정가매도".
        cut_pct = -target
        buy_price = round(avg_price * (1 + cut_pct / 100) - 0.01, 4)
        split_amt = state.quarter_cut_split_amount
        qty_quarter = math.floor(holding_qty * 0.25)
        qty_target = holding_qty - qty_quarter
        return {
            "holding": True, "lossCutMode": True, "quarterCutRound": state.quarter_cut_round,
            "orders": [
                {
                    "action": "buy", "orderType": "LOC", "label": f"Quarter-Cut Buy ({state.quarter_cut_round}/10)",
                    "pct": round(cut_pct, 2), "price": buy_price,
                    "qty": math.floor(split_amt / buy_price) if buy_price > 0 else 0,
                },
                {
                    "action": "sell", "orderType": "LOC", "label": "Quarter-Cut Exit Sell (1/4)",
                    "pct": round(cut_pct, 2), "price": round(avg_price * (1 + cut_pct / 100), 4), "qty": qty_quarter,
                },
                {
                    "action": "sell", "orderType": "LIMIT", "label": "Sell (Target 3/4)",
                    "pct": target, "price": round(avg_price * (1 + target / 100), 4), "qty": qty_target,
                },
            ],
            "note": (
                f"쿼터손절모드 {state.quarter_cut_round}/10회차(T={t_value:.2f}) — 매수·매도를 모두 "
                f"평단대비 {cut_pct:.1f}%에 걸고, {cut_pct:.1f}% 매도(1/4)가 체결되면 자동으로 "
                f"후반전 모드로 복귀합니다. 10회를 다 채우면 다시 1/4 MOC 강제매도부터 반복됩니다"
            ),
        }

    star = threshold_pct(target, splits, t_value)
    half_point = splits / 2
    order_amt_half = min(state.split_amount / 2, state.cash)
    order_amt_full = min(state.split_amount, state.cash)

    orders = []
    if t_value < half_point:
        # -0.01 보정은 ☆%(임계값) 매수점에만 적용한다 - 그 지점이 쿼터 LOC 매도가와
        # 겹치기 때문(원문: "☆% 지점매수점에 -0.01달러를 합니다"). 평단(0%) 매수점은
        # 어느 매도 가격과도 겹치지 않으므로 보정이 필요 없다 - 둘 다에 적용하던 것은
        # 버그였다(2026-10-21, 실계좌 화면과 대조해 발견).
        buy_price_a = round(avg_price, 4)
        buy_price_b = round(avg_price * (1 + star / 100) - 0.01, 4)
        orders.append({
            "action": "buy", "orderType": "LOC", "label": "Buy (Avg Price)", "pct": 0.0,
            "price": buy_price_a, "qty": math.floor(order_amt_half / buy_price_a) if buy_price_a > 0 else 0,
        })
        orders.append({
            "action": "buy", "orderType": "LOC", "label": "Buy (Threshold)", "pct": round(star, 2),
            "price": buy_price_b, "qty": math.floor(order_amt_half / buy_price_b) if buy_price_b > 0 else 0,
        })
    else:
        buy_price = round(avg_price * (1 + star / 100) - 0.01, 4)
        orders.append({
            "action": "buy", "orderType": "LOC", "label": "Buy (Threshold)", "pct": round(star, 2),
            "price": buy_price, "qty": math.floor(order_amt_full / buy_price) if buy_price > 0 else 0,
        })

    qty_quarter = math.floor(holding_qty * 0.25)
    qty_target = holding_qty - qty_quarter
    orders.append({
        "action": "sell", "orderType": "LOC", "label": "Sell (Quarter 1/4)", "pct": round(star, 2),
        "price": round(avg_price * (1 + star / 100), 4), "qty": qty_quarter,
    })
    orders.append({
        "action": "sell", "orderType": "LIMIT", "label": "Sell (Target 3/4)", "pct": target,
        "price": round(avg_price * (1 + target / 100), 4), "qty": qty_target,
    })

    phase = "전반전" if t_value < half_point else "후반전"
    return {
        "holding": True, "lossCutMode": False, "quarterCutRound": None,
        "orders": orders,
        "note": (
            f"{phase}(T={t_value:.2f}/{splits}, ☆%={star:.2f}%) — 매수·매도 주문을 표에 나온 가격으로 "
            f"동시에 걸어두고, 그날 종가가 어느 조건을 만족하는지에 따라 체결됩니다"
        ),
    }
