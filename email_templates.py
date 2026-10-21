# -*- coding: utf-8 -*-
"""모의투자/무한매수법 알림 메일의 공용 HTML 템플릿. 기존엔 둘 다 줄바꿈뿐인
plain text였는데, 사용자가 레퍼런스 이미지(카드형 다크 테마, 매수/매도 색상
배지, 표 형태 보유종목)를 보여주며 "이렇게 이미지처럼 깔끔하게" 요청해서
만들었다. 이메일 클라이언트는 외부 스타일시트/flex/grid를 신뢰할 수 없게
지원하므로(특히 Gmail 앱) 테이블 레이아웃 + 인라인 style만 쓴다. Resend는
text/html을 함께 받을 수 있어, 항상 html과 함께 그 html에서 태그만 뺀
plain text도 같이 보낸다(html 미지원 클라이언트 폴백 + 스팸 점수 완화).
"""
import html as _html

BG = "#0f1117"
CARD_BG = "#1a1d2b"
CARD_BORDER = "#2a2d42"
TEXT = "#e8e9f3"
MUTED = "#8b8fa3"
BUY_COLOR = "#22c55e"
SELL_COLOR = "#f43f5e"
PYRAMID_COLOR = "#38bdf8"
ACCENT = "#a78bfa"


def _esc(s):
    return _html.escape(str(s if s is not None else ""))


def _badge(text, color):
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:999px;'
        f'font-size:12px;font-weight:700;color:#0f1117;background:{color};">{_esc(text)}</span>'
    )


def _section_title(text, color=ACCENT):
    return (
        f'<tr><td style="padding:22px 20px 8px;font-size:14px;font-weight:700;color:{color};">'
        f'{_esc(text)}</td></tr>'
    )


def _table_header(cols):
    cells = "".join(
        f'<th style="text-align:left;padding:6px 8px;font-size:11px;color:{MUTED};'
        f'border-bottom:1px solid {CARD_BORDER};font-weight:600;">{_esc(c)}</th>'
        for c in cols
    )
    return f'<tr>{cells}</tr>'


def _table_row(cells, strong_idx=None):
    tds = []
    for i, c in enumerate(cells):
        weight = "700" if strong_idx is not None and i in strong_idx else "400"
        tds.append(
            f'<td style="padding:8px;font-size:13px;color:{TEXT};font-weight:{weight};'
            f'border-bottom:1px solid {CARD_BORDER};white-space:nowrap;">{c}</td>'
        )
    return f'<tr>{"".join(tds)}</tr>'


def _pnl_color(pct):
    if pct is None:
        return MUTED
    return BUY_COLOR if pct >= 0 else SELL_COLOR


def _pnl_text(pct):
    if pct is None:
        return "-"
    return f"{pct:+.2f}%"


def wrap_email(header_label, header_color, title, subtitle, body_rows_html):
    """모든 알림 메일의 공통 외곽(헤더 배지 + 카드). body_rows_html은 <tr> 문자열들의
    리스트(가운데 큰 <table> 안에 그대로 들어간다)."""
    rows = "".join(body_rows_html)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
       style="width:560px;max-width:560px;background:{CARD_BG};border:1px solid {CARD_BORDER};border-radius:14px;overflow:hidden;">
<tr><td style="padding:18px 20px;background:linear-gradient(135deg,{header_color}33,{header_color}11);
       border-bottom:1px solid {CARD_BORDER};">
  <div style="font-size:12px;font-weight:700;color:{header_color};letter-spacing:.04em;">{_esc(header_label)}</div>
  <div style="font-size:18px;font-weight:800;color:{TEXT};margin-top:4px;">{_esc(title)}</div>
  <div style="font-size:12px;color:{MUTED};margin-top:2px;">{_esc(subtitle)}</div>
</td></tr>
<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>
<tr><td style="padding:16px 20px;border-top:1px solid {CARD_BORDER};">
  <div style="font-size:11px;color:{MUTED};line-height:1.6;">JH-Trader 자동 알림 메일입니다. 계산 기준가와 실제 체결가는 다를 수 있습니다.</div>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


# ─── 모의투자 매매 알림 ──────────────────────────────────────────────────────
_EXIT_REASON_LABEL_FALLBACK = {}


