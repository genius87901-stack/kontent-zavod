# -*- coding: utf-8 -*-
"""Пути модуля content_plan относительно корня установки. Никаких абсолютных
путей в коде — всё от каталога с CONTRACT.md через pathlib (Windows в том числе).

Все рантайм-данные лежат в папке `content_plan/` (объявлена в манифесте, folders)
и гитигнорятся локальным content_plan/.gitignore — в репозиторий уезжает только
пример brief.example.json."""
from __future__ import annotations

from pathlib import Path

from .config import install_root

DATA_DIR = "content_plan"          # корневая папка данных модуля
QUEUE_DIR = "content_plan/queue"   # «готово к публикации» (аналог папки «Готово»)

PLAN_FILE = "content_plan.json"
PUBLISH_LOG = "publish_log.jsonl"
VETO_BOARD = "veto_board.md"
BRIEF_EXAMPLE = "brief.example.json"
BRIEF = "brief.json"                # ответы интервью — переиспользуются
PLAN_TABLE = "content_plan.md"      # человекочитаемая таблица плана
SHOWCASE_LOG = "showcase_log.jsonl"  # аудит выкладки в витрину (что и когда ушло)
DELIVERY_PLAN = "delivery.md"        # расписание ежедневной выдачи


def data_dir() -> Path:
    return install_root() / DATA_DIR


def queue_dir() -> Path:
    return install_root() / "content_plan" / "queue"


def plan_path() -> Path:
    return data_dir() / PLAN_FILE


def publish_log_path() -> Path:
    return data_dir() / PUBLISH_LOG


def veto_board_path() -> Path:
    return data_dir() / VETO_BOARD


def brief_example_path() -> Path:
    return data_dir() / BRIEF_EXAMPLE


def brief_path() -> Path:
    """Сохранённые ответы интервью. Переиспользуются следующим интервью как база."""
    return data_dir() / BRIEF


def plan_table_path() -> Path:
    return data_dir() / PLAN_TABLE


def showcase_log_path() -> Path:
    """Аудит витрины: одна строка JSON на каждую попытку выкладки (в т.ч.
    пробный прогон и отказ «витрина недоступна»)."""
    return data_dir() / SHOWCASE_LOG


def delivery_plan_path() -> Path:
    return data_dir() / DELIVERY_PLAN


def ensure_dirs() -> None:
    """Создаёт папки данных модуля. Зовётся кодом, а не установщиком, чтобы
    прототип работал и до полноценной установки."""
    data_dir().mkdir(parents=True, exist_ok=True)
    queue_dir().mkdir(parents=True, exist_ok=True)
