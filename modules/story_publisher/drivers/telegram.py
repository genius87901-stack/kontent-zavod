# -*- coding: utf-8 -*-
"""Драйвер Telegram.

СОСТОЯНИЕ (честно). Классические Stories в Telegram — фича пользователя, и ботом
они не постятся. Поэтому боевой путь здесь — публикация
сторис-творческого (кадр 1080×1920) В КАНАЛ собственным ботом через Bot API,
ровно как это уже делает завод в `tg_send.py` (архив @chirkova_arhiv). Это
ПОСТ В КАНАЛ, а не нативная Story — ограничение помечаем честно.

  * Если задан tg_bot_token — путь собственного бота (Bot API, голый requests,
    без aiogram): .png/.jpg → sendPhoto, .mp4/.mov → sendVideo(supports_streaming).
    Цель канала — tg_chat_id (env TG_CHAT_ID).
  * Если tg_bot_token не задан — публиковать НЕЧЕМ, и драйвер говорит это
    вслух. Запасного пути через сторонний сервис больше нет (04.09.2026,
    решение владельца): он скрывал неподключённый канал клиента.

Предохранитель: реальная отправка только при dry_run=False. По умолчанию —
намерение в лог, сеть не трогаем.
"""
from __future__ import annotations

import os

PLATFORM = "telegram"
ALIASES = ("tg", "tgram")

API = "https://api.telegram.org"

_PHOTO_EXT = (".jpg", ".jpeg", ".png")
_VIDEO_EXT = (".mp4", ".mov")


def _method_for(media_path):
    """(method, field) по расширению — та же логика, что в tg_send.py завода."""
    ext = os.path.splitext(str(media_path))[1].lower()
    if ext in _PHOTO_EXT:
        return "sendPhoto", "photo"
    if ext in _VIDEO_EXT:
        return "sendVideo", "video"
    return "sendDocument", "document"


def publish(media_path, caption, link, cfg, dry_run):
    intent = {
        "status": None,
        "platform": PLATFORM,
        "media": str(media_path),
        "dry_run": bool(dry_run),
    }

    bot_token = cfg.secret("tg_bot_token")

    # ПРИОРИТЕТ: настоящая Telegram Story в бизнес-аккаунт (напр. @valeria_chirkova)
    # через business connection. Требует: у аккаунта Telegram Premium+Business,
    # бот включён в бизнес-режим у @BotFather (can_connect_to_business), и аккаунт
    # подключил бота в Настройки → Telegram для бизнеса → Чат-боты с правом на
    # истории. Тогда бот получает business_connection_id и постит postStory.
    biz = cfg.secret("tg_business_connection_id")
    if bot_token and biz:
        intent["route"] = "business-story(postStory)"
        if dry_run:
            intent["status"] = "dry_run"
            intent["endpoint"] = "%s/bot<token>/postStory" % API
            intent["detail"] = ("Готово к публикации настоящей Telegram Story в "
                                "бизнес-аккаунт (postStory). Пробный прогон — "
                                "ничего не отправлено.")
            return intent
        return _post_story_business(bot_token, biz, media_path, caption, link, intent)

    if bot_token:
        # Путь собственного бота (Bot API завода). Пост в канал.
        # Цель сторис: сначала персональный TG_STORY_CHAT_ID (публичный канал
        # аудитории, напр. @aianalyticc), потом общий TG_CHAT_ID (архив).
        # any_value: оба имени и ОБА источника (окружение и несекретный конфиг
        # мастера, без учёта регистра). Раньше спрашивали только окружение — и
        # отвечали «не задан канал» клиенту, который на вопрос про канал ответил:
        # мастер кладёт TG_CHAT_ID в config/factory.config.json, а не в .env.
        chat = cfg.any_value("tg_story_chat_id", "tg_chat_id")
        method, field = _method_for(media_path)
        intent["route"] = "own-bot(Bot API)"
        intent["method"] = method
        intent["limitation"] = ("уходит ПОСТ В КАНАЛ (как архив завода), а не "
                                "нативная Story — классические Stories ботом не "
                                "постятся")
        if link and caption:
            caption = "%s\n%s" % (caption, link)
        elif link:
            caption = link

        if not chat:
            intent["status"] = "not_configured"
            intent["missing"] = ["канал Telegram (tg_chat_id / TG_CHAT_ID)"]
            intent["detail"] = ("Есть токен бота, но не задан канал (TG_CHAT_ID). "
                                "Telegram публиковаться не будет.")
            return intent

        if dry_run:
            intent["status"] = "dry_run"
            intent["endpoint"] = "%s/bot<token>/%s" % (API, method)
            intent["chat"] = str(chat)
            intent["detail"] = ("Готово к отправке в Telegram через собственного "
                                "бота (%s, пост в канал). Пробный прогон — ничего "
                                "не отправлено." % method)
            return intent

        return _send_bot(bot_token, chat, media_path, caption, method, field, intent)

    # 🔴 ЗАПАСНОГО ПУТИ БОЛЬШЕ НЕТ (решение владельца 04.09.2026).
    # Здесь стоял обход через сторонний сервис публикации. Убран сознательно,
    # по двум причинам.
    # Первая — решение владельца: из сторонних сервисов остаются только SmmBox и
    # прямой Telegram Bot API, прочие уходят из заводов целиком.
    # Вторая важнее и техническая: обход СКРЫВАЛ настоящую беду. Клиент, у
    # которого не заполнен токен бота, получал публикацию чужим сервисом и
    # никогда не узнавал, что его собственный канал не подключён, — пока не
    # кончался чужой ключ. Теперь незаполненный токен виден сразу и называется.
    intent["route"] = "telegram-bot(none)"
    intent["status"] = "not_configured"
    intent["missing"] = ["токен бота Telegram (tg_bot_token)",
                         "номер канала (tg_chat_id)"]
    intent["detail"] = (
        "Не подключён собственный бот Telegram — сторис публиковать нечем. "
        "Нужны токен бота и номер канала; подключается один раз, помощник "
        "проведёт по шагам. Другого пути в Telegram у завода нет: сторонние "
        "сервисы из публикации убраны, а нативные Stories ботом не постятся "
        "в принципе.")
    return intent


