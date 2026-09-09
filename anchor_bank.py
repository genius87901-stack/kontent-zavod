# -*- coding: utf-8 -*-
"""Банк якорных кейсов клиента — чередование вместо одного кейса.

ЗАЧЕМ. Сценарист завода держит РОВНО ОДИН якорный кейс (`bible.anchor`) и ставит
его в бит «кейс» во все ролики подряд. Владелец (НиколаевскТВ, 09.09.2026)
попросил чередовать все кейсы книги: «чередуй все кейсы из книги смыслов, новые
не выдумывай».

КАК. Кейсы лежат в самой книге смыслов, в секции «# ЯКОРНЫЕ КЕЙСЫ» — она
собрана дословно из банков историй (разделы 33 и 34 книги). Этот модуль её
читает и отдаёт списком. Ротацией занимается сам сценарист своим штатным
механизмом `_pick_from_pool`, поэтому здесь только чтение.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ В КОРНЕ. Файлы коробки перезаписываются обновлением
(проверено 09.09.2026: патч 1.3.65 затёр три правки за сутки). Корневые файлы,
которых нет в поставке, обновление не трогает — значит, после патча возвращать
надо только ОДНУ строку вызова в `writer.py`, а не всю логику.
"""
from __future__ import annotations

import re
from pathlib import Path

#: Заголовок секции в книге смыслов.
_SECTION = re.compile(r"^#{1,3}\s*ЯКОРНЫЕ\s+КЕЙСЫ\s*$", re.IGNORECASE)
#: Пункт списка: «12. Текст кейса».
_ITEM = re.compile(r"^\s*\d{1,3}[.)]\s+(.+?)\s*$")

_cache: dict[str, list] = {}


def _bible_path() -> Path | None:
    """Путь к подключённой книге смыслов — тем же ключом, что и у сценариста."""
    here = Path(__file__).resolve().parent
    try:
        import sys
        sys.path.insert(0, str(here / "install"))
        import factory_config
        name = (factory_config.load() or {}).get("script_bible_file")
        if name:
            p = here / str(name)
            if p.is_file():
                return p
    except Exception:                                     # noqa: BLE001
        pass
    for cand in sorted(here.glob("CONTENT_BIBLE*.md")) + [here / "КНИГА_СМЫСЛОВ.md"]:
        if cand.is_file():
            return cand
    return None


def cases(include_restricted: bool = True) -> list:
    """Список якорных кейсов из книги. Пусто — секции нет, сценарист работает
    по-старому (один кейс или пометка-задание).

    include_restricted=False убирает кейсы с меткой [R] — режим ограниченного
    доступа по разделу 06 книги.
    """
    path = _bible_path()
    if path is None:
        return []
    key = "%s|%s|%s" % (path, path.stat().st_mtime, include_restricted)
    if key in _cache:
        return _cache[key]

    out = []
    inside = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if _SECTION.match(line.strip()):
            inside = True
            continue
        if inside and line.startswith("#"):
            break                                          # секция кончилась
        if not inside:
            continue
        m = _ITEM.match(line)
        if not m:
            continue
        phrase = m.group(1).strip()
        if len(phrase) < 10:
            continue
        if not include_restricted and "[R]" in phrase:
            continue
        out.append(phrase)

    _cache[key] = out
    return out


if __name__ == "__main__":
    all_cases = cases()
    open_cases = cases(include_restricted=False)
    print("кейсов в банке: %d (из них открытых, без [R]: %d)"
          % (len(all_cases), len(open_cases)))
    for c in all_cases[:5]:
        print("   ", c)
    if len(all_cases) > 5:
        print("    ... ещё %d" % (len(all_cases) - 5))
