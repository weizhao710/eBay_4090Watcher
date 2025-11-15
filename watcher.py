import os
import time
import random
import json
import re
import requests
from bs4 import BeautifulSoup
import telebot

# ========== 环境变量 ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
EBAY_URL = (os.getenv("EBAY_URL") or "").strip()

if not TELEGRAM_TOKEN or not CHAT_ID or not EBAY_URL:
    raise RuntimeError(
        "请在 Railway Variables 中设置 TELEGRAM_TOKEN / CHAT_ID / EBAY_URL"
    )

# CHAT_ID 尝试转数字（更稳定，不是数字也没关系）
try:
    CHAT_ID = int(CHAT_ID)
except:
    pass

bot = telebot.TeleBot(TELEGRAM_TOKEN)

SEEN_FILE = "seen_ids.json"

# 自动生成 RSS 链接
if "_rss=1" in EBAY_URL:
    EBAY_RSS_URL = EBAY_URL
else:
    join_char = "&" if "?" in EBAY_URL else "?"
    EBAY_RSS_URL = EBAY_URL + f"{join_char}_rss=1"


# ========== 工具函数 ==========
def load_seen_ids():
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()


def save_seen_ids(seen_ids):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_ids), f)
    except:
        pass


def send_message(text: str):
    try:
        bot.send_message(CHAT_ID, text, disable_web_page_preview=False)
    except Exception as e:
        print(f"Telegram 发送失败: {e}")


def parse_price(text: str):
    if not text:
        return None
    txt = text.replace(",", "").replace("\xa0", " ")
    m = re.search(r"(\d+(\.\d+)?)", txt)
    if not m:
        return None
    try:
        return float(m.group(1))
    except:
        return None


def extract_item_id_from_url(url: str):
    if not url:
        return None
    m = re.search(r"/itm/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"item(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{10,})", url)
    if m:
        return m.group(1)
    return None


# ========== 抓网页前三条 ==========
def fetch_html_top3():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    resp = requests.get(EBAY_URL, headers=headers, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for card in soup.select(".s-item"):
        if len(items) >= 3:
            break

        title_tag = card.select_one(".s-item__title")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        lower_title = title.lower()

        # 跳广告
        if any(kw in lower_title for kw in ["sponsored", "shop on ebay", "results matching"]):
            continue

        # 必须包含 4090
        if "4090" not in lower_title:
            continue

        a = card.select_one(".s-item__link")
        if not a or not a.get("href"):
            continue

        url = a["href"]
        item_id = extract_item_id_from_url(url)
        if not item_id:
            continue

        price_tag = card.select_one(".s-item__price")
        price = parse_price(price_tag.get_text(strip=True)) if price_tag else None

        clean_url = url.split("?_")[0]

        items.append({
            "id": item_id,
            "title": title,
            "price": price,
            "url": clean_url,
            "source": "html",
        })

    return items


# ========== 抓 RSS ==========
def fetch_rss_items():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    resp = requests.get(EBAY_RSS_URL, headers=headers, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "xml")

    items = []
    for item in soup.find_all("item"):
        title = item.find("title").get_text(strip=True)
        link = item.find("link").get_text(strip=True)

        if "4090" not in title.lower():
            continue

        item_id = extract_item_id_from_url(link)
        if not item_id:
            continue

        clean_url = link.split("?_")[0]

        items.append({
            "id": item_id,
            "title": title,
            "price": None,
            "url": clean_url,
            "source": "rss",
        })

    return items


# ========== 跑一轮 ==========
def run_once():
    seen_ids = load_seen_ids()
    print(f"已记录 {len(seen_ids)} 条历史 item")

    all_items = {}

    # HTML
    try:
        html_items = fetch_html_top3()
        print(f"HTML 抓到 {len(html_items)} 条")
        for it in html_items:
            all_items[it["id"]] = it
    except Exception as e:
        print(f"抓取 HTML 出错: {e}")

    # RSS
    try:
        rss_items = fetch_rss_items()
        print(f"RSS 抓到 {len(rss_items)} 条")
        for it in rss_items:
            if it["id"] not in all_items:
                all_items[it["id"]] = it
    except Exception as e:
        print(f"抓取 RSS 出错: {e}")

    if not all_items:
        print("本次抓取没有任何结果")
        return

    # 找新 id
    new_items = [it for it in all_items.values() if it["id"] not in seen_ids]

    if not new_items:
        print("没有新的 item")
        return

    # 记录 seen
    for it in new_items:
        seen_ids.add(it["id"])
    save_seen_ids(seen_ids)

    # 按 HTML > RSS 排序
    new_items.sort(key=lambda x: x["source"])

    # 推送
    for it in new_items:
        lines = [
            "🆕 新 4090 Listing",
            f"来源：{'网页前 3 条' if it['source']=='html' else 'RSS'}",
            f"标题：{it['title']}"
        ]
        if it["price"]:
            lines.append(f"价格：£{it['price']}")
        lines.append(f"链接：{it['url']}")

        send_message("\n".join(lines))
        print(f"已推送：{it['id']} - {it['title']} ({it['source']})")


# ========== 自循环 ==========
if __name__ == "__main__":
    while True:
        try:
            print("====== 开始新一轮抓取 ======")
            run_once()
        except Exception as e:
            print(f"主循环出错：{e}")

        sleep_time = 20 + random.randint(0, 5)
        print(f"本轮抓取结束，休息 {sleep_time} 秒...\n")
        time.sleep(sleep_time)