def build_paper_trading_email(strategy_label, target_date, buys, pyramids, sells, holdings,
                               exit_reason_label=None):
    """paper_trading.py._send_one_trade_alert용. buys/pyramids/sells는 PaperPosition/
    PaperTrade ORM 객체 리스트, holdings는 현재 보유 포지션 전체(오늘 거래 여부와
    무관) - dict 리스트 [{"code","name","entry_price","current_price"(optional),
    "stop_price","bars_held"}]. exit_reason_label은 paper_trading.py의
    _ALERT_EXIT_REASON_LABEL을 그대로 받는다(여기서 새로 정의하지 않음 - 한 곳에서만
    관리).
    """
    exit_label = exit_reason_label or _EXIT_REASON_LABEL_FALLBACK
    rows = []
    has_activity = bool(buys or pyramids or sells)

    if has_activity:
        rows.append(_section_title("오늘 매매하실 종목이 있습니다", ACCENT))
        rows.append(
            f'<tr><td style="padding:0 20px 10px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{_table_header(["구분", "종목", "수량", "가격"])}'
        )
        body = ""
        for p in buys:
            body += _table_row([
                _badge("매수", BUY_COLOR), f'{_esc(p.name)}<br><span style="color:{MUTED};font-size:11px;">{_esc(p.code)}</span>',
                f'{p.shares:,}주', f'{round(p.entry_price):,}원',
            ])
        for p in pyramids:
            qty = p.last_pyramid_shares or 0
            body += _table_row([
                _badge("추가매수", PYRAMID_COLOR), f'{_esc(p.name)}<br><span style="color:{MUTED};font-size:11px;">{_esc(p.code)}</span>',
                f'{qty:,}주', f'{round(p.last_entry_price):,}원',
            ])
        for t in sells:
            reason = exit_label.get(t.exit_reason, t.exit_reason)
            pnl_color = _pnl_color(t.pnl_pct)
            body += _table_row([
                _badge("매도", SELL_COLOR), f'{_esc(t.name)}<br><span style="color:{MUTED};font-size:11px;">{_esc(t.code)}</span>',
                f'{t.shares:,}주',
                f'{round(t.exit_price):,}원<br><span style="color:{pnl_color};font-size:11px;font-weight:700;">{_pnl_text(t.pnl_pct)} · {_esc(reason)}</span>',
            ])
        rows.append(body + '</table></td></tr>')
    else:
        rows.append(
            f'<tr><td style="padding:22px 20px;font-size:13px;color:{MUTED};">'
            f'{_esc(target_date)} 기준, 오늘 실행할 매매가 없습니다.</td></tr>'
        )

    if holdings:
        rows.append(_section_title(f"보유 종목 ({len(holdings)})", "#38bdf8"))
        has_price = any(h.get("current_price") for h in holdings)
        cols = ["종목", "진입가", "보유일", "손절가"]
        if has_price:
            cols = ["종목", "진입가", "현재가", "손익", "손절가"]
        rows.append(
            f'<tr><td style="padding:0 20px 16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{_table_header(cols)}'
        )
        body = ""
        for h in holdings:
            name_cell = f'{_esc(h["name"])}<br><span style="color:{MUTED};font-size:11px;">{_esc(h["code"])}</span>'
            entry_cell = f'{round(h["entry_price"]):,}원'
            stop_cell = f'{round(h["stop_price"]):,}원'
            if has_price:
                cur = h.get("current_price")
                if cur:
                    pnl = (cur / h["entry_price"] - 1) * 100
                    cur_cell = f'{round(cur):,}원'
                    pnl_cell = f'<span style="color:{_pnl_color(pnl)};font-weight:700;">{_pnl_text(pnl)}</span>'
                else:
                    cur_cell, pnl_cell = "-", "-"
                body += _table_row([name_cell, entry_cell, cur_cell, pnl_cell, stop_cell])
            else:
                body += _table_row([name_cell, entry_cell, f'{h.get("bars_held", 0)}일', stop_cell])
        rows.append(body + '</table></td></tr>')

    html = wrap_email(
        "JH-TRADER · 모의투자", ACCENT, f"{strategy_label} 오늘의 매매",
        f"{target_date} 14:30 실시간가 기준", rows,
    )

    # plain text 폴백 (기존 _send_one_trade_alert 로직과 동일한 요약)
    text_lines = [f"{target_date} 14:30 실시간가 기준, {strategy_label} 전략의 오늘 매매 내역입니다."]
    if has_activity:
        if buys:
            text_lines.append("\n[신규 매수]")
            text_lines += [f"- {p.name}({p.code}): {p.shares:,}주 @ {round(p.entry_price):,}원" for p in buys]
        if pyramids:
            text_lines.append("\n[추가 매수(피라미딩)]")
            text_lines += [
                f"- {p.name}({p.code}): {(p.last_pyramid_shares or 0):,}주 추가 @ {round(p.last_entry_price):,}원"
                for p in pyramids
            ]
        if sells:
            text_lines.append("\n[매도]")
            text_lines += [
                f"- {t.name}({t.code}): {t.shares:,}주 @ {round(t.exit_price):,}원 "
                f"(사유: {exit_label.get(t.exit_reason, t.exit_reason)}, 손익 {t.pnl_pct:+.1f}%)"
                for t in sells
            ]
    else:
        text_lines.append("오늘 실행할 매매가 없습니다.")
    text_lines.append("\n※ 위 가격은 계산 기준가이며, 실제 체결가는 다를 수 있습니다.")
    text = "\n".join(text_lines)
    return html, text


