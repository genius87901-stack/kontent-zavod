# -*- coding: utf-8 -*-
"""Драйверы площадок для story-publisher: ig / tg / vk / max.

Каждый драйвер экспортирует:
    PLATFORM  — каноническое имя площадки (для отчётов);
    ALIASES   — кортеж псевдонимов, которыми его можно вызвать;
    publish(media_path, caption, link, cfg, dry_run) -> dict  — драйвер.

Единый адаптер (publisher.publish_story) выбирает драйвер по имени площадки.
"""
from __future__ import annotations

# `max` импортируем под псевдонимом: имя модуля совпадает со встроенной
# функцией max(), и голый `from . import max` затенил бы её в этом файле.
from . import instagram, telegram, vk
from . import max as max_messenger

_MODULES = (instagram, telegram, vk, max_messenger)

# Карта «имя/псевдоним -> модуль драйвера».
REGISTRY = {}
for _m in _MODULES:
    REGISTRY[_m.PLATFORM] = _m
    for _alias in getattr(_m, "ALIASES", ()):  # noqa: SLF001
        REGISTRY[_alias] = _m


def get_driver(platform):
    """Вернуть модуль драйвера по имени площадки или None, если неизвестна."""
    if not platform:
        return None
    return REGISTRY.get(str(platform).strip().lower())


def known_platforms():
    """Канонические имена площадок (без псевдонимов)."""
    return sorted({m.PLATFORM for m in _MODULES})
