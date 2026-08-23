"""SMTP 기반 알림 메일 발송 유틸. 자격증명은 항상 환경변수(.env / Render 환경변수)로만
받는다(RULES.md R10 - 키/시크릿 코드에 하드코딩 금지). 로컬 개발 환경처럼 SMTP_* 값이
비어 있으면 조용히 건너뛴다(메일 기능이 없어도 나머지 기능은 정상 동작해야 하므로) -
호출부(백그라운드 스레드)가 예외로 죽으면 안 되기 때문에 여기서 예외를 전부 삼킨다.
"""
import contextlib
import os
import re
import smtplib
import socket
from email.mime.text import MIMEText

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


@contextlib.contextmanager
def _force_ipv4():
    """Render 같은 컨테이너 환경은 IPv6 주소는 받아오지만(smtp.gmail.com 등은
    AAAA 레코드도 있음) 실제 IPv6 라우트가 없어 "[Errno 101] Network is
    unreachable"로 실패하는 경우가 흔하다(smtplib가 getaddrinfo가 돌려준 주소
    순서대로 시도하는데 IPv6를 먼저 집어들면 거기서 막힌다). 연결 시도 구간에서만
    getaddrinfo가 IPv4 주소만 돌려주도록 임시로 바꿔치기해 우회한다 - 호스트명은
    그대로 넘기므로 TLS 인증서 검증(SNI)에는 영향이 없다."""
    socket.getaddrinfo = _getaddrinfo_ipv4_only
    try:
        yield
    finally:
        socket.getaddrinfo = _orig_getaddrinfo


def is_valid_email(addr):
    return bool(addr) and len(addr) <= 255 and "\n" not in addr and "\r" not in addr and bool(EMAIL_RE.match(addr))


def send_email_verbose(to_addr, subject, body):
    """send_email과 같은 로직이지만 실패 원인을 함께 돌려준다 - 관리자 페이지의
    "테스트 발송"처럼 사람이 바로 원인(SMTP 미설정/인증 실패/연결 실패 등)을
    알아야 하는 곳에서 쓴다. 백그라운드 스케줄러는 원인까지 필요 없어
    send_email()을 그대로 쓴다."""
    if not is_valid_email(to_addr):
        return False, "이메일 형식이 올바르지 않습니다"
    host = os.environ.get("SMTP_HOST")
    port = os.environ.get("SMTP_PORT")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM") or user
    if not (host and port and user and password):
        return False, "서버에 SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD 환경변수가 설정되어 있지 않습니다"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    try:
        with _force_ipv4(), smtplib.SMTP(host, int(port), timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True, "발송 성공"
    except Exception as e:
        return False, str(e)


def send_email(to_addr, subject, body):
    """성공하면 True, 설정이 비어 있거나 발송에 실패하면 False."""
    ok, _ = send_email_verbose(to_addr, subject, body)
    return ok
