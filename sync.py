from playwright.sync_api import sync_playwright
import requests
from icalendar import Calendar
from datetime import datetime, timedelta, date, timezone
import time
import os

# =====================================================================
# ★ 여기 3줄만 아까 메모장(aci_test.py)에 넣었던 내용으로 채워주세요!
# =====================================================================
USER_ID = "alex.kim"
USER_PW = "790405"
ICAL_URL = "https://calendar.google.com/calendar/ical/baf6cf7a0759ad75dd0fc053b7aa290258e3151f4b8e4d7c02783d88c879e92c%40group.calendar.google.com/private-6538be901e8bce62e50fcfc066055567/basic.ics"

SYNCED_FILE = "synced_events.txt"
KST = timezone(timedelta(hours=9))

def get_synced_uids():
    if not os.path.exists(SYNCED_FILE):
        return set()
    with open(SYNCED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())

def save_synced_uid(uid):
    with open(SYNCED_FILE, "a", encoding="utf-8") as f:
        f.write(uid + "\n")

def fetch_new_events():
    print("구글 'ACI 업무' 캘린더에서 새 일정을 확인하는 중...")
    resp = requests.get(ICAL_URL)
    cal = Calendar.from_ical(resp.content)
    synced_uids = get_synced_uids()
    new_events = []
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    for component in cal.walk():
        if component.name == "VEVENT":
            uid = str(component.get("uid"))
            if uid in synced_uids:
                continue

            summary = str(component.get("summary", "업무 일정"))
            if not summary.startswith("<김정호>"):
                title = f"<김정호> {summary}"
            else:
                title = summary

            dtstart = component.get("dtstart").dt
            dtend_prop = component.get("dtend")
            dtend = dtend_prop.dt if dtend_prop else None

            if isinstance(dtstart, date) and not isinstance(dtstart, datetime):
                start_date = dtstart.strftime("%Y-%m-%d")
                start_time = "09:00"
                end_date = start_date
                end_time = "11:00"
            else:
                if dtstart.tzinfo is not None:
                    dtstart = dtstart.astimezone(KST)
                start_date = dtstart.strftime("%Y-%m-%d")
                start_time = dtstart.strftime("%H:%M")

                if dtend:
                    if dtend.tzinfo is not None:
                        dtend = dtend.astimezone(KST)
                    if (dtend - dtstart) <= timedelta(hours=1):
                        dtend = dtstart + timedelta(hours=2)
                else:
                    dtend = dtstart + timedelta(hours=2)

                end_date = dtend.strftime("%Y-%m-%d")
                end_time = dtend.strftime("%H:%M")

            if start_date >= today_str:
                new_events.append({
                    "uid": uid,
                    "title": title,
                    "start_date": start_date,
                    "start_time": start_time,
                    "end_date": end_date,
                    "end_time": end_time
                })

    return new_events

def register_to_groupware(events):
    if not events:
        print("현재 새로 등록할 일정이 없습니다!")
        return

    with sync_playwright() as p:
        # 클라우드 서버용 headless=True 설정
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("1. 회사 그룹웨어 로그인 중...")
        page.goto("http://gw.acichemical.com/gw/sub/common/login/cmmLogin.do")
        page.fill("#cmmLogin_userId", USER_ID)
        page.fill("input[type='password']", USER_PW)
        page.press("input[type='password']", "Enter")
        time.sleep(5)

        active_page = context.pages[-1]
        print("2. 상단 '캘린더' 메뉴 클릭...")
        active_page.locator("text=캘린더").first.click()
        time.sleep(5)

        for ev in events:
            print(f"👉 일정 등록 시작: {ev['title']} ({ev['start_date']} {ev['start_time']}~{ev['end_time']})")
            for pg in context.pages:
                for f in pg.frames:
                    try:
                        btns = f.locator("text=일정추가")
                        if btns.count() > 0:
                            btns.last.click(timeout=2000)
                    except:
                        pass
            time.sleep(3)

            popup_target = None
            for pg in context.pages:
                for f in pg.frames:
                    if f.locator("#scdEventForm_eventSubject").count() > 0 or f.locator("input[placeholder*='이벤트 제목']").count() > 0:
                        popup_target = f
                        break
                if popup_target:
                    break

            if not popup_target:
                print("❌ 팝업창을 찾지 못했습니다.")
                continue

            popup_target.locator("#scdEventForm_eventSubject, input[placeholder*='이벤트 제목']").first.fill(ev["title"])
            time.sleep(0.5)

            all_day_checkbox = popup_target.locator("tr:has-text('기간') input[type='checkbox']").first
            if all_day_checkbox.is_checked():
                all_day_checkbox.uncheck()
            time.sleep(0.5)

            date_inputs = popup_target.locator("tr:has-text('기간') input[type='text']")
            if date_inputs.count() >= 4:
                date_inputs.nth(0).fill(ev["start_date"])
                date_inputs.nth(1).fill(ev["start_time"])
                date_inputs.nth(2).fill(ev["end_date"])
                date_inputs.nth(3).fill(ev["end_time"])

            popup_target.evaluate("""() => {
                const rows = Array.from(document.querySelectorAll('tr'));
                const calRow = rows.find(r => r.innerText && r.innerText.includes('캘린더') && r.offsetParent !== null);
                if (!calRow) return;

                const sel = calRow.querySelector('select');
                if (sel) {
                    const opt = Array.from(sel.options).find(o => o.text.includes('ACI-KOREA'));
                    if (opt) {
                        sel.value = opt.value;
                        sel.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }

                const trigger = calRow.querySelector('.select_box, .combobox, .btn_drop, button, a, div, span');
                if (trigger) trigger.click();

                const items = Array.from(calRow.querySelectorAll('li, label, a, span'));
                const aciItem = items.find(el => el.textContent && el.textContent.trim() === 'ACI-KOREA');
                if (aciItem) aciItem.click();

                const checkboxes = Array.from(calRow.querySelectorAll('input[type="checkbox"], input[type="radio"]'));
                checkboxes.forEach(cb => {
                    const parentText = cb.parentElement ? cb.parentElement.textContent : '';
                    if (parentText.includes('내 캘린더') && cb.checked) cb.click();
                    if (parentText.includes('ACI-KOREA') && !cb.checked) cb.click();
                });
            }""")
            time.sleep(1)

            popup_target.locator("#scdEventForm_eventSubject, input[placeholder*='이벤트 제목']").first.click()
            time.sleep(0.5)

            popup_target.evaluate("""() => {
                const elements = Array.from(document.querySelectorAll('button, a, input[type="button"], span'));
                const saveBtns = elements.filter(el => {
                    const txt = (el.textContent || el.value || '').trim();
                    return txt === '저장' && el.offsetParent !== null;
                });
                if (saveBtns.length > 0) {
                    saveBtns[saveBtns.length - 1].click();
                }
            }""")

            save_synced_uid(ev["uid"])
            print("✅ 등록 완료!")
            time.sleep(4)

        time.sleep(3)
        browser.close()

if __name__ == "__main__":
    new_ev = fetch_new_events()
    register_to_groupware(new_ev)
