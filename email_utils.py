"""SMTP 기반 알림 메일 발송 유틸. 자격증명은 항상 환경변수(.env / Render 환경변수)로만
받는다(RULES.md R10 - 키/시크릿 코드에 하드코딩 금지). 로컬 개발 환경처럼 SMTP_* 값이
비어 있으면 조용히 건너뛴다(메일 기능이 없어도 나머지 기능은 정상 동작해야 하므로) -
호출부(백그라운드 스레드)가 예외로 죽으면 안 되기 때문에 여기서 예외를 전부 삼킨다.
"""
import os
import re
import smtplib
from email.mime.text import MIMEText

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def is_valid_email(addr):
    return bool(addr) and len(addr) <= 255 and "\n" not in addr and "\r" not in addr and bool(EMAIL_RE.match(addr))


def send_email(to_addr, subject, body):
    """성공하면 True, 설정이 비어 있거나 발송에 실패하면 False."""
    if not is_valid_email(to_addr):
        return False
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM") or user
    if not (host and port and user and password):
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(host, int(port), timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception:
        return False
