"""
Telegram Deal Poster — runs on GitHub Actions every hour (free).
Reads the published Google Sheet CSV from the TOP, posts the next 20 rows
not posted in the last 24 hours, and records them in posted.txt.
"""
import csv, io, os, time
from datetime import datetime, timezone, timedelta
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID   = os.environ["CHAT_ID"]
SHEET_CSV = os.environ["SHEET_CSV_URL"]

# ---- Controls: set these in GitHub → Settings → Secrets and variables → Actions → Variables
# (no code change needed). The numbers after "or" are the defaults.
def _num(name, default):
    v = (os.environ.get(name) or "").strip()
    return int(v) if v.isdigit() else default

POSTS_PER_RUN      = _num("POSTS_PER_HOUR", 20)   # posts each hour
ACTIVE_FROM_HOUR   = _num("START_HOUR", 8)        # IST, first posting hour (0-23)
ACTIVE_TO_HOUR     = _num("END_HOUR", 23)         # IST, stop at this hour (1-24)
REPOST_AFTER_HOURS = _num("REPOST_HOURS", 24)     # don't repost same product+price within this
GAP_SECONDS        = 3                            # keep >= 3 (Telegram limit ~20 msgs/min)
KEEP_HISTORY_DAYS  = 7     # older entries are deleted from posted.txt
POSTED_FILE        = "posted.txt"

IST = timezone(timedelta(hours=5, minutes=30))

def load_rows():
    r = requests.get(SHEET_CSV, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    return list(csv.DictReader(io.StringIO(r.text)))

def load_history():
    """posted.txt lines: <unix_time>|<product link>|<price>"""
    hist = {}
    if os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, encoding="utf-8") as f:
            for line in f:
                ts, _, key = line.strip().partition("|")
                if ts.isdigit() and key:
                    hist[key] = max(hist.get(key, 0), int(ts))
    return hist

def save_history(hist):
    cutoff = time.time() - KEEP_HISTORY_DAYS * 86400
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        for key, ts in sorted(hist.items(), key=lambda x: x[1]):
            if ts >= cutoff:
                f.write(f"{ts}|{key}\n")

def send(text, link):
    payload = {"chat_id": CHAT_ID, "text": text}
    if link.startswith("http"):
        payload["link_preview_options"] = {
            "url": link, "prefer_large_media": True, "show_above_text": True}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code == 429:                       # Telegram asked us to slow down
        time.sleep(r.json().get("parameters", {}).get("retry_after", 30) + 1)
        r = requests.post(url, json=payload, timeout=30)
    if not r.ok:
        print("   Failed:", r.text[:150])
    return r.ok

def main():
    if not (ACTIVE_FROM_HOUR <= datetime.now(IST).hour < ACTIVE_TO_HOUR) \
       and os.environ.get("FORCE_RUN") != "true":
        print("Outside posting hours, skipping.")
        return

    rows, hist = load_rows(), load_history()
    recent = time.time() - REPOST_AFTER_HOURS * 3600
    sent, remaining = 0, 0

    try:
        for row in rows:                                   # top to bottom, in sheet order
            text = (row.get("Telegram Post") or "").strip()
            link = (row.get("Product Link") or "").strip()
            key  = f"{link}|{(row.get('Selling Price') or '').strip()}"
            if not text or text == "N/A" or hist.get(key, 0) >= recent:
                continue
            if sent >= POSTS_PER_RUN:
                remaining += 1
                continue
            if send(text, link):
                hist[key] = int(time.time())
                sent += 1
                print(f"   Posted {sent}: {(row.get('Product Name') or '')[:60]}")
            time.sleep(GAP_SECONDS)
    finally:
        save_history(hist)

    print(f"Settings: {POSTS_PER_RUN}/hour, {ACTIVE_FROM_HOUR}:00-{ACTIVE_TO_HOUR}:00 IST, "
          f"repost after {REPOST_AFTER_HOURS}h")
    print(f"Done. Posted {sent} this run. Left in current sheet: {remaining}")

if __name__ == "__main__":
    main()
