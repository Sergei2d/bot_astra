"""
Проверяет почтовый ящик по IMAP на новые письма от CI (тема начинается с SUBJECT_PREFIX)
и пересылает их текст в Telegram-группу через Bot API. Непрочитанные письма помечаются
прочитанными сразу при чтении (IMAP FETCH BODY[]) — это и есть маркер "уже обработано",
отдельного стейта между запусками не нужно.
"""

import email
import imaplib
import os
import sys
import urllib.parse
import urllib.request
from email.header import decode_header

IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASSWORD = os.environ["IMAP_PASSWORD"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SUBJECT_PREFIX = os.environ.get("SUBJECT_PREFIX", "[Astra Autotests]")


def decode_mime_header(raw_value: str) -> str:
    parts = decode_header(raw_value)
    decoded = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded += text.decode(charset or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


def get_plain_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(charset, errors="replace")
        return ""
    charset = msg.get_content_charset() or "utf-8"
    payload = msg.get_payload(decode=True)
    return payload.decode(charset, errors="replace") if payload else ""


def send_to_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode()
        if resp.status != 200:
            raise RuntimeError(f"Telegram API error: {resp.status} {body}")
        print(f"Sent to Telegram: {resp.status}")


def main() -> None:
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    conn.login(IMAP_USER, IMAP_PASSWORD)
    conn.select("INBOX")

    status, data = conn.search(None, "UNSEEN")
    if status != "OK":
        print(f"IMAP search failed: {status}", file=sys.stderr)
        sys.exit(1)

    ids = data[0].split()
    print(f"Found {len(ids)} unseen message(s)")

    for msg_id in ids:
        status, msg_data = conn.fetch(msg_id, "(RFC822)")
        if status != "OK":
            print(f"Failed to fetch message {msg_id!r}", file=sys.stderr)
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        subject = decode_mime_header(msg.get("Subject", ""))

        if not subject.startswith(SUBJECT_PREFIX):
            print(f"Skipping (не наше письмо): {subject!r}")
            continue

        body = get_plain_text_body(msg)
        text = f"{subject}\n\n{body.strip()}"
        print(f"Forwarding: {subject!r}")
        send_to_telegram(text)

    conn.close()
    conn.logout()


if __name__ == "__main__":
    main()
