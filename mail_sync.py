import poplib
import imaplib
import smtplib
import email
import email.policy
from email.utils import parsedate_to_datetime, getaddresses, formatdate, make_msgid, formataddr
from email.header import Header
import os
import time
import base64
import uuid

POP_HOST = "gwmail.acichemical.com"
POP_PORT = 110
SMTP_HOST = "gwmail.acichemical.com"
SMTP_PORT = 425
POP_USER = "alex.kim@acichemical.com"
POP_PASS = os.environ["ACI_MAIL_PASSWORD"]

IMAP_HOST = "imap.gmail.com"
IMAP_USER = "kjh794598@gmail.com"
IMAP_PASS = os.environ["GMAIL_APP_PASSWORD"]
TARGET_LABEL = "[ACI 업무]"
OUTBOX_LABEL = "[회사발송]"
SYNC_LOG_FILE = "synced_mails.txt"
SEND_LATEST_DRAFT = os.environ.get("SEND_LATEST_DRAFT", "false").lower() == "true"


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
    with open(SYNC_LOG_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_synced_id(msg_id):
    with open(SYNC_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg_id + "\n")


def connect_pop():
    for attempt in range(3):
        try:
            pop = poplib.POP3(POP_HOST, POP_PORT, timeout=25)
            pop.user(POP_USER)
            pop.pass_(POP_PASS)
            return pop
        except Exception as e:
            print(f"POP3 connection attempt {attempt + 1} failed: {e}")
            time.sleep(3)
    return None


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


def sanitize_and_fix_encoding(msg):
    """1. Gmail 특유의 MIME boundary 흔적을 아웃룩 스타일로 세탁하여 스팸 분류 방지
    2. Quoted-Printable로 인한 한글/HTML 깨짐 방지를 위해 Base64(UTF-8)로 재포장"""
    for part in msg.walk():
        if part.is_multipart():
            old_boundary = part.get_boundary()
            if old_boundary:
                new_boundary = f"----=_NextPart_{uuid.uuid4().hex[:16].upper()}"
                part.set_boundary(new_boundary)
            continue

        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/html"):
            payload_bytes = part.get_payload(decode=True)
            if payload_bytes is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload_bytes.decode(charset, errors="replace")
            except Exception:
                text = payload_bytes.decode("utf-8", errors="replace")

            utf8_bytes = text.encode("utf-8")
            b64_str = base64.encodebytes(utf8_bytes).decode("ascii")

            part.set_payload(b64_str)
            if "Content-Transfer-Encoding" in part:
                del part["Content-Transfer-Encoding"]
            part["Content-Transfer-Encoding"] = "base64"
            part.set_param("charset", "utf-8", header="Content-Type")


def transmit_and_cleanup_draft(imap, current_folder, num):
    sent_folders = [
        f'"{encode_imap_utf7("[Gmail]/보낸편지함")}"',
        '"[Gmail]/Sent Mail"',
    ]
    trash_folders = [
        f'"{encode_imap_utf7("[Gmail]/휴지통")}"',
        '"[Gmail]/Trash"',
    ]
    draft_folders = [
        f'"{encode_imap_utf7("[Gmail]/임시보관함")}"',
        '"[Gmail]/Drafts"',
    ]

    typ, full_data = imap.fetch(num, "(BODY.PEEK[])")
    if typ != "OK" or not full_data or not full_data[0]:
        return False
    raw_email = full_data[0][1]
    msg = email.message_from_bytes(raw_email)
    orig_msg_id = msg.get("Message-ID", "").strip()

    tos = msg.get_all("To", [])
    ccs = msg.get_all("Cc", [])
    bccs = msg.get_all("Bcc", [])
    recipients = [
        addr for name, addr in getaddresses(tos + ccs + bccs) if addr
    ]

    if not recipients:
        print("Draft has no recipients, skipping.")
        return False

    encoded_from = formataddr((Header("김정호", "utf-8").encode(), POP_USER))
    for hdr in ("From", "Reply-To", "Return-Path", "Sender", "Date", "Message-ID", "Bcc", "X-Mailer"):
        while hdr in msg:
            del msg[hdr]

    msg["From"] = encoded_from
    msg["Reply-To"] = encoded_from
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="acichemical.com")
    msg["X-Mailer"] = "Microsoft Outlook 16.0"

    for h in list(msg.keys()):
        hl = h.lower()
        if (
            hl.startswith("x-gm-")
            or hl.startswith("x-google-")
            or hl.startswith("dkim-")
            or hl.startswith("arc-")
            or hl == "received"
        ):
            del msg[h]

    # 스팸 방지용 MIME 경계선 세탁 + 한글/HTML 깨짐 방지(Base64) + 표준 CRLF 줄바꿈 적용
    sanitize_and_fix_encoding(msg)
    out_bytes = msg.as_bytes(policy=email.policy.SMTP)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, local_hostname="acichemical.com", timeout=30) as smtp:
        smtp.ehlo("acichemical.com")
        try:
            smtp.starttls()
            smtp.ehlo("acichemical.com")
        except Exception:
            pass
        smtp.login(POP_USER, POP_PASS)
        smtp.sendmail(POP_USER, recipients, out_bytes)

    print(f"Sent draft via company SMTP (port 425) to: {recipients}")

    for tf in trash_folders:
        try:
            res, _ = imap.copy(num, tf)
            if res == "OK":
                break
        except Exception:
            continue
    imap.store(num, "+FLAGS", "\\Deleted")

    date_param = imaplib.Time2Internaldate(time.time())
    for sf in sent_folders:
        try:
            res, _ = imap.append(sf, "\\Seen", date_param, out_bytes)
            if res == "OK":
                break
        except Exception:
            continue

    if orig_msg_id:
        clean_orig_id = "".join(orig_msg_id.split()).replace('"', "")
        for df in draft_folders:
            try:
                t_sel, _ = imap.select(df, readonly=False)
                if t_sel == "OK":
                    t_srch, d_srch = imap.search(
                        None, f'(HEADER Message-ID "{clean_orig_id}")'
                    )
                    if t_srch == "OK" and d_srch and d_srch[0]:
                        for d_num in d_srch[0].split():
                            for tf in trash_folders:
                                try:
                                    imap.copy(d_num, tf)
                                except Exception:
                                    pass
                            imap.store(d_num, "+FLAGS", "\\Deleted")
                        imap.expunge()
                    break
            except Exception:
                continue
        imap.select(current_folder, readonly=False)

    return True


