# -*- coding: utf-8 -*-
"""Драйвер MAX (мессенджер VK).

СОСТОЯНИЕ (честно). В MAX НЕТ формата «истории» вообще — ни ботом, ни руками.
Поэтому боевой путь здесь такой же, как у Telegram: кадр сторис 1080×1920
уходит ПОСТОМ В КАНАЛ собственным ботом через Bot API. Это пост, а не Story,
и мы помечаем это в каждом ответе полем `limitation` — чтобы никто (ни агент,
ни клиент) не прочитал «accepted» как «вышла история».

ПУТЬ ПУБЛИКАЦИИ — ТРИ ШАГА, так устроен Bot API MAX (в отличие от Telegram,
где файл уходит одним запросом):

  1. POST /uploads?type=image|video  → сервер отвечает {"url": ..., "token": ...}
     Это ещё не загрузка: он выдаёт ОДНОРАЗОВЫЙ адрес, куда класть файл.
  2. POST на выданный url, multipart  → сам файл. Для картинок сервер отвечает
     телом со СВОИМ токеном ({"photos": {"<ключ>": {"token": ...}}}) — он и
     идёт во вложение; для видео берётся токен из шага 1. Поэтому токен ищем
     сначала в ответе загрузки и только потом падаем на токен шага 1.
  3. POST /messages?chat_id=...  → JSON с attachments[{type, payload{token}}].

ПРО БАЗОВЫЙ АДРЕС. Документация MAX (dev.max.ru) называет
`https://platform-api2.max.ru`; в обиходе встречается и `platform-api.max.ru`,
а самый старый `botapi.max.ru` объявлен устаревшим. Адрес вынесен в настройку
`max_api_base` — если MAX его снова поменяет, правится ответом в мастере, а не
патчем кода.

Предохранитель тот же, что у всех драйверов: реальная отправка только при
dry_run=False. По умолчанию — намерение в лог, сеть не трогаем.
"""
from __future__ import annotations

import os

PLATFORM = "max"
ALIASES = ("maks", "макс", "max_messenger")

API_DEFAULT = "https://platform-api2.max.ru"

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif")
_VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm")

#: Потолки MAX на вложение. Больше — сервер откажет, и лучше сказать это до
#: сети: отказ по размеру выглядит как «ошибка API», хотя чинится пересжатием.
_LIMIT_MB = {"image": 50, "video": 250}

_LIMITATION = ("уходит ПОСТ В КАНАЛ, а не история: в MAX формата «истории» нет "
               "ни у ботов, ни у людей")


def _kind_for(media_path):
    """image / video / None по расширению файла."""
    ext = os.path.splitext(str(media_path))[1].lower()
    if ext in _IMAGE_EXT:
        return "image"
    if ext in _VIDEO_EXT:
        return "video"
    return None


def _api_base(cfg):
    return (cfg.any_value("max_api_base") or API_DEFAULT).rstrip("/")


def publish(media_path, caption, link, cfg, dry_run):
    intent = {
        "status": None,
        "platform": PLATFORM,
        "media": str(media_path),
        "dry_run": bool(dry_run),
        "route": "own-bot(MAX Bot API)",
        "limitation": _LIMITATION,
    }

    kind = _kind_for(media_path)
    if kind is None:
        intent["status"] = "error"
        intent["detail"] = ("MAX принимает во вложение картинку (%s) или видео "
                            "(%s), а тут %s."
                            % (", ".join(_IMAGE_EXT), ", ".join(_VIDEO_EXT),
                               os.path.splitext(str(media_path))[1] or "файл без "
                               "расширения"))
        return intent
    intent["kind"] = kind

    # Размер проверяем ДО сети: отказ по потолку иначе прилетит как невнятная
    # ошибка API, хотя лечится пересжатием кадра.
    try:
        mb = os.path.getsize(str(media_path)) / (1024 * 1024)
    except OSError:
        mb = 0.0
    if mb > _LIMIT_MB[kind]:
        intent["status"] = "error"
        intent["detail"] = ("Файл %.1f МБ, а MAX принимает %s до %d МБ. "
                            "Пересожмите кадр."
                            % (mb, kind, _LIMIT_MB[kind]))
        return intent

    token = cfg.secret("max_bot_token")
    chat = cfg.any_value("max_chat_id")

    missing = []
    if not token:
        missing.append("токен бота MAX (max_bot_token)")
    if not chat:
        missing.append("канал MAX (max_chat_id)")
    if missing:
        intent["status"] = "not_configured"
        intent["missing"] = missing
        intent["detail"] = ("Не заполнено: %s. MAX публиковаться не будет."
                            % ", ".join(missing))
        return intent

    # Ссылку MAX отдельным полем не принимает — дописываем в текст, как в TG.
    if link and caption:
        caption = "%s\n%s" % (caption, link)
    elif link:
        caption = link

    base = _api_base(cfg)
    intent["chat"] = str(chat)

    if dry_run:
        intent["status"] = "dry_run"
        intent["endpoint"] = "%s/uploads?type=%s → %s/messages" % (base, kind, base)
        intent["detail"] = ("Готово к отправке в MAX через собственного бота "
                            "(%s, пост в канал). Пробный прогон — ничего не "
                            "отправлено." % kind)
        return intent

    return _send(base, token, chat, media_path, caption, kind, intent)


