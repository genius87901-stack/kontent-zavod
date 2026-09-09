# -*- coding: utf-8 -*-
"""
networks — чтение каталога соцсетей для селектора установки.

Каталог живёт в install/networks.json (рядом). Здесь только ЧТЕНИЕ: даём мастеру
/install готовый список сетей с зрелостью и подписями, а по выбранным сетям —
объединение канонических ключей, чтобы отфильтровать вопросы плана только под то,
что клиент реально подключает.

Ничего не публикует, секретов не хранит, манифесты модулей не трогает. Только stdlib.

Запуск:
    python install/networks.py --list                 # русская сводка по зрелости
    python install/networks.py --json                 # машинный каталог
    python install/networks.py --check                 # валидация каталога (замер)
    python install/networks.py --keys instagram,vkontakte
        печатает объединение требуемых КАНОНИЧЕСКИХ ключей (по одному в строке) —
        мастер оставляет из config_questions плана только эти вопросы.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Консоль русской Windows — cp1251, и ЛЮБОЙ значок вне неё (→ ✓ ✗ • ⚠) роняет
# скрипт UnicodeEncodeError прямо в лицо клиенту. Переводим свой вывод в utf-8
# с заменой непечатаемого: ни один текст мастера больше не может убить установку.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # старый питон или подменённый stdout — не критично
    pass


CATALOG_NAME = "networks.json"
_VALID_MATURITY = {"proven", "risk"}


def catalog_path() -> Path:
    return Path(__file__).resolve().parent / CATALOG_NAME


def load(path: Path | None = None) -> dict:
    p = path or catalog_path()
    return json.loads(p.read_text(encoding="utf-8-sig"))


def networks(cat: dict) -> list[dict]:
    return list(cat.get("networks", []) or [])


def norm_ids(raw: str) -> list[str]:
    """'instagram, VKontakte' -> ['instagram','vkontakte'] (без пустых)."""
    out = []
    for part in str(raw).replace(";", ",").split(","):
        pid = part.strip().lower()
        if pid and pid not in out:
            out.append(pid)
    return out


def keys_for(cat: dict, ids: list[str]) -> list[str]:
    """Объединение канонических requires-ключей по выбранным сетям (стабильный порядок)."""
    want = set(ids)
    out: list[str] = []
    for net in networks(cat):
        if str(net.get("id", "")).strip().lower() not in want:
            continue
        for k in net.get("requires", []) or []:
            ck = str(k).strip().lower()
            if ck and ck not in out:
                out.append(ck)
    return out


def label(cat: dict, maturity: str) -> str:
    return (cat.get("maturity_labels", {}) or {}).get(maturity, maturity)


def human_list(cat: dict) -> str:
    lines = ["Какие соцсети можно подключить (пометки честные):", ""]
    for m in ("proven", "risk"):
        group = [n for n in networks(cat) if n.get("maturity") == m]
        if not group:
            continue
        lines.append(f"[{label(cat, m)}]")
        for n in group:
            keys = ", ".join(n.get("requires", []) or []) or "—"
            lines.append(f"  • {n.get('name','?')} (id: {n.get('id','?')}) — ключи: {keys}")
            if n.get("note"):
                lines.append(f"      {n['note']}")
        lines.append("")
    methods = cat.get("connect_methods", {}) or {}
    if methods:
        lines.append("Способы подключения:")
        for mid, desc in methods.items():
            lines.append(f"  • {mid}: {desc}")
    return "\n".join(lines).rstrip()


def check(cat: dict) -> list[str]:
    """Валидация каталога для замера. Возвращает список проблем (пусто = ок)."""
    problems: list[str] = []
    nets = networks(cat)
    if not nets:
        problems.append("В каталоге нет ни одной сети.")
    seen: set[str] = set()
    for n in nets:
        nid = str(n.get("id", "")).strip().lower()
        if not nid:
            problems.append(f"Сеть без id: {n!r}")
            continue
        if nid in seen:
            problems.append(f"Дублирующийся id: {nid}")
        seen.add(nid)
        if not n.get("name"):
            problems.append(f"[{nid}] без человекочитаемого имени.")
        if n.get("maturity") not in _VALID_MATURITY:
            problems.append(f"[{nid}] неизвестная зрелость: {n.get('maturity')!r}")
        if not (n.get("requires") or []):
            problems.append(f"[{nid}] без ключей requires.")
        for k in n.get("requires", []) or []:
            if k != str(k).strip().lower():
                problems.append(f"[{nid}] ключ '{k}' не в каноне lower_snake.")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Чтение каталога соцсетей установки.")
    ap.add_argument("--list", action="store_true", help="Русская сводка по зрелости.")
    ap.add_argument("--json", action="store_true", help="Машинный каталог JSON.")
    ap.add_argument("--check", action="store_true", help="Проверить каталог (замер).")
    ap.add_argument("--keys", metavar="ID,ID", help="Ключи для выбранных сетей (union).")
    ap.add_argument("--path", help="Путь к каталогу (по умолчанию рядом с этим файлом).")
    args = ap.parse_args(argv)

    cat = load(Path(args.path) if args.path else None)

    if args.check:
        problems = check(cat)
        if problems:
            print("ПРОБЛЕМЫ каталога сетей:")
            for p in problems:
                print(f"  • {p}")
            return 2
        print(f"Каталог сетей в порядке: {len(networks(cat))} сет(и/ей).")
        return 0

    if args.keys is not None:
        for k in keys_for(cat, norm_ids(args.keys)):
            print(k)
        return 0

    if args.json:
        print(json.dumps(cat, ensure_ascii=False, indent=2))
        return 0

    # по умолчанию и на --list — человекочитаемая сводка
    print(human_list(cat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
