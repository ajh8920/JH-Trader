# -*- coding: utf-8 -*-
"""한투 Open API 연결 검증 스크립트 - 실전 자동매매 1단계(인증+시세+잔고
조회, 주문은 dry_run만). KIS_DEMO_* 키를 .env에 넣은 뒤 실행한다.

절대 실제 주문을 내지 않는다(dry_run=True 고정) - 이 스크립트의 목적은
"연결이 되는가"만 확인하는 것이다.

사용법: python check_kis_connection.py
"""
import sys
from dotenv import load_dotenv

load_dotenv()

from kis_api import KisClient, KisApiError

TEST_CODE = "005930"  # 삼성전자 - 항상 유동적이라 시세조회 테스트에 적합


def main():
    print("[1/3] 모의투자 서버 인증 + 시세조회...")
    try:
        client = KisClient(env="demo")
        price = client.get_price(TEST_CODE)
        print(f"  성공 - 삼성전자({TEST_CODE}) 현재가 {price:,.0f}원")
    except KisApiError as e:
        print(f"  실패: {e}")
        sys.exit(1)

    print("[2/3] 잔고조회...")
    try:
        balance = client.get_balance()
        print(f"  성공 - 예수금 {balance['cash']:,.0f}원, 보유종목 {len(balance['positions'])}개")
        for p in balance["positions"]:
            print(f"    {p['name']}({p['code']}): {p['shares']}주 @ {p['avg_price']:,.0f}원")
    except KisApiError as e:
        print(f"  실패: {e}")
        sys.exit(1)

    print("[3/3] 주문 dry-run(실제 주문 안 나감)...")
    try:
        plan = client.place_order(TEST_CODE, qty=1, side="buy", dry_run=True)
        print(f"  성공 - 실행됐다면 이런 주문: {plan}")
    except KisApiError as e:
        print(f"  실패: {e}")
        sys.exit(1)

    print("\n모의투자 연결 검증 완료. 실전 전환은 KIS_REAL_* 키로 env=\"real\"을 쓰되,")
    print("여전히 dry_run=True로 최소 며칠간 더 검증한 뒤 진행하세요.")


if __name__ == "__main__":
    main()
