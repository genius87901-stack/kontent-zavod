# -*- coding: utf-8 -*-
"""Банк тем/хуков/смыслов для планировщика — ПОДКЛЮЧАЕМЫЙ ПЕР-КЛИЕНТ.

BUG-2026-08-08 (баг-репорт клиента Елены Сокольской, астрология/мед.
астрология): раньше банк тем планировщика (`planner.TOPIC_BANK`) был захардко-
жен в Python прямо под книгу смыслов Валерии — ни файла данных, ни ключа-
переключателя (аналога `script_bible_file` у script_writer) не существовало.
Подключить книгу смыслов другого клиента к контент-плану было невозможно в
принципе — не только у этого клиента.

Self-contained (не тянет script_writer — граница владения по файлам,
CONTRACT.md §5, остаётся чистой; см. bible.py в script_writer — та же логика
чтения секции тем продублирована здесь намеренно, а не импортирована).

Правило приоритета ПО РУБРИКЕ:
  * рубрика есть в книге смыслов клиента (секция про темы для постов,
    подрубрика «## <имя>» с нумерованными фразами) → берём фразы КЛИЕНТА,
    рубрика остаётся под ЕГО дословным именем (см. BUG-2026-08-08 в bible.py:
    рубрики другой ниши не обязаны совпадать со списком Валерии);
  * рубрики нет в файле клиента (или файла нет вовсе) → берём заготовку из
    `topic_bank.default.json` (пример-донор, помечен source="default") —
    план всё равно получается непустым и неповторяющимся, а не падает.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import config

# см. bible.py (script_writer) — тот же гибкий поиск заголовка секции тем,
# продублирован здесь намеренно (self-contained, без cross-module импорта).
_THEMES_HEADER_RE = re.compile(
    r"^#{1,2}[ \t]*(?=.*\bтем\w*)(?=.*(пост\w*|рубрик\w*)).*$",
    re.IGNORECASE | re.MULTILINE)


def _default_bank_path() -> Path:
    return Path(__file__).resolve().parent / "topic_bank.default.json"


def _load_default_bank() -> dict:
    """{rubric: [(topic, hook, meaning), ...]} из дефолтного JSON-банка.
    Битый/отсутствующий файл → {} (планировщик и на этом не падает)."""
    path = _default_bank_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    bank = {}
    for rubric, triples in raw.items():
        if rubric.startswith("_") or not isinstance(triples, list):
            continue
        bank[rubric] = [tuple(t) for t in triples if isinstance(t, list) and len(t) == 3]
    return bank


def default_bank() -> dict:
    """Публичное имя для нейтральной заготовки банка тем.

    Нужно, чтобы у заготовки был РОВНО ОДИН источник: planner.py раньше держал
    собственную копию литералом, и вычистка одной копии не вычищала вторую
    (BUG-2026-08-08). Читатели снаружи берут банк только отсюда."""
    return _load_default_bank()


def _bible_default_path() -> Path:
    try:
        return config.install_root() / config.bible_file()
    except Exception:
        # Аварийный путь без конфига: имя нейтральное, легаси-имя эксперта-донора
        # берём только если такой файл реально лежит в корне.
        root = Path(__file__).resolve().parents[2]
        for name in ("КНИГА_СМЫСЛОВ.md", "CONTENT_BIBLE_Valeria_Chirkova.md"):
            if (root / name).is_file():
                return root / name
        return root / "КНИГА_СМЫСЛОВ.md"


def _read_client_themes(text: str) -> dict:
    """{rubric (дословно из файла): [phrase, ...]} — та же логика, что
    bible._parse_themes_by_rubric, но без хуков и без канонизации рубрик."""
    m = _THEMES_HEADER_RE.search(text)
    if not m:
        return {}
    block = text[m.end():]
    nxt = re.search(r"\n#\s(?!#)", block)
    if nxt:
        block = block[:nxt.start()]
    themes, current = {}, None
    for line in block.splitlines():
        sub = re.match(r"\s*##+\s+(.+?)\s*$", line)
        if sub:
            current = re.sub(r"\s+", " ", sub.group(1).strip())
            continue
        if current is None:
            continue
        mm = re.match(r"\s*\d+\.\s+(.+?)\s*$", line)
        if not mm:
            continue
        phrase = re.sub(r"\s+", " ", mm.group(1).strip())
        if len(phrase) >= 6:
            themes.setdefault(current, []).append(phrase)
    return themes


def load_topic_bank(bible_path=None, bible_text=None):
    """Собирает рабочий банк тем планировщика.

    Возвращает (bank, meta):
      bank — {rubric: [(topic, hook, meaning), ...]}, topic==hook==фраза
             клиента и meaning="" для рубрик клиента; полная тройка из JSON
             для рубрик-заготовок;
      meta — {"client_bible_ok": bool, "source": {rubric: "client"|"default"},
              "warnings": [str, ...]}.

    `bible_text` — прямая инъекция содержимого (для тестов). В бою читается
    файл по `bible_path`/дефолтному пути (тот же ключ script_bible_file, что
    у script_writer — один ответ клиента подключает книгу смыслов сразу к
    обоим модулям)."""
    default_bank = _load_default_bank()
    warnings = []

    client_ok = False
    client_themes = {}
    if bible_text is not None:
        client_themes = _read_client_themes(bible_text)
        client_ok = True
    else:
        path = Path(bible_path) if bible_path else _bible_default_path()
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, ValueError):
            warnings.append(
                "Файл книги смыслов не найден/не читается — банк тем "
                "контент-плана целиком на заготовках модуля (не клиента).")
        else:
            client_ok = True
            client_themes = _read_client_themes(text)
            if not client_themes:
                warnings.append(
                    "В книге смыслов не нашлась секция с темами по рубрикам "
                    "— банк тем контент-плана на заготовках модуля.")

    bank, source = {}, {}
    for rubric, phrases in client_themes.items():
        if not phrases:
            continue
        bank[rubric] = [(p, p, "") for p in phrases]
        source[rubric] = "client"
    for rubric, triples in default_bank.items():
        if rubric in bank:
            continue
        bank[rubric] = triples
        source[rubric] = "default"

    return bank, {
        "client_bible_ok": client_ok,
        "source": source,
        "warnings": warnings,
    }
