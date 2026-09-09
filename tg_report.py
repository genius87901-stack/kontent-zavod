# -*- coding: utf-8 -*-
"""
ТЕКСТОВЫЙ ОТЧЁТ В TG-АРХИВ — что опубликовано, куда и когда.

Не файл/карусель (это делает tg_send.py), а простое текстовое сообщение
sendMessage — сводка по прошедшему батчу публикаций.

    python tg_report.py --file report.txt --send
    python tg_report.py --text "..." --send
"""
import argparse
import os
import sys
from pathlib import Path

import requests

API = "https://api.telegram.org"


def load_env(path=".env"):
    env = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="файл с текстом отчёта (UTF-8)")
    ap.add_argument("--text", help="текст отчёта прямо в команде")
    ap.add_argument("--to", help="chat_id (умолч. TG_CHAT_ID из .env)")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    if not a.file and not a.text:
        sys.exit("Укажи --file или --text")
    text = Path(a.file).read_text(encoding="utf-8") if a.file else a.text

    env = load_env()
    token = env.get("TG_BOT_TOKEN") or os.environ.get("TG_BOT_TOKEN")
    chat = a.to or env.get("TG_CHAT_ID") or os.environ.get("TG_CHAT_ID")
    if not token:
        sys.exit("НЕТ TG_BOT_TOKEN в .env")
    if not chat:
        sys.exit("НЕТ TG_CHAT_ID (укажи --to или пропиши в .env)")

    print("КУДА:", chat)
    print("ТЕКСТ (%d симв.):" % len(text))
    print(text)

    if not a.send:
        print("[ПРОБНЫЙ ПРОГОН] Ничего не отправлено. Для отправки добавь --send")
        return

    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        r = requests.post(f"{API}/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": chunk}, timeout=30)
        if r.status_code != 200:
            sys.exit("Telegram отказал (%d): %s" % (r.status_code, r.text[:300]))
    print("ОТПРАВЛЕНО.")


if __name__ == "__main__":
    main()
