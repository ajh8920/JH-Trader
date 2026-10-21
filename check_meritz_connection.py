# -*- coding: utf-8 -*-
"""메리츠 Open API 연결 검증 - 조회(인증+시세+잔고)까지만 한다.

**메리츠는 모의투자가 없다** - 이 스크립트는 그래서 주문 관련 호출을
아예 하지 않는다(dry-run조차 포함 안 함 - kis_api.py의 1단계와 달리
의도적으로 더 좁혔다). 주문 dry-run은 사용자가 이 조회 결과(특히
예수금)를 직접 확인하고 다음 단계로 진행하자고 한 뒤에만 별도
스크립트로 진행한다.

사용법: python check_meritz_connection.py
"""
from dotenv import load_dotenv

load_dotenv()

from meritz_api import MeritzClient, MeritzApiError

TEST_CODE = "005930"


def main():
    client = MeritzClient()
    print(f"[1/3] 인증 + 시세조회... (mac_address={client._mac})")
    try:
        price = client.get_price(TEST_CODE)
        print(f"  성공 - 삼성전자 현재가 {price:,.0f}원")
    except MeritzApiError as e:
        print(f"  실패: {e}")
        return

    print("[2/3] 예수금 조회...")
    try:
        deposit = client.get_deposit()
        print(f"  성공 - 응답: {deposit}")
    except MeritzApiError as e:
        print(f"  실패: {e}")

    print("[3/3] 보유종목 조회...")
    try:
        holdings = client.get_holdings()
        print(f"  성공 - 보유종목 {len(holdings)}개")
        for h in holdings:
            print(f"    {h.get('isnm')}({h.get('iscd')}): {h.get('rmnd_qty')}주 "
                  f"@ {h.get('byng_unpr')}원")
    except MeritzApiError as e:
        print(f"  실패: {e}")

    print("\n조회 검증 완료. 주문 테스트는 이 결과를 보고 진행 여부를 "
          "직접 확인한 뒤(모의투자가 없어 실제 계좌로 나감) 별도로 진행합니다.")


if __name__ == "__main__":
    main()
