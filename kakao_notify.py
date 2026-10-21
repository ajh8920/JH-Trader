# -*- coding: utf-8 -*-
"""카카오톡 "나에게 보내기" API로 매매 알림을 보낸다.

공식 문서(developers.kakao.com, 2026-10-22 확인) 기준 정리:
- 이 기능은 "친구에게 보내기"/알림톡과 다르다 - 비즈니스 심사나 템플릿 사전등록이
  필요 없다. scope는 talk_message 하나만 있으면 되고, 메시지 수신자는 토큰을
  발급한 그 카카오 계정 자신(=이 앱 운영자)으로 고정된다.
- 엔드포인트: POST https://kapi.kakao.com/v2/api/talk/memo/default/send
  (사전 등록한 템플릿이 아니라 요청마다 template_object를 그대로 실어 보내는
  "기본 템플릿" 방식 - 매매 내역처럼 매번 내용이 달라지는 경우에 맞다)
- 액세스 토큰 유효기간은 문서 예시 기준 약 12시간, 리프레시 토큰은 약 60일.
  리프레시 토큰은 남은 유효기간이 1개월 미만일 때만 갱신 응답에 새로 포함된다.
- 처음 연동(인가 코드 발급)은 운영자가 브라우저로 카카오 로그인을 한 번 동의해야
  한다 - 이 모듈은 그 이후의 토큰 갱신·발송만 자동화한다. /admin 페이지의
  "카카오톡 연동하기" 버튼 -> app.py의 kakao_oauth_start/callback 라우트가 그
  일회성 절차를 담당한다.
- 비공식적으로 "나에게 보내기"는 자동화 봇 용도로 널리 쓰이지만, 카카오가 명시한
  일일 호출 한도는 문서에서 찾지 못했다 - 과도하게 자주 보내지 않도록 매매가
  실제로 있었을 때만 호출한다(paper_trading.py/auto_infinite.py 호출부 참고).
"""
import json
import os
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# 메시지에 넣을 "웹에서 보기" 버튼의 기본 도메인. 카카오 디벨로퍼스 콘솔의
# [웹 도메인]에 이 값과 정확히 같은 도메인을 등록해야 링크/버튼이 동작한다.
APP_BASE_URL = os.environ.get("APP_BASE_URL") or "https://stock-tracker-5gy3.onrender.com"


class KakaoApiError(Exception):
    """카카오 API가 에러를 돌려줬을 때. 호출부(paper_trading.py/auto_infinite.py)는
    이 예외를 절대 밖으로 흘려보내지 않는다 - 알림 발송 실패가 실제 매매/기록을
    막으면 안 된다(notify()가 그 경계 역할을 한다)."""


def _get_credentials():
    api_key = os.environ.get("KAKAO_REST_API_KEY")
    if not api_key:
        raise KakaoApiError(".env에 KAKAO_REST_API_KEY가 설정되어 있지 않습니다")
    return api_key, os.environ.get("KAKAO_CLIENT_SECRET")  # 시크릿은 콘솔에서 켰을 때만 필요


