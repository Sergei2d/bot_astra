"""
Проверяет почтовый ящик по IMAP на новые письма от CI (тема начинается с SUBJECT_PREFIX)
и пересылает их текст в Telegram-группу через Bot API.

Письмо помечается прочитанным (\\Seen) только ПОСЛЕ успешной отправки в Telegram — если
отправка упала (например, невалидный токен), письмо остаётся непрочитанным и будет
повторно обработано на следующем запуске. Ошибка на одном письме не прерывает обработку
остальных — все найденные письма пытаются обработаться, ошибки собираются и падают
в конце одним exit-кодом, чтобы джоба всё равно показала красный статус.
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Sent to Telegram: {resp.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Telegram API error: {e.code} {body}") from e


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

    failures = []

    for msg_id in ids:
        # BODY.PEEK[] — читаем письмо, НЕ выставляя \Seen автоматически
        status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[])")
        if status != "OK":
            print(f"Failed to fetch message {msg_id!r}", file=sys.stderr)
            failures.append((msg_id, "fetch failed"))
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        subject = decode_mime_header(msg.get("Subject", "") or "")

        if not subject.startswith(SUBJECT_PREFIX):
            print(f"Skipping (не наше письмо): {subject!r}")
            continue

        body = get_plain_text_body(msg)
        text = f"{subject}\n\n{body.strip()}"
        print(f"Forwarding: {subject!r}")

        try:
            send_to_telegram(text)
        except Exception as e:
            print(f"Failed to forward {subject!r}: {e}", file=sys.stderr)
            failures.append((msg_id, str(e)))
            continue

        # Помечаем прочитанным только после успешной отправки
        conn.store(msg_id, "+FLAGS", "\\Seen")

    conn.close()
    conn.logout()

    if failures:
        print(f"\n{len(failures)} message(s) failed, will retry next run:", file=sys.stderr)
        for msg_id, reason in failures:
            print(f"  - {msg_id!r}: {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
