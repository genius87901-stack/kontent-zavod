# -*- coding: utf-8 -*-
"""Чтение конфига модуля analytics — ЕДИНСТВЕННАЯ точка доступа.

Зеркалит контракт чтения content_plan / script_writer (CONTRACT.md §1, §3):
  * Корень установки = каталог с CONTRACT.md; все пути через pathlib от него —
    никаких абсолютных путей в коде.
  * Несекретный конфиг: config/factory.config.json (ключи = key из
    config_questions манифеста). У analytics секретов нет, но интерфейс единый —
    секреты (токены Insights площадок, когда дойдёт до живого источника) читались
    бы из ОКРУЖЕНИЯ, а не из файла и не из репозитория.
  * Нет файла / битый файл → пустой конфиг → урезанный режим (валидные
    умолчания), а НЕ падение.
"""
from __future__ import annotations

import json
from pathlib import Path


def install_root() -> Path:
    """Каталог с CONTRACT.md — корень установки. Идём вверх от этого файла."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CONTRACT.md").exists():
            return parent
    # запасной вариант: .../modules/analytics/config.py -> parents[2]
    return here.parents[2]


def _config_path() -> Path:
    return install_root() / "config" / "factory.config.json"


def _load_config_file() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def config_value(key, default=None):
    """ЕДИНСТВЕННАЯ функция чтения конфига (только несекретные значения)."""
    return _load_config_file().get(key, default)


# --- типизированные удобные чтения ------------------------------------------

def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --- источник метрик (образец / живой) --------------------------------------

# `sample` — встроенный образец (безопасный дефолт). `upload_post` — живой сбор
# через Upload-Post (тот же сервис, что публикует). `live` — исторический синоним
# `upload_post` (BOX-15), оставлен ради обратной совместимости конфигов.
METRICS_SOURCES = ("sample", "upload_post", "live")
LIVE_SOURCES = ("upload_post", "live")
DEFAULT_METRICS_SOURCE = "sample"


def metrics_source(default=DEFAULT_METRICS_SOURCE) -> str:
    """Откуда брать метрики: `sample` — встроенный образец-mock (безопасный
    дефолт, ничего не зовёт), `upload_post`/`live` — живой сбор через Upload-Post.
    Живой путь идёт в платный сервис ТОЛЬКО при наличии ключа UPLOAD_POST_API_KEY;
    без ключа модуль честно берёт образец (providers.fetch_metrics). Незнакомое
    значение → безопасный `sample`."""
    raw = config_value("analytics_metrics_source", default)
    if not isinstance(raw, str):
        return default
    val = raw.strip().lower()
    return val if val in METRICS_SOURCES else default


def live_source_requested() -> bool:
    """Просили ли живой источник (`upload_post`/`live`) в конфиге. Само по себе это
    НИЧЕГО не публикует и не звонит в платный API без ключа — живой сбор
    активируется при снятии метрик и только при наличии ключа."""
    return metrics_source() in LIVE_SOURCES


def profile_username(default="") -> str:
    """Имя профиля Upload-Post для агрегатов профиля/суммарных показов
    (`/api/analytics/{username}`, `/total-impressions/{username}`). Несекретное —
    это не ключ. Пусто → агрегаты профиля не собираем (по постам работаем всё
    равно)."""
    raw = config_value("analytics_profile_username", default)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return default


def sample_file(default="metrics.sample.json") -> str:
    """Имя файла образца метрик. Вынесено в конфиг, чтобы модуль переиспользовался
    под другой аккаунт/выгрузку без правки кода (как script_bible_file)."""
    raw = config_value("analytics_sample_file")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return default


# --- пороги правил рекомендаций ---------------------------------------------
# Все пороги читаются из конфига, но имеют разумные умолчания — модуль работает и
# без единой настройки. Проценты хранятся как проценты (35 = 35 %).

def min_retention_pct(default=35) -> int:
    """Ниже этого % досмотра/удержания ролик считаем недосмотренным (слабый хук /
    затянуто). Держим в разумных рамках 5..90."""
    val = _as_int(config_value("analytics_min_retention_pct", default), default)
    return max(5, min(90, val))


def weak_reach_drop_pct(default=30) -> int:
    """На сколько % ниже вашего медианного охвата (по площадке+формату) охват
    поста считать провалом. Рамки 5..80."""
    val = _as_int(config_value("analytics_weak_reach_drop_pct", default), default)
    return max(5, min(80, val))


def min_ctr_pct(default=1.0) -> float:
    """Ниже этого % переходов по ссылке (клики/охват) — мало переходов, нужен
    явный CTA. Тонкая настройка, обычно не спрашивается у клиента."""
    val = _as_float(config_value("analytics_min_ctr_pct", default), default)
    return max(0.05, min(20.0, val))


def min_save_rate_pct(default=1.5) -> float:
    """Ниже этого % сохранений (сохранения/охват) — контенту не хватает
    пользы-выжимки, которую хочется сохранить."""
    val = _as_float(config_value("analytics_min_save_rate_pct", default), default)
    return max(0.1, min(20.0, val))


def min_engagement_rate_pct(default=5.0) -> float:
    """Ниже этого % вовлечённости (реакции/охват) — контент не цепляет, нужен
    вопрос/провокация/голосование."""
    val = _as_float(config_value("analytics_min_engagement_rate_pct", default),
                    default)
    return max(0.1, min(50.0, val))


def thresholds() -> dict:
    """Собранные пороги — одним словарём для движка рекомендаций и doctor."""
    return {
        "retention_pct": min_retention_pct(),
        "reach_drop_pct": weak_reach_drop_pct(),
        "ctr_pct": min_ctr_pct(),
        "save_rate_pct": min_save_rate_pct(),
        "engagement_rate_pct": min_engagement_rate_pct(),
    }
