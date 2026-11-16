import poplib
import imaplib
import email
from email.utils import parsedate_to_datetime
import os
import time
import base64

POP_HOST = "gwmail.acichemical.com"
POP_PORT = 110
POP_USER = "alex.kim@acichemical.com"
POP_PASS = os.environ["ACI_MAIL_PASSWORD"]

IMAP_HOST = "imap.gmail.com"
IMAP_USER = "kjh794598@gmail.com"
IMAP_PASS = os.environ["GMAIL_APP_PASSWORD"]
TARGET_LABEL = "[ACI 업무]"
SYNC_LOG_FILE = "synced_mails.txt"


def encode_imap_utf7(s):
    res = []
    b64_buf = []
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            if b64_buf:
                utf16 = "".join(b64_buf).encode("utf-16-be")
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
        utf16 = "".join(b64_buf).encode("utf-16-be")
        b64 = (
            base64.b64encode(utf16)
            .decode("ascii")
            .rstrip("=")
            .replace("/", ",")
        )
        res.append("&" + b64 + "-")
    return "".join(res)


def load_synced_ids():
    if not os.path.exists(SYNC_LOG_FILE):
        return set()
    with open(SYNC_LOG_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_synced_id(msg_id):
    with open(SYNC_LOG_FILE, "a", encoding="utf-8")
