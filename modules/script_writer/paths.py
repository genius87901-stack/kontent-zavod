# -*- coding: utf-8 -*-
"""Пути модуля script_writer относительно корня установки. Никаких абсолютных
путей — всё от каталога с CONTRACT.md через pathlib (Windows в том числе).

Рантайм-данные лежат в папке `script_writer/` (объявлена в манифесте, folders) и
гитигнорятся локальным script_writer/.gitignore — в репозиторий уезжает только
пример брифа `brief.example.json`."""
from __future__ import annotations

from pathlib import Path

from .config import install_root

DATA_DIR = "script_writer"                 # корневая папка данных модуля
OUTPUT_DIR = "script_writer/output"        # готовые сценарии (.md/.txt/.json)
BRIEF_EXAMPLE = "brief.example.json"


def data_dir() -> Path:
    return install_root() / DATA_DIR


def output_dir() -> Path:
    return install_root() / "script_writer" / "output"


def brief_example_path() -> Path:
    return data_dir() / BRIEF_EXAMPLE


def ensure_dirs() -> None:
    """Создаёт папки данных модуля. Зовётся кодом, а не установщиком, чтобы
    прототип работал и до полноценной установки.

    Кладёт в корневую папку данных `.gitignore` с `*` — рантайм-сценарии (со
    смыслами клиента) НЕ должны попадать в репозиторий (CONTRACT.md §3). В
    поставке клиенту это не git-репозиторий, так что файл там безвреден."""
    dd = data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    output_dir().mkdir(parents=True, exist_ok=True)
    gi = dd / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