def _find_token(node):
    """Первый попавшийся 'token' в ответе загрузки, на любой глубине.

    Форма ответа отличается по типу вложения (у картинок токен спрятан внутри
    photos/<ключ>), а гадать по типу — значит ломаться на каждой правке формата.
    Ищем поле по имени.
    """
    if isinstance(node, dict):
        val = node.get("token")
        if isinstance(val, str) and val:
            return val
        for v in node.values():
            found = _find_token(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_token(v)
            if found:
                return found
    return None


def _send(base, bot_token, chat, media_path, caption, kind, intent):  # pragma: no cover - боевой путь
    import requests

    headers = {"Authorization": bot_token}

    # ── шаг 1: получить адрес для загрузки ──
    try:
        r1 = requests.post("%s/uploads" % base, headers=headers,
                           params={"type": kind}, timeout=60)
    except requests.RequestException as err:
        intent["status"] = "error"
        intent["detail"] = "MAX недоступен на шаге /uploads: %s" % err
        return intent
    if r1.status_code >= 400:
        intent["status"] = "error"
        intent["http"] = r1.status_code
        intent["detail"] = ("MAX отказал на /uploads: HTTP %s. Проверьте токен "
                            "бота и адрес API (max_api_base)." % r1.status_code)
        return intent
    try:
        up = r1.json()
    except ValueError:
        intent["status"] = "error"
        intent["detail"] = "MAX ответил на /uploads не-JSON: HTTP %s" % r1.status_code
        return intent

    url = up.get("url")
    token_from_step1 = up.get("token")
    if not url:
        intent["status"] = "error"
        intent["detail"] = "MAX не выдал адрес загрузки в ответе /uploads: %r" % up
        return intent

    # ── шаг 2: положить файл по выданному адресу ──
    try:
        with open(str(media_path), "rb") as fh:
            r2 = requests.post(url, files={"data": (os.path.basename(str(media_path)), fh)},
                               timeout=900)
    except requests.RequestException as err:
        intent["status"] = "error"
        intent["detail"] = "MAX недоступен на загрузке файла: %s" % err
        return intent
    if r2.status_code >= 400:
        intent["status"] = "error"
        intent["http"] = r2.status_code
        intent["detail"] = "MAX отказал при загрузке файла: HTTP %s" % r2.status_code
        return intent
    try:
        body2 = r2.json()
    except ValueError:
        body2 = {}

    attach_token = _find_token(body2) or token_from_step1
    if not attach_token:
        intent["status"] = "error"
        intent["detail"] = ("Файл загружен, но MAX не вернул токен вложения — "
                            "прикрепить нечего. Ответ: %r" % body2)
        return intent

    # ── шаг 3: отправить сообщение в канал ──
    payload = {"attachments": [{"type": kind, "payload": {"token": attach_token}}]}
    if caption:
        payload["text"] = caption
    try:
        r3 = requests.post("%s/messages" % base, headers=headers,
                           params={"chat_id": str(chat)}, json=payload, timeout=180)
    except requests.RequestException as err:
        intent["status"] = "error"
        intent["detail"] = "MAX недоступен на шаге /messages: %s" % err
        return intent

    intent["http"] = r3.status_code
    try:
        body3 = r3.json()
    except ValueError:
        body3 = {}

    if r3.status_code >= 400:
        intent["status"] = "error"
        intent["detail"] = ("MAX отказал на /messages: HTTP %s%s"
                            % (r3.status_code,
                               (" — %s" % body3.get("message")) if body3.get("message") else ""))
        return intent

    mid = ((body3.get("message") or {}).get("body") or {}).get("mid") \
        if isinstance(body3.get("message"), dict) else None
    if mid:
        intent["status"] = "accepted"
        intent["message_id"] = mid
        intent["detail"] = "Опубликовано в канал MAX, mid=%s" % mid
        return intent

    # HTTP 200 без опознаваемого сообщения в ответе — тот же случай, что у
    # стороннего сервиса публикации: могло уйти, а могло и нет. Врать «вышло» нельзя.
    intent["status"] = "unconfirmed"
    intent["detail"] = ("MAX ответил %s, но в ответе нет идентификатора "
                        "сообщения — подтвердить публикацию нечем. Посмотрите "
                        "канал глазами; если поста нет — выложите вручную."
                        % r3.status_code)
    return intent
