# -*- coding: utf-8 -*-
"""Шов с публикацией: читаем журнал `.state/published_ids.jsonl`.

`autopost.persist_published` при РЕАЛЬНОЙ отправке дописывает туда по строке на
площадку с ровно теми полями, которые нужны живому сбору:

    {"request_id": …, "platform": "instagram", "platforms": [...], "user": …,
     "post_id": "<материал>_<площадка>", "material": …, "media_type": …,
     "published_at": "…UTC ISO…", "status": …}

Отсюда аналитика берёт СПИСОК ПОСТОВ для живого снятия метрик — без ручной
выгрузки и без правки чужого модуля. Мы журнал только ЧИТАЕМ, никогда не пишем.

Файл может отсутствовать (ещё ничего не публиковали), быть пустым или содержать
битые строки — всё это НЕ ошибка: возвращаем то, что удалось прочитать.
"""
from __future__ import annotations

import json

from . import paths

# Площадки Upload-Post → наш внутренний формат карточки. Формат публикации
# (карусель/reels/пост) выводим из media_type, который пишет autopost.
MEDIA_TYPE_TO_FORMAT = {
    "photos": "carousel",
    "photo": "carousel",
    "video": "reels",
    "text": "post",
}


def read_log(path=None) -> list:
    """Сырые строки журнала публикаций. Нет файла → []. Битые строки пропускаем."""
    path = path or paths.published_log_path()
    if not path.exists():
        return []
    out = []
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("request_id"):
                    out.append(rec)
    except OSError:
        return []
    return out


def to_cards(rows=None, platforms=None) -> list:
    """Строки журнала → карточки постов в том виде, который ждёт
    `providers.fetch_metrics` (post_id / platform / format / request_id / user).

    Дедуплицируем по (request_id, платформа): повторный прогон публикации мог
    дописать строку заново. Площадки, которые Upload-Post аналитикой не покрывает
    (VK), не отбрасываем — пусть честно отвалятся с причиной на своём уровне."""
    rows = read_log() if rows is None else rows
    want = {str(p).lower() for p in platforms} if platforms else None
    seen, cards = set(), []
    for rec in rows:
        plat = str(rec.get("platform") or "").strip().lower()
        if want and plat not in want:
            continue
        key = (rec.get("request_id"), plat)
        if key in seen:
            continue
        seen.add(key)
        media = str(rec.get("media_type") or "").strip().lower()
        cards.append({
            "post_id": rec.get("post_id") or "%s_%s" % (rec.get("material") or
                                                        "post", plat),
            "platform": plat,
            "format": MEDIA_TYPE_TO_FORMAT.get(media, "post"),
            "topic": rec.get("material") or "",
            "rubric": "",
            "published_at": rec.get("published_at") or "",
            "request_id": rec.get("request_id"),
            "user": rec.get("user") or "",
            "url": rec.get("url") or "",
            "platform_post_id": rec.get("platform_post_id") or "",
        })
    return cards


def summary() -> dict:
    """Сводка для doctor: есть ли журнал, сколько в нём публикаций и площадок."""
    path = paths.published_log_path()
    rows = read_log(path)
    return {"exists": path.exists(), "rows": len(rows),
            "requests": len({r.get("request_id") for r in rows}),
            "platforms": sorted({str(r.get("platform") or "?").lower()
                                 for r in rows})}
