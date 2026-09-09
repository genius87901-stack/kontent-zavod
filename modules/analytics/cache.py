# -*- coding: utf-8 -*-
"""Кэш ПОСЛЕДНЕГО УСПЕШНОГО ответа Upload-Post + отметка «когда обновлялись».

Зачем. Клиент работает на Windows без GPU и часто офлайн. Аналитика — сетевая
функция, но она НЕ имеет права ломать офлайн-установку. Поэтому:

  * каждый успешный живой ответ кладём на диск (`analytics/live_cache.json`);
  * когда сети/ключа нет, живой сбор берёт ПОСЛЕДНИЙ УСПЕШНЫЙ ответ из кэша и
    честно помечает данные как «из кэша от <дата>», а не падает и не врёт свежестью;
  * doctor показывает, когда было последнее успешное обновление, — это и есть
    ответ на вопрос «а живой сбор вообще хоть раз сработал?».

Кэш — обычный JSON в папке данных модуля (она гитигнорится, см. paths.ensure_dirs),
запись атомарная через .tmp + replace. Ничего секретного внутрь не кладём: ключа
там нет, только ответы сервиса и отметки времени.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import paths

CACHE_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty() -> dict:
    return {"version": CACHE_VERSION, "entries": {}}


def load() -> dict:
    """Читает кэш. Нет файла / битый файл → пустой кэш (урезанный режим, НЕ
    падение): испорченный кэш не имеет права уронить аналитику."""
    path = paths.cache_path()
    if not path.exists():
        return _empty()
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return _empty()
    return data


def save(cache: dict) -> None:
    path = paths.cache_path()
    paths.ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError:
        # Не можем писать (диск/права) — это не повод ронять сбор метрик.
        return


def put(name: str, payload) -> None:
    """Запомнить успешный ответ под именем (`cached:<user>:<platform>`, `status:<id>`…)."""
    cache = load()
    cache.setdefault("entries", {})[str(name)] = {
        "saved_at": _now(), "payload": payload}
    cache["last_success"] = _now()
    save(cache)


def get(name: str):
    """(payload, saved_at) последнего успешного ответа или (None, None)."""
    entry = load().get("entries", {}).get(str(name))
    if not isinstance(entry, dict):
        return None, None
    return entry.get("payload"), entry.get("saved_at")


def last_success() -> "str | None":
    """Когда живой сбор последний раз реально сработал (ISO-8601 UTC) или None.
    Это главный индикатор для доктора: «данные живые» vs «ни разу не тянули»."""
    val = load().get("last_success")
    return val if isinstance(val, str) and val else None


def age_hours(stamp: str = None):
    """Сколько часов прошло с отметки. None, если отметки нет/она нечитаема."""
    stamp = stamp or last_success()
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    return round(delta.total_seconds() / 3600.0, 1)


def clear() -> None:
    """Сбросить кэш (для тестов и для «начать с чистого листа»)."""
    save(_empty())
