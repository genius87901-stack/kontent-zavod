# -*- coding: utf-8 -*-
"""Режим напоминания: когда автопубликация невозможна (например, сторис со
ссылкой в Instagram — Upload-Post ссылок/стикеров в сторис не умеет), задача не
теряется, а уходит человеку памяткой в папку reminders.

Секреты сюда НЕ пишутся — только медиа, подпись и ссылка."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .paths import module_folder, REMINDERS


def _timestamp():
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def write_reminder(platform, media_path, caption, link, client_id=None, reason=""):
    """Записать памятку о ручной публикации. Возвращает путь к созданному файлу.

    Папка reminders создаётся установщиком; на всякий случай создаём её и тут
    (mkdir parents/exist_ok — идемпотентно, без падения)."""
    folder = module_folder(REMINDERS)
    folder.mkdir(parents=True, exist_ok=True)

    who = client_id or "default"
    name = "%s_%s_%s.txt" % (_timestamp(), platform, who)
    path = folder / name

    lines = [
        "ТРЕБУЕТСЯ РУЧНАЯ ПУБЛИКАЦИЯ",
        "",
        "Площадка : %s" % platform,
        "Клиент   : %s" % who,
        "Медиа    : %s" % media_path,
    ]
    if reason:
        lines.append("Причина  : %s" % reason)
    if link:
        lines.append("Ссылка   : %s" % link)
    lines.append("")
    lines.append("Подпись:")
    for row in (caption or "").splitlines() or [""]:
        lines.append("   " + row)
    lines.append("")
    lines.append("Автопубликация НЕ выполнена. Опубликуйте сторис вручную и "
                 "добавьте ссылку/стикер в приложении.")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
