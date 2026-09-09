# -*- coding: utf-8 -*-
"""Схема СЦЕНАРИЯ СЪЁМКИ, справочники допустимых значений, чтение/запись .json.

Сценарий — простой dict (как content_plan/model.py), а не dataclass: меньше
магии, легче сериализуется, работает на любом Python 3.9+.

ПРОСТОЙ ВЫВОД (по просьбе эксперта): дубль несёт ровно то, что нужно на съёмке —
что сказать (`say`), что показать (`show`), длительность и субтитр. Раскладку
монтажа и тип плана камеры из вывода убрали. Роль дубля в арке (`intent`
hook/point/emotion/cta) остаётся служебным полем — по нему собирается драматургия
и валидируется «хук в начале, CTA в конце», но в человекочитаемый .txt/.md
эксперту она печатается лишь как короткая пометка арки, а не как режиссёрский план.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import paths

SCHEMA_VERSION = 2

# --- справочники (валидные значения) ---------------------------------------

# Роль дубля в арке — таксономия talk_plan (hook/point/emotion/cta). Служебное
# поле: держит драматургию (хук в начале, CTA в конце) и ротацию арок. НЕ
# режиссёрский план — раскладку монтажа и тип плана сценарист больше не диктует.
INTENT_HOOK = "hook"
INTENT_POINT = "point"
INTENT_EMOTION = "emotion"
INTENT_CTA = "cta"
INTENTS = [INTENT_HOOK, INTENT_POINT, INTENT_EMOTION, INTENT_CTA]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# --- слаг для имени файла ---------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slug(text: str, maxlen: int = 40) -> str:
    """Устойчивый ASCII-слаг из русской строки — для имени файла сценария.
    Детерминирован; пустой вход → 'script'."""
    text = (text or "").strip().lower()
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ord(ch) < 128:
            out.append(ch)
        elif ch in " -_/\\":
            out.append("-")
        # прочее (пунктуация, эмодзи) выбрасываем
    s = re.sub(r"-+", "-", "".join(out)).strip("-")
    s = s[:maxlen].strip("-")
    return s or "script"


# --- чтение / запись --------------------------------------------------------

def save_json(script: dict, path: Path = None) -> Path:
    """Атомарно пишет .json сценария (машинный вид)."""
    paths.ensure_dirs()
    if path is None:
        path = paths.output_dir() / (script.get("slug", "script") + ".json")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(script, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return path


def load_json(path: Path) -> dict:
    with Path(path).open(encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Файл сценария повреждён: ожидался объект сценария.")
    return data


def validate(script: dict) -> list:
    """Возвращает список проблем схемы (пустой список = всё ок). Дёргается
    doctor'ом и тестами — гарантирует, что арка на месте (хук в начале, CTA в
    конце), у каждого дубля есть реплика/показ/длительность и задано кодовое слово.
    Раскладку монтажа и тип плана больше не проверяем — их в дубле нет."""
    problems = []
    shots = script.get("shots") or []
    if not shots:
        problems.append("в сценарии нет ни одного дубля")
        return problems
    if shots[0].get("intent") != INTENT_HOOK:
        problems.append("первый дубль должен быть хуком (intent=hook)")
    if shots[-1].get("intent") != INTENT_CTA:
        problems.append("последний дубль должен быть CTA (intent=cta)")
    for i, sh in enumerate(shots, 1):
        if sh.get("intent") not in INTENTS:
            problems.append("дубль %d: неизвестный intent %r" % (i, sh.get("intent")))
        if not str(sh.get("say", "")).strip():
            problems.append("дубль %d: пустая реплика" % i)
        if not str(sh.get("show", "")).strip():
            problems.append("дубль %d: пусто «что показать»" % i)
        if int(sh.get("duration_sec", 0)) <= 0:
            problems.append("дубль %d: неположительная длительность" % i)
    # Пустое кодовое слово — НЕ поломка схемы. Раньше здесь стояла ошибка, и
    # чтобы её не ловить, модуль подставлял чужое слово («РОСТ») — так дефект
    # BUG-2026-08-08 и держался. Теперь сценарий с пустым словом валиден: в
    # финале стоит явная пометка-задание, а doctor отдельно предупреждает
    # человека. Пустое место эксперт закроет, чужое слово он не заметит.
    return problems
