# -*- coding: utf-8 -*-
"""Чтение конфига модуля script_writer — ЕДИНСТВЕННАЯ точка доступа.

Зеркалит контракт чтения content_plan/story_publisher (CONTRACT.md §1, §3):
  * Корень установки = каталог с CONTRACT.md; все пути через pathlib от него —
    никаких абсолютных путей в коде.
  * Несекретный конфиг: config/factory.config.json (ключи = key из
    config_questions манифеста). У script_writer секретов нет, но интерфейс
    единый (secret-значения читались бы из окружения, а не из файла).
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
    # запасной вариант: .../modules/script_writer/config.py -> parents[2]
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


# --- типизированные удобные чтения умолчаний --------------------------------

def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_duration_sec(default=45) -> int:
    """Ориентировочная длительность ролика (сек), по ней раскладываются дубли."""
    val = _as_int(config_value("script_default_duration_sec", default), default)
    # держим в разумных рамках разговорного Reels
    return max(20, min(90, val))


def default_beats(default=4) -> int:
    """Сколько СРЕДНИХ битов (между хуком и CTA): 3–5. По умолчанию 4."""
    val = _as_int(config_value("script_beats", default), default)
    return max(3, min(5, val))


def code_word(default="") -> str:
    """Кодовое слово CTA. Приоритет: конфиг → (иначе книга смыслов в writer)."""
    raw = config_value("script_code_word")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return default


#: Имя файла книги смыслов по умолчанию — НЕЙТРАЛЬНОЕ. Клиент, у которого ключ
#: конфига не задан, раньше видел в подсказках и ошибках «CONTENT_BIBLE_Valeria_
#: Chirkova.md» — то есть завод просил его положить в корень файл с именем чужого
#: человека. Легаси-имя осталось вторым кандидатом: у эксперта-донора ключ в
#: конфиге не задан, и без этого его установка перестала бы находить книгу.
DEFAULT_BIBLE_NAME = "КНИГА_СМЫСЛОВ.md"
LEGACY_BIBLE_NAMES = ("CONTENT_BIBLE_Valeria_Chirkova.md",)


def bible_file(default=None) -> str:
    """Имя файла книги смыслов в корне установки.

    Порядок: ключ конфига `script_bible_file` → файл с нейтральным именем, если
    он реально лежит в корне → легаси-имя, если лежит оно → нейтральное имя как
    ответ по умолчанию (его и показываем человеку в подсказках)."""
    raw = config_value("script_bible_file")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if default:
        return default
    try:
        root = install_root()
        for name in (DEFAULT_BIBLE_NAME,) + LEGACY_BIBLE_NAMES:
            if (root / name).is_file():
                return name
    except Exception:
        pass
    return DEFAULT_BIBLE_NAME


def voice_gender(default="neutral") -> str:
    """Род реплик рассказчика: neutral / male / female. По умолчанию нейтральный —
    реплики базы написаны без родовых окончаний, чтобы подходить любому эксперту."""
    raw = config_value("script_voice_gender")
    if isinstance(raw, str) and raw.strip().lower() in ("neutral", "male", "female"):
        return raw.strip().lower()
    return default
