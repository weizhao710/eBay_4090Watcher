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
EBAY_URL = os.getenv("EBAY_URL")

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
        print(f"Telegram