def build_authorize_url(redirect_uri):
    """운영자가 브라우저에서 열 카카오 로그인 동의 URL. scope=talk_message만 요청한다
    (이 앱은 "나에게 보내기"만 쓰므로 friends 등 다른 동의항목은 필요 없다)."""
    api_key, _ = _get_credentials()
    params = {
        "client_id": api_key, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": "talk_message",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code, redirect_uri):
    """인가 코드를 최초 1회 토큰으로 교환해 DB에 저장한다(oauth 콜백 라우트 전용)."""
    from models import KakaoToken, db

    api_key, client_secret = _get_credentials()
    data = {
        "grant_type": "authorization_code", "client_id": api_key,
        "redirect_uri": redirect_uri, "code": code,
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(TOKEN_URL, data=data, timeout=10)
    body = resp.json()
    if resp.status_code != 200:
        raise KakaoApiError(f"인가 코드 교환 실패: {resp.status_code} {body}")

    now = datetime.utcnow()
    row = KakaoToken.query.filter_by(purpose="default").first()
    if not row:
        row = KakaoToken(purpose="default")
        db.session.add(row)
    row.access_token = body["access_token"]
    row.refresh_token = body["refresh_token"]
    row.access_expires_at = now + timedelta(seconds=body.get("expires_in", 0))
    row.refresh_expires_at = now + timedelta(seconds=body.get("refresh_token_expires_in", 0))
    db.session.commit()


def is_connected():
    from models import KakaoToken
    return KakaoToken.query.filter_by(purpose="default").first() is not None


def _get_valid_access_token():
    from models import KakaoToken, db

    row = KakaoToken.query.filter_by(purpose="default").with_for_update().first()
    if not row:
        raise KakaoApiError("카카오톡이 연동되어 있지 않습니다 - /admin에서 먼저 연동하세요")

    now = datetime.utcnow()
    if row.access_expires_at > now + timedelta(minutes=10):
        db.session.commit()  # with_for_update로 건 락 해제
        return row.access_token

    # gunicorn 워커 2개가 거의 동시에 만료를 감지해도, 위 with_for_update가 뒤에
    # 오는 워커를 여기서 대기시킨 뒤 이미 갱신된 토큰을 그대로 읽게 한다.
    api_key, client_secret = _get_credentials()
    data = {"grant_type": "refresh_token", "client_id": api_key, "refresh_token": row.refresh_token}
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(TOKEN_URL, data=data, timeout=10)
    body = resp.json()
    if resp.status_code != 200:
        db.session.rollback()
        raise KakaoApiError(f"토큰 갱신 실패: {resp.status_code} {body}")

    row.access_token = body["access_token"]
    row.access_expires_at = now + timedelta(seconds=body.get("expires_in", 0))
    if "refresh_token" in body:  # 유효기간이 1개월 이상 남으면 응답에 안 옴 - 그땐 기존 값 유지
        row.refresh_token = body["refresh_token"]
        row.refresh_expires_at = now + timedelta(seconds=body.get("refresh_token_expires_in", 0))
    db.session.commit()
    return row.access_token


def _send_feed(title, description, url, button_title):
    """피드 템플릿(제목+본문+버튼)으로 발송한다 - 공식 문서의 object_type="feed"
    스키마 그대로(content.title/description/link, buttons[].title/link)."""
    token = _get_valid_access_token()
    link = {"web_url": url, "mobile_web_url": url}
    template_object = {
        "object_type": "feed",
        "content": {"title": title, "description": description, "link": link},
        "buttons": [{"title": button_title, "link": link}],
    }
    resp = requests.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
        data={"template_object": json.dumps(template_object, ensure_ascii=False)},
        timeout=10,
    )
    body = resp.json()
    if resp.status_code != 200:
        raise KakaoApiError(f"메시지 발송 실패: {resp.status_code} {body}")
    return body


def notify_verbose(title, lines, url=None, button_title="웹에서 보기"):
    """성공/실패와 사유를 함께 돌려준다 - /admin의 "테스트 발송"처럼 사람이 바로
    원인을 알아야 하는 곳에서 쓴다. (title, 실패사유) 튜플이 아니라
    (ok: bool, message: str)을 돌려준다."""
    description = "\n".join(lines)[:1000]
    try:
        _send_feed(title, description, url or APP_BASE_URL, button_title)
        return True, "발송 성공"
    except KakaoApiError as e:
        return False, str(e)
    except Exception as e:
        return False, f"알 수 없는 오류: {e}"


def notify(title, lines, url=None, button_title="웹에서 보기"):
    """성공하면 True, 연동이 안 돼 있거나 발송에 실패하면 False(예외를 던지지 않음 -
    email_utils.send_email과 같은 원칙. 알림 실패가 실제 매매 흐름을 막으면 안 됨)."""
    ok, message = notify_verbose(title, lines, url=url, button_title=button_title)
    if not ok:
        print(f"[kakao_notify] 발송 실패: {message}", flush=True)
    return ok
