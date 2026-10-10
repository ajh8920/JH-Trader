# -*- coding: utf-8 -*-
"""미국 유니버스(us_stocks.json, 606종목)의 현재 상장주식수를 yfinance에서 받아
캐시한다 - JPEX를 미국 시장에 적용해보는 실험(research/jpex/
test_jpex_us_lite.py)에서 시가총액 필터(min_market_cap)에 쓸 shares_map이
필요한데, 국내처럼 과거 시점별 상장주식수 시계열이 없어 "현재" 값을 전체
구간에 고정 근사로 쓴다(주식분할은 가격 데이터가 이미 분할조정돼 있어
영향이 작지만, 대규모 바이백/증자가 있었던 종목은 과거 시가총액이 왜곡될 수
있다는 제약을 감안할 것 - 이 실험은 "미국 시장 적용 가능성"을 보는 1차
근사 테스트다).

산출(결과물): research/jpex/us_shares_cache.json에 {code: shares} 저장.

사용법: python -m research.jpex.build_us_shares_map
"""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import yfinance as yf

with open(PROJECT_DIR / "us_stocks.json", encoding="utf-8") as f:
    us_stocks = json.load(f)

out_path = Path(__file__).parent / "us_shares_cache.json"
shares_map = {}
if out_path.exists():
    shares_map = json.load(open(out_path, encoding="utf-8"))
    print(f"기존 캐시 {len(shares_map)}건 로드", flush=True)

codes = [s["code"] for s in us_stocks if s["code"] not in shares_map]
print(f"신규 조회 대상: {len(codes)}건", flush=True)

t0 = time.time()
for i, code in enumerate(codes):
    try:
        info = yf.Ticker(code).fast_info
        shares = info.get("shares") or info.get("sharesOutstanding")
        if shares:
            shares_map[code] = int(shares)
    except Exception:
        pass
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(codes)} 완료 ({round(time.time()-t0)}s), 캐시 {len(shares_map)}건", flush=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(shares_map, f)

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(shares_map, f)
print(f"\n최종 {len(shares_map)}/{len(us_stocks)}건 저장 완료: {out_path} ({round(time.time()-t0)}s)", flush=True)
