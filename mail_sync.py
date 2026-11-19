import poplib
import imaplib
import smtplib
import email
from email.utils import parsedate_to_datetime, getaddresses, formatdate, make_msgid, formataddr
from email.header import Header
import os
import time
import base64

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


def send_outbox_drafts(imap):
    outbox_folder = f'"{encode_imap_utf7(OUTBOX_LABEL)}"'
    try:
        typ, _ = imap.select(outbox_folder, readonly=False)
        if typ != "OK":
            return
    except Exception:
        return

    try:
        typ, data = imap.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return
        msg_nums = data[0].split()
    except Exception as e:
        print(f"Outbox search error: {e}")
        return

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

    for num in msg_nums:
        try:
            typ, full_data = imap.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not full_data or not full_data[0]:
                continue
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
                continue

            encoded_from = formataddr((Header("김정호", "utf-8").encode(), POP_USER))
            while "From" in msg:
                del msg["From"]
            msg["From"] = encoded_from

            while "Date" in msg:
                del msg["Date"]
            msg["Date"] = formatdate(localtime=True)

            while "Message-ID" in msg:
                del msg["Message-ID"]
            msg["Message-ID"] = make_msgid(domain="acichemical.com")

            if "Bcc" in msg:
                del msg["Bcc"]

            for h in list(msg.keys()):
                if h.lower().startswith("x-gm-") or h.lower().startswith("x-google-"):
                    del msg[h]

            out_bytes = msg.as_bytes()

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
                smtp.ehlo()
                try:
                    smtp.starttls()
                    smtp.ehlo()
                except Exception:
                    pass
                smtp.login(POP_USER, POP_PASS)
                smtp.sendmail(POP_USER, recipients, out_bytes)

            print(f"Sent draft via company SMTP (port 425) to: {recipients}")

            # 1) [회사발송] 폴더에서 휴지통으로 복사 후 삭제
            for tf in trash_folders:
                try:
                    res, _ = imap.copy(num, tf)
                    if res == "OK":
                        break
                except Exception:
                    continue
            imap.store(num, "+FLAGS", "\\Deleted")

            # 2) 보낸편지함에 저장
            date_param = imaplib.Time2Internaldate(time.time())
            for sf in sent_folders:
                try:
                    res, _ = imap.append(sf, "\\Seen", date_param, out_bytes)
                    if res == "OK":
                        break
                except Exception:
                    continue

            # 3) [임시보관함]에 남아있는 원본도 찾아서 완전히 삭제
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
                imap.select(outbox_folder, readonly=False)

        except Exception as e:
            print(f"Failed to send outbox draft {num}: {e}")
            continue

    try:
        imap.expunge()
    except Exception:
        pass


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
