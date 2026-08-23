"""Resend(https://resend.com) HTTP API 기반 알림 메일 발송 유틸. 원래 smtplib로 직접
SMTP 연결을 했는데, Render 컨테이너에서 SMTP 아웃바운드 포트(587)가 막혀 있어
연결이 타임아웃났다(클라우드 호스트의 스팸 방지 정책으로 추정). HTTPS(443)는 이
앱이 이미 야후파이낸스/DART Open API 호출에 쓰고 있어 막힐 걱정이 없어 HTTP API
방식으로 전환했다. 자격증명은 항상 환경변수로만 받는다(RULES.md R10 - 키/시크릿
코드에 하드코딩 금지). 로컬 개발 환경처럼 RESEND_API_KEY가 비어 있으면 조용히
건너뛴다(메일 기능이 없어도 나머지 기능은 정상 동작해야 하므로).
"""
import os
import re

import requests

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
RESEND_API_URL = "https://api.resend.com/emails"
# Resend가 도메인 인증 없이도 바로 쓸 수 있게 제공하는 기본 발신 주소.
DEFAULT_FROM = "onboarding@resend.dev"


def is_valid_email(addr):
    return bool(addr) and len(addr) <= 255 and "\n" not in addr and "\r" not in addr and bool(EMAIL_RE.match(addr))


def send_email_verbose(to_addr, subject, body):
    """send_email과 같은 로직이지만 실패 원인을 함께 돌려준다 - 관리자 페이지의
    "테스트 발송"처럼 사람이 바로 원인(API 키 미설정/Resend 측 오류 등)을 알아야
    하는 곳에서 쓴다. 백그라운드 스케줄러는 원인까지 필요 없어 send_email()을
    그대로 쓴다."""
    if not is_valid_email(to_addr):
        return False, "이메일 형식이 올바르지 않습니다"
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        return False, "서버에 RESEND_API_KEY 환경변수가 설정되어 있지 않습니다"
    from_addr = os.environ.get("RESEND_FROM") or DEFAULT_FROM
    try:
        res = requests.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": from_addr, "to": [to_addr], "subject": subject, "text": body},
            timeout=20,
        )
        if res.status_code >= 400:
            return False, f"Resend API 오류({res.status_code}): {res.text[:300]}"
        return True, "발송 성공"
    except Exception as e:
        return False, str(e)


def send_email(to_addr, subject, body):
    """성공하면 True, 설정이 비어 있거나 발송에 실패하면 False."""
    ok, _ = send_email_verbose(to_addr, subject, body)
    return ok