def send_outbox_drafts(imap):
    outbox_folder = f'"{encode_imap_utf7(OUTBOX_LABEL)}"'
    try:
        typ, _ = imap.select(outbox_folder, readonly=False)
        if typ == "OK":
            typ, data = imap.search(None, "ALL")
            if typ == "OK" and data and data[0]:
                for num in data[0].split():
                    try:
                        transmit_and_cleanup_draft(imap, outbox_folder, num)
                    except Exception as e:
                        print(f"Failed to send outbox draft {num}: {e}")
                try:
                    imap.expunge()
                except Exception:
                    pass
    except Exception:
        pass

    if SEND_LATEST_DRAFT:
        draft_folders = [
            f'"{encode_imap_utf7("[Gmail]/임시보관함")}"',
            '"[Gmail]/Drafts"',
        ]
        for df in draft_folders:
            try:
                typ, _ = imap.select(df, readonly=False)
                if typ == "OK":
                    typ, data = imap.search(None, "ALL")
                    if typ == "OK" and data and data[0]:
                        nums = data[0].split()
                        latest_num = nums[-1]
                        print(f"Sending latest draft ({latest_num}) from {df}...")
                        transmit_and_cleanup_draft(imap, df, latest_num)
                        imap.expunge()
                    break
            except Exception as e:
                print(f"Failed to send latest draft: {e}")


def main():
    synced_ids = load_synced_ids()

    imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
    imap.login(IMAP_USER, IMAP_PASS)
    mailbox = f'"{encode_imap_utf7(TARGET_LABEL)}"'
    imap.create(mailbox)

    send_outbox_drafts(imap)

    pop = connect_pop()
    if not pop:
        print("POP3 server temporarily unreachable. Will retry on next schedule.")
        try:
            imap.logout()
        except Exception:
            pass
        return

    try:
        num_messages = len(pop.list()[1])
    except Exception as e:
        print(f"Failed to list messages: {e}")
        try:
            imap.logout()
        except Exception:
            pass
        return

    start_idx = max(1, num_messages - 40)
    synced = 0

    for i in range(start_idx, num_messages + 1):
        try:
            try:
                resp, header_lines, _ = pop.top(i, 0)
            except Exception:
                pop = connect_pop()
                if not pop:
                    break
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
                if not pop:
                    break
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
            continue

    try:
        if pop:
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