# ─── 무한매수법 주문 제출 알림 ────────────────────────────────────────────────
def build_auto_infinite_order_email(position, action, order_type, qty, price, state=None):
    """auto_infinite.py._notify_order_submitted용. state는 replay_position_state가
    돌려주는 PositionState(이미 주문 전에 계산해둔 걸 재사용 - 이 함수가 새로
    API를 호출하지 않는다)."""
    action_ko = "매도" if action == "sell" else "매수"
    color = SELL_COLOR if action == "sell" else BUY_COLOR
    price_text = f"${price:,.2f}" if price else "시장가(장마감)"
    label_suffix = f" · {position.label}" if position.label else ""

    rows = [
        f'<tr><td style="padding:20px;">'
        f'{_badge(action_ko, color)} '
        f'<span style="font-size:16px;font-weight:800;color:{TEXT};margin-left:8px;">{_esc(position.ticker)}{_esc(label_suffix)}</span>'
        f'</td></tr>',
        f'<tr><td style="padding:0 20px 16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + _table_row(["수량", f'{qty}주'])
        + _table_row(["주문구분", order_type.upper()])
        + _table_row(["가격", price_text])
        + '</table></td></tr>',
    ]

    if state is not None:
        cycle_day = getattr(state, "cycle_day", None)
        cycle_text = f'{state.cycle_no}회차' + (f' · {cycle_day}일차' if cycle_day else '')
        rows.append(_section_title("현재 포지션 현황", "#38bdf8"))
        rows.append(
            f'<tr><td style="padding:0 20px 16px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            + _table_row(["사이클", cycle_text])
            + _table_row(["보유수량", f'{state.holding_qty}주'])
            + _table_row(["평단가", f'${state.avg_price:,.2f}' if state.avg_price else "-"])
            + _table_row(["T값", f'{state.t_value:.2f}' if getattr(state, "t_value", None) is not None else "-"])
            + '</table></td></tr>'
        )

    html = wrap_email("JH-TRADER · 무한매수법", color, f"{position.ticker} {action_ko} 주문 제출",
                       "실제 계좌에 제출된 주문입니다", rows)

    text = (
        f"{position.ticker}{label_suffix} 포지션에 실제 주문이 제출됐습니다.\n\n"
        f"구분: {action_ko}\n수량: {qty}주\n주문구분: {order_type.upper()}\n가격: {price_text}\n\n"
        f"자세한 내용(체결 여부 포함)은 앱의 무한매수 탭에서 확인하세요."
    )
    return html, text


WARN_COLOR = "#f59e0b"


def build_auto_infinite_failure_email(position, reason):
    """app.py._send_auto_infinite_failure_alerts용 - 실제 매매가 아니라 "오늘
    자동제출이 실패했다"는 경고 알림이라 거래 카드 대신 경고 배지 하나만 쓴다."""
    label_suffix = f" · {position.label}" if position.label else ""
    rows = [
        f'<tr><td style="padding:20px;">'
        f'{_badge("확인 필요", WARN_COLOR)} '
        f'<span style="font-size:16px;font-weight:800;color:{TEXT};margin-left:8px;">{_esc(position.ticker)}{_esc(label_suffix)}</span>'
        f'</td></tr>',
        f'<tr><td style="padding:0 20px 20px;font-size:13px;color:{TEXT};line-height:1.6;">{_esc(reason)}</td></tr>',
    ]
    html = wrap_email("JH-TRADER · 무한매수법", WARN_COLOR, f"{position.ticker} 자동매매 실패",
                       "앱에서 주문 내역/연결 상태를 확인하세요", rows)
    text = (
        f"{position.ticker}{label_suffix} - {reason}\n\n"
        f"앱의 무한매수 탭에서 주문 내역/연결 상태를 직접 확인해주세요."
    )
    return html, text
