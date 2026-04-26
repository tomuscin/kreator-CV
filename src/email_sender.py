"""
Email sender module for Kreator CV.
Uses SMTP over SSL (port 465) — mail.tomaszuscinski.pl
"""

import os
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import certifi
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST     = os.environ["SMTP_HOST"]
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER     = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
SMTP_FROM     = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "")


def send_cv(
    to: str,
    subject: str,
    body_html: str,
    docx_path: Path,
    body_plain: str | None = None,
) -> None:
    """
    Sends a CV email with a .docx attachment via SMTP SSL.

    Args:
        to:         Recipient email address.
        subject:    Email subject.
        body_html:  HTML body of the email.
        docx_path:  Path to the generated .docx file to attach.
        body_plain: Optional plain text fallback (auto-generated if omitted).

    Raises:
        FileNotFoundError: If docx_path does not exist.
        smtplib.SMTPException: On SMTP errors.
    """
    if not docx_path.exists():
        raise FileNotFoundError(f"Plik CV nie istnieje: {docx_path}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{SMTP_FROM_NAME} <{SMTP_FROM}>" if SMTP_FROM_NAME else SMTP_FROM
    msg["To"]      = to

    plain = body_plain or _strip_html(body_html)
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # Attach .docx
    with docx_path.open("rb") as f:
        attachment = MIMEApplication(f.read(), _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document")
    attachment.add_header("Content-Disposition", "attachment", filename=docx_path.name)

    outer = MIMEMultipart("mixed")
    outer["Subject"] = msg["Subject"]
    outer["From"]    = msg["From"]
    outer["To"]      = msg["To"]
    outer.attach(msg)
    outer.attach(attachment)

    context = ssl.create_default_context(cafile=certifi.where())
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to], outer.as_string())


def test_connection() -> bool:
    """
    Tests SMTP connection and authentication without sending any message.

    Returns:
        True if connection and login succeed.

    Raises:
        smtplib.SMTPAuthenticationError: On bad credentials.
        smtplib.SMTPException: On other SMTP errors.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
    return True


def _strip_html(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html).strip()
