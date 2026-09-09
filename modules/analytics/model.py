# -*- coding: utf-8 -*-
"""Схема МЕТРИК опубликованного поста, производные ставки, хранилище и валидация.

Запись метрик — простой dict (как content_plan/model.py и script_writer/model.py),
а не dataclass: меньше магии, легче сериализуется, работает на любом Python 3.9+.

Единый нормализованный вид метрик (не все поля есть на всех площадках → None,
если площадка такого не даёт). Провайдеры (providers.py) переводят «сырые» поля
Insights площадки в этот вид, а движок рекомендаций работает уже только с ним.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import paths

SCHEMA_VERSION = 1

# Площадки и форматы (справочники — для валидации и группировки базовой линии).
PLATFORMS = ["instagram", "telegram", "vk"]
FORMATS = ["reels", "carousel", "post", "story", "video"]

# Нормализованные поля метрик. None = площадка такого не отдаёт (не 0!).
METRIC_FIELDS = [
    "reach",            # уникальный охват
    "impressions",      # показы (могут быть > охвата)
    "views",            # просмотры видео / плеи (для reels/видео)
    "avg_watch_sec",    # средняя длительность просмотра
    "video_len_sec",    # длина ролика
    "retention_pct",    # досмотр/удержание в %, если площадка отдаёт напрямую
    "likes",
    "comments",
    "saves",            # сохранения (IG); TG/VK — обычно None
    "shares",           # поделились в сообщении
    "forwards",         # репосты/пересылки (TG forwards, VK reposts)
    "profile_visits",   # переходы в профиль
    "link_clicks",      # переходы по ссылке (website taps / клики) — «переходы»
    "follows",          # новые подписки с поста
]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def empty_metrics() -> dict:
    return {k: None for k in METRIC_FIELDS}


# --- производные ставки ------------------------------------------------------

def _num(v):
    """Число или None. Отрицательные и мусор → None (защита от битых выгрузок)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and v >= 0:
        return v
    return None


def _base_audience(m: dict):
    """Знаменатель ставок: охват → показы → просмотры (первое доступное > 0).
    На TG/VK охвата как такового нет — берём просмотры."""
    for key in ("reach", "impressions", "views"):
        val = _num(m.get(key))
        if val and val > 0:
            return val
    return None


def _engagement_sum(m: dict):
    parts = [_num(m.get(k)) for k in ("likes", "comments", "saves", "shares",
                                      "forwards")]
    parts = [p for p in parts if p is not None]
    return sum(parts) if parts else None


def _pct(numerator, denominator):
    n = _num(numerator)
    d = _num(denominator)
    if n is None or not d:
        return None
    return round(n / d * 100.0, 2)


def derive(record: dict) -> dict:
    """Считает производные ставки из нормализованных метрик и кладёт их в
    record['derived']. Всё, чего не хватает данных, → None (не 0)."""
    m = record.get("metrics") or {}
    base = _base_audience(m)

    # Досмотр: приоритет — прямое значение площадки, иначе avg_watch/len.
    retention = _num(m.get("retention_pct"))
    if retention is None:
        aw, ln = _num(m.get("avg_watch_sec")), _num(m.get("video_len_sec"))
        if aw is not None and ln:
            retention = round(aw / ln * 100.0, 2)

    eng = _engagement_sum(m)
    derived = {
        "base_audience": base,
        "retention_pct": retention,
        "engagement": eng,
        "engagement_rate_pct": _pct(eng, base),
        "save_rate_pct": _pct(m.get("saves"), base),
        "ctr_pct": _pct(m.get("link_clicks"), base),
        "follow_rate_pct": _pct(m.get("follows"), base),
    }
    record["derived"] = derived
    return derived


def new_record(post: dict, metrics: dict, source: str) -> dict:
    """Собирает запись хранилища из карточки поста и нормализованных метрик."""
    rec = {
        "post_id": str(post.get("post_id") or post.get("id") or "post"),
        "platform": str(post.get("platform") or "").strip().lower(),
        "format": str(post.get("format") or "").strip().lower(),
        "rubric": post.get("rubric") or "",
        "topic": post.get("topic") or "",
        "published_at": post.get("published_at") or "",
        "url": post.get("url") or "",
        "source": source,
        "fetched_at": now_iso(),
        "metrics": {k: metrics.get(k) for k in METRIC_FIELDS},
    }
    derive(rec)
    return rec


# --- хранилище (json в своей папке) -----------------------------------------

def empty_store() -> dict:
    return {"version": SCHEMA_VERSION, "updated": now_iso(), "posts": []}


def load_store(path: Path = None) -> dict:
    path = path or paths.store_path()
    if not path.exists():
        return empty_store()
    with path.open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("metrics_store.json повреждён: ожидался объект хранилища.")
    data.setdefault("posts", [])
    return data


def save_store(store: dict, path: Path = None) -> None:
    path = path or paths.store_path()
    paths.ensure_dirs()
    store["updated"] = now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)  # атомарная замена — не оставить полуфайл


def upsert(store: dict, record: dict) -> None:
    """Кладёт запись по post_id (перезапись, если уже была)."""
    posts = store.setdefault("posts", [])
    for i, p in enumerate(posts):
        if p.get("post_id") == record.get("post_id"):
            posts[i] = record
            return
    posts.append(record)


def find_post(store: dict, post_id: str):
    for p in store.get("posts", []):
        if p.get("post_id") == post_id:
            return p
    return None


def baseline_reach(store: dict, platform: str, fmt: str, exclude_id=None):
    """Медианный охват (или база аудитории) по площадке+формату из истории —
    точка отсчёта для правила «охват ниже среднего». Нужна история из ≥3 постов,
    иначе None (честно: живых данных ещё мало — см. DESIGN §7)."""
    vals = []
    for p in store.get("posts", []):
        if p.get("platform") != platform or p.get("format") != fmt:
            continue
        if exclude_id and p.get("post_id") == exclude_id:
            continue
        base = (p.get("derived") or {}).get("base_audience")
        if isinstance(base, (int, float)) and base > 0:
            vals.append(base)
    if len(vals) < 3:
        return None
    vals.sort()
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


# --- валидация ---------------------------------------------------------------

def validate(record: dict) -> list:
    """Возвращает список проблем схемы (пустой = ок). Дёргается doctor'ом/тестами."""
    problems = []
    if not str(record.get("post_id", "")).strip():
        problems.append("нет post_id")
    if record.get("platform") not in PLATFORMS:
        problems.append("неизвестная площадка %r" % record.get("platform"))
    m = record.get("metrics")
    if not isinstance(m, dict):
        problems.append("нет блока метрик")
        return problems
    if _base_audience(m) is None:
        problems.append("нет ни охвата, ни показов, ни просмотров — не от чего "
                        "считать ставки")
    for k, v in m.items():
        if k not in METRIC_FIELDS:
            problems.append("лишнее поле метрики %r" % k)
        elif v is not None and not isinstance(v, (int, float)):
            problems.append("метрика %r не число: %r" % (k, v))
    return problems


def counts(store: dict) -> dict:
    """Сколько постов по площадкам — для doctor/status."""
    out = {}
    for p in store.get("posts", []):
        plat = p.get("platform", "?")
        out[plat] = out.get(plat, 0) + 1
    return out
