#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отправка файла в Telegram-канал-архив ЧЕРЕЗ СВОЙ БОТ (TG_BOT_TOKEN из .env).
Нужно для теста репостера SMMplanner: пост в @chirkova_arhiv делает бот,
которым управляем мы, а не сторонний сервис.

Тип отправки выбирается по расширению:
  .jpg/.jpeg/.png  -> sendPhoto
  .mp4/.mov        -> sendVideo
  прочее           -> sendDocument

Предохранитель: без --send скрипт делает ПРОБНЫЙ ПРОГОН (dry-run) — печатает,
что и куда ушло бы, но запрос к Telegram API не выполняет. Реальная отправка —
только с явным --send (единый закон проекта: наружу только по --send).

Примеры (PowerShell):
  python tg_send.py --file out_reels/r31.mp4 --caption "тест репостера"        # dry-run
  python tg_send.py --file out_reels/r31.mp4 --caption "тест репостера" --send  # отправка
  python tg_send.py --file out_c21/01.png --caption-file caption_c21_threads.txt --send
  python tg_send.py --file 01.png --to @chirkova_arhiv --send
"""
import argparse
import os
import sys
from pathlib import Path

import requests

API = "https://api.telegram.org"
HERE = os.path.dirname(os.path.abspath(__file__))

# Единый ридер настроек — общий с modules/publishing/doctor.py. См. шапку
# modules/publishing/config_reader.py.
sys.path.append(os.path.join(HERE, "modules"))
try:
    from publishing import config_reader
except Exception:          # обрезанная копия без modules/ — работаем как раньше
    config_reader = None


def probe_video(path):
    """Вернуть (width, height, duration_sec) или None, если пробер недоступен.
    Пробуем ffprobe, затем moviepy. Нужно, чтобы Telegram НЕ пережимал видео:
    без корректных размеров он строит свой уменьшенный вариант."""
    import subprocess
    import shutil
    if shutil.which("ffprobe"):
        try:
            out = subprocess.check_output([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "default=nw=1:nk=1", str(path)
            ], text=True, timeout=30).split()
            w, h, dur = int(out[0]), int(out[1]), int(float(out[2]))
            return w, h, dur
        except Exception:
            pass
    try:
        from moviepy.editor import VideoFileClip
        with VideoFileClip(str(path)) as clip:
            w, h = clip.size
            return int(w), int(h), int(clip.duration)
    except Exception:
        return None


def load_env(path=".env"):
    """Настройки архива: config/factory.config.json, поверх — .env.

    ⚠️ ДВА ФАЙЛА, А НЕ ОДИН. Ключ бота помечен в манифесте секретом и уезжает в
    `.env`, а адрес канала (TG_CHAT_ID) секретом не помечен — это публичный
    @логин — и уезжает в `config/factory.config.json`. Пока читался только
    `.env`, скрипт отвечал «НЕТ TG_CHAT_ID» клиенту, который его честно вписал.
    """
    if config_reader is not None:
        return config_reader.merged(start=HERE)
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
    ap = argparse.ArgumentParser(description="Отправка файла в TG-архив через свой бот")
    ap.add_argument("--file", help="путь к одному файлу (фото/видео/док)")
    ap.add_argument("--dir", help="папка карусели: все .png/.jpg уйдут АЛЬБОМОМ (media group)")
    ap.add_argument("--to", help="chat_id канала (умолч. TG_CHAT_ID из .env)")
    ap.add_argument("--caption", help="подпись")
    ap.add_argument("--caption-file", help="файл с подписью (UTF-8)")
    ap.add_argument("--as-document", action="store_true",
                    help="слать видео как ДОКУМЕНТ — без пережатия Telegram "
                         "(для архива; инлайн-плеера не будет)")
    ap.add_argument("--send", action="store_true",
                    help="РЕАЛЬНО отправить в Telegram. Без флага — пробный "
                         "прогон (dry-run): печатается, что и куда ушло бы, "
                         "но запрос к Telegram API НЕ делается.")
    args = ap.parse_args()
    if not args.file and not args.dir:
        sys.exit("Укажи --file ИЛИ --dir")

    env = load_env()
    token = env.get("TG_BOT_TOKEN") or os.environ.get("TG_BOT_TOKEN")
    # ЛОКАЛЬНАЯ ПРАВКА (09.09.2026): докстринг выше обещает, что адрес канала
    # читается из config/factory.config.json — но там ключ называется
    # `tg_chat_id` строчными, а искали только `TG_CHAT_ID` заглавными.
    # config_reader.merged() регистр не меняет, поэтому адрес НЕ находился
    # никогда, и скрипт врал «НЕ ЗАДАН АДРЕС КАНАЛА-АРХИВА» клиенту, который
    # его честно вписал (ровно тот случай, что описан в докстринге).
    chat = (args.to or env.get("TG_CHAT_ID") or env.get("tg_chat_id")
            or os.environ.get("TG_CHAT_ID"))
    if chat is not None:
        chat = str(chat)
    if not token:
        sys.exit("НЕ ЗАДАН КЛЮЧ БОТА TELEGRAM (TG_BOT_TOKEN) — архив вести "
                 "нечем.\nОн спрашивается в мастере установки («Ключ бота для "
                 "Telegram-архива») и лежит в .env.")
    if not chat:
        sys.exit("НЕ ЗАДАН АДРЕС КАНАЛА-АРХИВА (TG_CHAT_ID).\n"
                 "Он спрашивается в мастере установки («Адрес вашего "
                 "Telegram-канала-архива») и лежит\n"
                 "в config/factory.config.json, а не в .env. Либо укажите "
                 "канал прямо: --to @имя_канала.")

    caption = args.caption or ""
    if args.caption_file:
        caption = Path(args.caption_file).read_text(encoding="utf-8")

    # --- режим АЛЬБОМА (карусель) ---
    if args.dir:
        d = Path(args.dir)
        if not d.is_dir():
            sys.exit(f"Папка не найдена: {d}")
        imgs = sorted(p for p in d.iterdir()
                      if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        if not (2 <= len(imgs) <= 10):
            sys.exit(f"Для альбома нужно 2–10 картинок, найдено {len(imgs)}")
        if not args.send:
            print(f"[ПРОБНЫЙ ПРОГОН] тип=album (sendMediaGroup), "
                  f"chat={chat}, слайдов={len(imgs)}")
            for i, img in enumerate(imgs):
                print(f"  [{i}] {img}")
            if caption:
                print(f"  подпись ({len(caption)} симв.) уйдёт к слайду [0]")
            print("Ничего не отправлено. Для реальной отправки добавь --send")
            return
        media = []
        files = {}
        for i, img in enumerate(imgs):
            key = f"f{i}"
            item = {"type": "photo", "media": f"attach://{key}"}
            if i == 0 and caption:
                item["caption"] = caption
            media.append(item)
            files[key] = img.open("rb")
        import json as _json
        url = f"{API}/bot{token}/sendMediaGroup"
        r = requests.post(url, data={"chat_id": chat, "media": _json.dumps(media)},
                          files=files, timeout=180)
        for fh in files.values():
            fh.close()
        j = r.json()
        if j.get("ok"):
            ids = [m.get("message_id") for m in j["result"]]
            print(f"OK: альбом {len(imgs)} слайдов отправлен, message_ids={ids}, chat={chat}")
            return
        sys.exit(f"TELEGRAM ОТКАЗАЛ: {j}")

    # --- режим одного файла ---
    f = Path(args.file)
    if not f.exists():
        sys.exit(f"Файл не найден: {f}")

    ext = f.suffix.lower()
    is_video = ext in (".mp4", ".mov")
    if ext in (".jpg", ".jpeg", ".png"):
        method, field = "sendPhoto", "photo"
    elif is_video and not args.as_document:
        method, field = "sendVideo", "video"
    else:
        method, field = "sendDocument", "document"

    data = {"chat_id": chat}
    if caption:
        data["caption"] = caption

    # Видео плеером — чтобы Telegram НЕ пережимал: streaming + реальные размеры.
    if method == "sendVideo":
        data["supports_streaming"] = "true"
        meta = probe_video(f)
        if meta:
            data["width"], data["height"], data["duration"] = meta
        else:
            print("  (предупреждение: нет ffprobe/moviepy — размеры видео "
                  "не переданы; на ПК с moviepy будет точнее)")

    if not args.send:
        kind = {"sendPhoto": "photo", "sendVideo": "video",
                "sendDocument": "document"}.get(method, method)
        print(f"[ПРОБНЫЙ ПРОГОН] тип={kind} ({method}), chat={chat}, файл={f}")
        if caption:
            print(f"  подпись ({len(caption)} симв.)")
        if method == "sendVideo":
            if data.get("width"):
                print(f"  размеры видео: {data['width']}x{data['height']}, "
                      f"{data['duration']}с")
            else:
                print("  размеры видео не определены (нет ffprobe/moviepy)")
        print("Ничего не отправлено. Для реальной отправки добавь --send")
        return

    url = f"{API}/bot{token}/{method}"
    with f.open("rb") as fh:
        r = requests.post(url, data=data, files={field: fh}, timeout=180)

    try:
        j = r.json()
    except Exception:
        sys.exit(f"HTTP {r.status_code}: {r.text[:500]}")

    if j.get("ok"):
        mid = j["result"].get("message_id")
        print(f"OK: отправлено методом {method}, message_id={mid}, chat={chat}")
    else:
        sys.exit(f"TELEGRAM ОТКАЗАЛ: {j}")


if __name__ == "__main__":
    main()
