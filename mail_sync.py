import poplib
import imaplib
import email
from email.utils import parsedate_to_datetime
import os
import time
import re

POP_HOST = "gwmail.acichemical.com"
POP_PORT = 110
POP_USER = "alex.kim@acichemical.com"
POP_PASS = os.environ["ACI_MAIL_PASSWORD"]

IMAP_HOST = "imap.gmail.com"
IMAP_USER = "kjh794598@gmail.com"
IMAP_PASS = os.environ["GMAIL_APP_PASSWORD"]
TARGET_LABEL = "[ACI 업무]"


def encode_imap_utf7(s):
    res = []
    b64_buf = []
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            if b64_buf:
                utf16 = "".join(b64_buf).encode("utf-16-be")
                import base64

                b64 = (
                    base64.b64encode(utf16)
                    .decode("ascii")
                    .rstrip("=")
                    .replace("/", ",")
                )
                res.append("&" + b64 + "-")
                b64_buf = []
            res.append("&-" if ch == "&" else ch)
        else:
            b64_buf.append(ch)
    if b64_buf:
        import base64

        utf16 = "".join(b64_buf).encode("utf-16-be")
        b64 = (
            base64.b64encode(utf16)
            .decode("ascii")
            .rstrip("=")
            .replace("/", ",")
        )
        res.append("&" + b64 + "-")
    return "".join(res)


def main():
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(IMAP_USER, IMAP_PASS)
    encoded_mailbox = f'"{encode_imap_utf7(TARGET_LABEL)}"'
    imap.create(encoded_mailbox)

    pop = poplib.POP3(POP_HOST, POP_PORT, timeout=30)
    pop.user(POP_USER)
    pop.pass_(POP_PASS)

    num_messages = len(pop.list()[1])
    start_idx = max(1, num_messages - 40)

    synced = 0
    for i in range(start_idx, num_messages + 1):
        resp, header_lines, _ = pop.top(i, 0)
        header_bytes = b"\r\n".join(header_lines)
        msg_header = email.message_from_bytes(header_bytes)
        msg_id = msg_header.get("Message-ID", "").strip()

        if msg_id:
            clean_id = msg_id.replace('"', "").strip()
            typ, data = imap.select(encoded_mailbox)
            if typ == "OK":
                typ, search_data = imap.search(
                    None, f'(HEADER Message-ID "{clean_id}")'
                )
                if typ == "OK" and search_data and search_data[0]:
                    continue

        resp, lines, octets = pop.retr(i)
        raw_email = b"\r\n".join(lines)
        full_msg = email.message_from_bytes(raw_email)

        try:
            dt = parsedate_to_datetime(full_msg.get("Date"))
            date_param = imaplib.Time2Internaldate(dt.timestamp())
        except Exception:
            date_param = imaplib.Time2Internaldate(time.time())

        imap.append(encoded_mailbox, "", date_param, raw_email)
        synced += 1

    pop.quit()
    imap.logout()
    print(f"Sync complete. New messages copied: {synced}")


if __name__ == "__main__":
    main()
