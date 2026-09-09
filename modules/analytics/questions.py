# -*- coding: utf-8 -*-
"""Структурированный список вопросов под аналитику опубликованного контента.

`input()` внутри модулей запрещён (CONTRACT.md §3). «Диалог» ведёт Claude Code /
мастер установки: зачитывает эти вопросы человеку и складывает ответы в конфиг.
Модуль вопросы только ОБЪЯВЛЯЕТ и печатает — сам не спрашивает.
"""
from __future__ import annotations

QUESTIONS = [
    {
        "key": "analytics_metrics_source",
        "ask": "Откуда брать метрики опубликованных постов?",
        "type": "choice",
        "example": "sample",
        "help": "sample — встроенный образец (для настройки и демонстрации, "
                "ничего никуда не звонит). live — живые Insights площадок; пока "
                "не подключено, это будущий этап, и модуль всё равно возьмёт "
                "образец. Без ответа берём sample.",
    },
    {
        "key": "analytics_min_retention_pct",
        "ask": "Ниже какого % досмотра считать, что ролик недосматривают?",
        "type": "int",
        "example": "35",
        "help": "Для Reels/видео. Если досмотр (сколько в среднем смотрят от "
                "длины) ниже — совет усилить хук и сократить. Без ответа берём 35.",
    },
    {
        "key": "analytics_weak_reach_drop_pct",
        "ask": "На сколько % ниже вашего среднего охват считать провалом?",
        "type": "int",
        "example": "30",
        "help": "Модуль сравнивает охват поста с вашей медианой по этой площадке "
                "и формату (нужно ≥3 прошлых поста). Провал → совет про время "
                "выхода и обложку. Без ответа берём 30.",
    },
    {
        "key": "analytics_sample_file",
        "ask": "Как называется файл с выгрузкой метрик (если своя)?",
        "type": "string",
        "example": "metrics.sample.json",
        "help": "Нужен, только если кладёте собственную выгрузку метрик под своим "
                "именем в папку analytics. Без ответа берём встроенный образец "
                "metrics.sample.json.",
    },
]


def render(prefix: str = "") -> str:
    lines = []
    for i, q in enumerate(QUESTIONS, 1):
        lines.append("%s%d. %s" % (prefix, i, q["ask"]))
        if q.get("example"):
            lines.append("%s   пример: %s" % (prefix, q["example"]))
        if q.get("help"):
            lines.append("%s   зачем: %s" % (prefix, q["help"]))
    return "\n".join(lines)


def keys():
    return [q["key"] for q in QUESTIONS]


if __name__ == "__main__":
    print("=== Вопросы под аналитику контента (задаёт Claude Code, не модуль) ===")
    print(render())
