# -*- coding: utf-8 -*-
"""Пути модуля analytics относительно корня установки. Никаких абсолютных путей —
всё от каталога с CONTRACT.md через pathlib (Windows в том числе).

Рантайм-данные лежат в папке `analytics/` (объявлена в манифесте, folders) и
гитигнорятся локальным analytics/.gitignore — в репозиторий из модуля уезжает
только код и упакованный образец `metrics.sample.json` (лежит рядом с кодом, а не
в папке данных, поэтому не гитигнорится)."""
from __future__ import annotations

from pathlib import Path

from .config import install_root

DATA_DIR = "analytics"                       # корневая папка данных модуля
STORE_FILE = "metrics_store.json"            # хранилище нормализованных метрик (срез «сейчас»)
HISTORY_FILE = "metrics_history.jsonl"       # ИСТОРИЯ замеров (по строке на замер)
CACHE_FILE = "live_cache.json"               # последний успешный ответ Upload-Post
RECS_FILE = "recommendations.json"           # последний прогон рекомендаций
FEEDBACK_FILE = "planner_feedback.suggest.json"  # подсказки для планировщика


def data_dir() -> Path:
    return install_root() / DATA_DIR


def store_path() -> Path:
    return data_dir() / STORE_FILE


def history_path() -> Path:
    return data_dir() / HISTORY_FILE


def cache_path() -> Path:
    return data_dir() / CACHE_FILE


def published_log_path() -> Path:
    """`.state/published_ids.jsonl` в корне установки — журнал «наш пост ↔
    request_id», который дописывает autopost.py при реальной публикации."""
    return install_root() / ".state" / "published_ids.jsonl"


def recs_path() -> Path:
    return data_dir() / RECS_FILE


def feedback_path() -> Path:
    return data_dir() / FEEDBACK_FILE


def packaged_sample_path(name: str = "metrics.sample.json") -> Path:
    """Упакованный образец метрик рядом с кодом модуля (коммитится в репозиторий,
    как brief.example.json). Это дефолтный источник провайдера в режиме sample."""
    return Path(__file__).resolve().parent / name


def ensure_dirs() -> None:
    """Создаёт папку данных модуля. Зовётся кодом, а не установщиком, чтобы
    прототип работал и до полноценной установки.

    Кладёт в папку данных `.gitignore` с `*` — рантайм-метрики аккаунта клиента
    НЕ должны попадать в репозиторий (CONTRACT.md §3). В поставке клиенту это не
    git-репозиторий, так что файл там безвреден."""
    dd = data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    gi = dd / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
