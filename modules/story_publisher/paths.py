# -*- coding: utf-8 -*-
"""Пути модуля относительно корня установки. Никаких абсолютных путей в коде —
всё считается от каталога с CONTRACT.md через pathlib (кроссплатформенно,
Windows в том числе)."""
from __future__ import annotations

from pathlib import Path

from .config import install_root

# Папки из манифеста (folders) — создаются установщиком относительно корня.
INBOX = "inbox/stories"
OUTPUT = "output/stories"
REMINDERS = "reminders"


def module_folder(rel):
    """Абсолютный (на рантайме) путь к папке модуля по её относительному имени
    из манифеста. rel — POSIX-строка вида "reminders"."""
    return install_root().joinpath(*rel.split("/"))


def resolve_media(media_path):
    """Путь к медиа. Абсолютный оставляем как есть; относительный считаем от
    корня установки. Возвращаем Path (существование проверяет вызывающий)."""
    p = Path(media_path)
    if p.is_absolute():
        return p
    return install_root() / p
