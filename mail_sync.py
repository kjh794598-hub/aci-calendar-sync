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
                raw = "".join(b64_buf).encode("utf-16-be")
                b64 = base64.b64encode(raw).decode("ascii")
                b64 = b64.rstrip("=").replace("/", ",")
                res.append("&" + b64 + "-")
                b64_buf = []
            res.append("&-" if ch == "&" else ch)
        else:
            b64_buf.append(ch)
    if b64_buf:
        raw = "".join(b64_buf).encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii")
        b64 = b64.rstrip("=").replace("/", ",")
        res.append("&" + b64 + "-")
    return "".join(res)


def load_synced_ids():
    if not os.path.exists(SYNC_LOG_FILE):
        return set()
    f = open(SYNC_LOG_FILE, "r", encoding="utf-8")
    data = set(line.strip() for line in f if line.strip())
    f.close()
    return data


def save_synced_id(msg_id):
    f = open(SYNC_LOG_FILE, "a", encoding="utf-8")
    f.write(msg_id + "\n")
    f.close()


def connect_pop():
    pop = poplib.POP3(POP_HOST, POP_PORT, timeout=120)
    pop.user(POP_USER)
    pop.pass_(POP_PASS)
    return pop


def is_already_in_gmail(imap, mailbox, clean_id):
    kr_trash = f'"{encode_imap_utf7("[Gmail]/휴지통")}"'
    folders = [mailbox, '"[Gmail]/Trash"', kr_trash]
    for folder in folders:
        try:
            typ, _ = imap.select(folder, readonly=True)
            if typ == "OK":
                q = f'(HEADER Message-ID "{clean_id}")'
                typ, data = imap.search(None, q)
                if typ == "OK" and data and data[0]:
                    return True
        except Exception:
            continue
    return False


def main():
    synced_ids = load_synced_ids()

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(IMAP_USER, IMAP_PASS)
    mailbox = f'"{encode_imap_utf7(TARGET_LABEL)}"'
    imap.create(mailbox)

    pop = connect_pop()
    num_messages = len(pop.list()[1])
    start_idx = max(1, num_messages - 40)

    synced = 0
    for i in range(start_idx, num_messages + 1):
        try:
            try:
                resp, header_lines, _ = pop.top(i, 0)
            except Exception:
                pop = connect_pop()
                resp, header_lines, _ = pop.top(i, 0)

            header_bytes = b"\r\n".join(header_lines)
            msg_header = email.message_from_bytes(header_bytes)
            msg_id = msg_header.get("Message-ID", "").strip()

            if msg_id:
                clean_id = "".join(msg_id.split()).replace('"', "")
            else:
                clean_id = f"NO_ID_{i}"

            if clean_id in synced_ids:
                continue

            if msg_id and is_already_in_gmail(imap, mailbox, clean_id):
                save_synced_id(clean_id)
                synced_ids.add(clean_id)
                continue

            try:
                resp, lines, octets = pop.retr(i)
            except Exception:
                pop = connect_pop()
                resp, lines, octets = pop.retr(i)

            raw_email = b"\r\n".join(lines)
            full_msg = email.message_from_bytes(raw_email)

            try:
                dt = parsedate_to_datetime(full_msg.get("Date"))
                date_param = imaplib.Time2Internaldate(dt.timestamp())
            except Exception:
                date_param = imaplib.Time2Internaldate(time.time())

            imap.append(mailbox, "", date_param, raw_email)
            save_synced_id(clean_id)
            synced_ids.add(clean_id)
            synced += 1
        except Exception as e:
            print(f"Skipped {i}: {e}")
            save_synced_id(clean_id)
            synced_ids.add(clean_id)
            continue

    try:
        pop.quit()
    except Exception:
        pass
    try:
        imap.logout()
    except Exception:
        pass
    print(f"Sync complete. New messages copied: {synced}")


if __name__ == "__main__":
    main()
