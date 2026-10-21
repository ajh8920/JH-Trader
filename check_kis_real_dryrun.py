# -*- coding: utf-8 -*-
"""한투 3단계 - 실전투자 키로 연결 검증(인증+시세+잔고조회+주문 dry-run).

안전장치: 이 스크립트는 dry_run=True를 하드코딩하고 있고, 주문 함수를
호출하는 단 한 줄 외에는 place_order를 또 호출하지 않는다. 실제 주문이
나가려면 코드를 직접 고쳐야 한다 - 명령줄 인자나 환경변수로 끌 수 없게
일부러 만들었다(실전 계좌 사고 방지).

사용법: python check_kis_real_dryrun.py
"""
from dotenv import load_dotenv

load_dotenv()

from kis_api import KisClient, KisApiError

TEST_CODE = "005930"


def main():
    client = KisClient(env="real")
    assert client.env == "real"

    print("[1/3] 실전투자 서버 인증 + 시세조회...")
    price = client.get_price(TEST_CODE)
    print(f"  성공 - 삼성전자 현재가 {price:,.0f}원")

    print("[2/3] 실전 계좌 잔고조회...")
    balance = client.get_balance()
    print(f"  성공 - 예수금 {balance['cash']:,.0f}원, 보유종목 {len(balance['positions'])}개")
    for p in balance["positions"]:
        print(f"    {p['name']}({p['code']}): {p['shares']}주 @ {p['avg_price']:,.0f}원")

    print("[3/3] 주문 dry-run(실제 주문 절대 안 나감 - dry_run=True 고정)...")
    plan = client.place_order(TEST_CODE, qty=1, side="buy", dry_run=True)  # dry_run 하드코딩
    print(f"  성공 - 실행됐다면 이런 주문: {plan}")

    print("\n실전투자 연결 검증 완료(주문은 미실행). 실제 주문을 시작하려면")
    print("별도로 명시적인 확인을 받은 뒤 진행합니다.")


if __name__ == "__main__":
    main()
