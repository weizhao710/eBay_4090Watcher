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
EBAY_URL = os.getenv("EBAY_URL")  # 例如：https://www.ebay.co.uk/sch/i.html?_nkw=4090&_sacat=27386&LH_PrefLoc=1&_sop=10&rt=nc

if not TELEGRAM_TOKEN or not CHAT_ID or not EBAY_URL:
    raise RuntimeError(
        "请在 Railway Variables 中设置 TELEGRAM_TOKEN / CHAT_ID / EBAY_URL"
    )

# 如果 CHAT_ID 是纯数字，转成 int（Telegram 库更稳）
try:
    CHAT_ID = int(CHAT_ID)
except ValueError:
    # 有的人会用 @username，那就保持字符串
    pass

bot = telebot.TeleBot(TELEGRAM_TOKEN)

SEEN_FILE = "seen_ids.json"

# 派生出 RSS URL（同一个搜索，加上 &_rss=1）
if "_rss=1" in EBAY_URL:
    EBAY_RSS_URL = EBAY_URL
else:
    join_char = "&" if "?" in EBAY_URL else "?"
    EBAY_RSS_URL = EBAY_URL + f"{join_char}_rss=1"


# ========== 工具函数 ==========

def load_seen_ids():
    """从本地 JSON 读已见过的 item id 集合"""
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_seen_ids(seen_ids):
    """保存已见过的 item id 集合到本地 JSON"""
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_ids), f)
    except Exception as e:
        print(f"保存 seen_ids 失败: {e}")


def send_message(text: str):
    """发送 Telegram 消息"""
    try:
        bot.send_message(CHAT_ID, text, disable_web_page_preview=False)
    except Exception as e:
        print(f"Telegram 发送失败: {e}")


def parse_price(text: str):
    """从价格字符串里抽一个浮点数，比如 '£1,299.99' -> 1299.99"""
    if not text:
        return None
    txt = text.replace(",", "").replace("\xa0", " ")
    m = re.search(r"(\d+(\.\d+)?)", txt)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def extract_item_id_from_url(url: str):
    """从 eBay 链接中抽 item id"""
    if not url:
        return None
    # 常见形式：/itm/123456789012
    m = re.search(r"/itm/(\d+)", url)
    if m:
        return m.group(1)
    # 备用：item123456789012
    m = re.search(r"item(\d+)", url)
    if m:
        return m.group(1)
    # 备用：/?hash=item123456789012
    m = re.search(r"(\d{10,})", url)
    if m:
        return m.group(1)
    return None


# ========== 抓网页前 3 条 ==========

def fetch_html_top3():
    """
    从正常搜索页 (EBAY_URL) 抓取“有效的前三个结果”
    返回列表 [{
        id, title, price, url
    }]
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    resp = requests.get(EBAY_URL, headers=headers, timeout=15)
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

        # 跳过广告/提示类结果
        if any(
            kw in lower_title
            for kw in ["sponsored", "shop on ebay", "results matching fewer words"]
        ):
            continue

        # 只要标题里包含 4090（你只监控 4090）
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
        price_text = price_tag.get_text(strip=True) if price_tag else None
        price = parse_price(price_text)

        clean_url = url.split("?_")[0]

        items.append(
            {
                "id": item_id,
                "title": title,
                "price": price,
                "url": clean_url,
                "source": "html",
            }
        )

    return items


# ========== 抓 RSS ==========

def fetch_rss_items():
    """
    从 RSS (EBAY_RSS_URL) 抓取若干结果
    返回列表 [{
        id, title, price(None), url
    }]
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )
    }

    resp = requests.get(EBAY_RSS_URL, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "xml")

    items = []
    for item in soup.find_all("item"):
        title_tag = item.find("title")
        link_tag = item.find("link")

        if not title_tag or not link_tag:
            continue

        title = title_tag.get_text(strip=True)
        link = link_tag.get_text(strip=True)
        lower_title = title.lower()

        # 同样只收 4090
        if "4090" not in lower_title:
            continue

        item_id = extract_item_id_from_url(link)
        if not item_id:
            continue

        clean_url = link.split("?_")[0]

        items.append(
            {
                "id": item_id,
                "title": title,
                "price": None,  # RSS 里一般不直接给价格，这里就不管了
                "url": clean_url,
                "source": "rss",
            }
        )

    return items


# ========== 主流程 ==========

def main():
    # 随机延迟 0–5 秒，配合 Railway cron（比如每 20 秒一次）
    delay = random.randint(0, 5)
    print(f"本次延迟 {delay} 秒后开始抓取")
    time.sleep(delay)

    seen_ids = load_seen_ids()
    print(f"已记录 {len(seen_ids)} 条历史 item")

    all_items = {}

    # 1) 网页前 3 条
    try:
        html_items = fetch_html_top3()
        print(f"HTML 抓到 {len(html_items)} 条")
        for it in html_items:
            all_items[it["id"]] = it
    except Exception as e:
        print(f"抓取 HTML 出错: {e}")
        send_message(f"[eBay 4090 Watcher] 抓取 HTML 出错：{e}")

    # 2) RSS
    try:
        rss_items = fetch_rss_items()
        print(f"RSS 抓到 {len(rss_items)} 条")
        for it in rss_items:
            # 如果 HTML 已有同 id，就保留 HTML（因为有价格）
            if it["id"] not in all_items:
                all_items[it["id"]] = it
    except Exception as e:
        print(f"抓取 RSS 出错: {e}")
        send_message(f"[eBay 4090 Watcher] 抓取 RSS 出错：{e}")

    if not all_items:
        print("本次抓取没有任何结果（可能是网络/结构问题）")
        return

    # 只对“之前未见过”的 id 发通知
    new_items = [it for it in all_items.values() if it["id"] not in seen_ids]

    if not new_items:
        print("没有新的 item")
        return

    # 更新已见 ID
    for it in new_items:
        seen_ids.add(it["id"])
    save_seen_ids(seen_ids)

    # 按来源简单排序：先 HTML（因为更稳定）、再 RSS
    new_items.sort(key=lambda x: x["source"])

    # 推送
    for it in new_items:
        lines = [
            "🆕 新 4090 Listing",
            f"来源：{ '网页前 3 条' if it['source']=='html' else 'RSS' }",
            f"标题：{it['title']}",
        ]
        if it["price"] is not None:
            lines.append(f"价格：£{it['price']}")
        lines.append(f"链接：{it['url']}")
        msg = "\n".join(lines)
        send_message(msg)
        print(f"已推送：{it['id']} - {it['title']} ({it['source']})")


if __name__ == "__main__":
    main()