def _ensure_story_jpeg(media_path):
    """Telegram Story требует JPEG (PNG → 'can't download or generate the file').
    Фото не-JPEG конвертируем в temp .jpg 1080×1920 RGB. Возвращает (путь, temp?)."""
    ext = os.path.splitext(str(media_path))[1].lower()
    if ext in (".jpg", ".jpeg") or ext in _VIDEO_EXT:
        return str(media_path), False
    import tempfile
    from PIL import Image
    im = Image.open(str(media_path)).convert("RGB")
    fd, jpg = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    im.save(jpg, "JPEG", quality=92)
    return jpg, True


def _post_story_business(bot_token, biz_id, media_path, caption, link, intent):  # pragma: no cover - боевой путь
    """Настоящая Telegram Story в бизнес-аккаунт через business connection.
    postStory(business_connection_id, content=InputStoryContent, active_period)."""
    import json as _json
    import requests

    intent["route"] = "business-story(postStory)"
    src, is_temp = _ensure_story_jpeg(media_path)
    ext = os.path.splitext(src)[1].lower()
    is_video = ext in _VIDEO_EXT
    content = {"type": "video" if is_video else "photo",
               ("video" if is_video else "photo"): "attach://media"}
    data = {"business_connection_id": biz_id,
            "content": _json.dumps(content),
            "active_period": 86400}
    if caption:
        data["caption"] = caption[:2048]
    try:
        with open(src, "rb") as fh:
            resp = requests.post("%s/bot%s/postStory" % (API, bot_token),
                                 data=data,
                                 files={"media": (os.path.basename(src), fh)},
                                 timeout=180)
    finally:
        if is_temp:
            try:
                os.unlink(src)
            except OSError:
                pass
    try:
        body = resp.json()
    except ValueError:
        intent["status"] = "error"; intent["detail"] = "postStory не-JSON: HTTP %s" % resp.status_code
        return intent
    if body.get("ok"):
        intent["status"] = "accepted"
        intent["story_id"] = body.get("result", {}).get("id")
        intent["detail"] = ("Опубликована Telegram Story в бизнес-аккаунт, id=%s"
                            % intent["story_id"])
    else:
        intent["status"] = "error"
        intent["detail"] = ("Telegram postStory отказал: %s. Проверь: аккаунт "
                            "подключил бота в «Telegram для бизнеса → Чат-боты» с "
                            "правом на истории; бот в бизнес-режиме у @BotFather."
                            % body.get("description"))
    return intent


def _send_bot(bot_token, chat, media_path, caption, method, field, intent):  # pragma: no cover - боевой путь
    import requests

    data = {"chat_id": str(chat)}
    if caption:
        data["caption"] = caption
    if method == "sendVideo":
        data["supports_streaming"] = "true"

    url = "%s/bot%s/%s" % (API, bot_token, method)
    with open(str(media_path), "rb") as fh:
        resp = requests.post(url, data=data,
                             files={field: (os.path.basename(str(media_path)), fh)},
                             timeout=180)
    try:
        body = resp.json()
    except ValueError:
        intent["status"] = "error"
        intent["http"] = resp.status_code
        intent["detail"] = "Telegram ответил не-JSON: HTTP %s" % resp.status_code
        return intent

    if body.get("ok"):
        intent["status"] = "accepted"
        intent["message_id"] = body.get("result", {}).get("message_id")
        intent["detail"] = ("Опубликовано в канал Telegram, message_id=%s"
                            % intent["message_id"])
    else:
        intent["status"] = "error"
        intent["http"] = resp.status_code
        intent["detail"] = "Telegram отказал: %s" % body.get("description", body)
    return intent
